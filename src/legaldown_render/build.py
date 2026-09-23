"""Stage 3: build the render tree from the validator's document model
(docs/architecture.md, docs/decisions/0002).

There is one parser: ``legaldown-validator``'s. Its ``Document`` gives the
sections, their blocks, and each block's text, and its own findings say
where markers are placed and whether the document is a template
(:mod:`.validator_bridge`). The builder never decides a structural or
LegalDown question itself.

Within one block's text it still needs inline Markdown — emphasis, links,
code spans — which markdown-it-py parses in inline mode only. Directives
are protected first by **sentinels**: every directive (and every
``"Term" {{def:}}`` span) is replaced by a private-use token,
``\\ue000<nonce>:<n>\\ue001``, so Markdown can never reinterpret directive
syntax, and put back as tree nodes afterwards. The nonce is random for every
build, so no text in a document — written out, as an entity, or
percent-encoded — can pass for a sentinel.

Until the validator keeps nested lists (ForLegalAI/legaldown-validator#14),
lists render as the validator holds them: one level of items.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, replace
from urllib.parse import unquote

from legaldown import Block as ModelBlock
from legaldown import Directive, Document, ValidationResult, find_definition_anchors, parse_document, render_block
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

from .errors import InternalError
from .tree import (
    Block,
    Code,
    CodeBlock,
    DefinitionSource,
    DirectiveSource,
    DraftingNote,
    Emphasis,
    HardBreak,
    Image,
    Inline,
    Link,
    List,
    ListItem,
    Paragraph,
    Quote,
    RenderTree,
    Rule,
    Section,
    SoftBreak,
    Strong,
    Table,
    Text,
)
from .validator_bridge import block_fragments, block_quotes, lex, placed_markers

_OPEN, _CLOSE = "\ue000", "\ue001"
# Leads the sentinel of source that renders nothing (a {{def:}}). It is
# Unicode punctuation, so an emphasis closer just before it still counts as
# right-flanking and closes (CommonMark); a bare sentinel reads like a letter.
_HIDDEN_LEAD = "\u2e31"
# A fenced code block's opening line (validator's model keeps the fences).
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def normalize_source(source: str) -> str:
    """*source* with a byte-order mark removed and line endings unified."""
    return source.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True, slots=True)
class _DefinitionPayload:
    directive: Directive
    #: The term's text between its quotation marks, inner directives
    #: already replaced by sentinels.
    inner: str
    term: str
    #: The source span the sentinel replaced, for restoring the text where
    #: Markdown shows it literally (a code block, a link URL).
    source: str


@dataclass(frozen=True, slots=True)
class _Hidden:
    """Source that renders nothing — a {{def:}} and the spacing before it —
    kept so that literal restoration gives back the source exactly."""

    source: str


_Payload = DirectiveSource | _DefinitionPayload | _Hidden


@dataclass(frozen=True, slots=True)
class _DefinitionSpan:
    """Where a defined term and its {{def:}} lie in the body.

    ``leading`` and ``trailing`` are the emphasis markers written before the
    opening mark and after the closing one. When they wrap just the term,
    they go with it — the style decides how a defined term looks (§7.2);
    otherwise the trailing ones are *kept*, because they pair with text
    beyond the term.
    """

    opening: int  # offset of the opening quotation mark
    closing: int  # offset of the closing quotation mark
    leading: str  # emphasis markers just before the opening mark
    trailing: str  # emphasis markers between the closing mark and the spacing
    term: str

    @property
    def wraps(self) -> bool:
        """True when the markers open before the term and close right after
        it, as in ``**"Term"**``."""
        return bool(self.leading) and self.trailing == self.leading[::-1]

    @property
    def start(self) -> int:
        """Start of the term's sentinel."""
        return self.opening - len(self.leading) if self.wraps else self.opening

    @property
    def end(self) -> int:
        """End of the term's sentinel."""
        return self.hidden if self.wraps else self.closing + 1

    @property
    def kept(self) -> str:
        return "" if self.wraps else self.trailing

    @property
    def hidden(self) -> int:
        """Start of the spacing and {{def:}}, which render nothing."""
        return self.closing + 1 + len(self.trailing)


class _Builder:
    def __init__(self, document: Document, result: ValidationResult) -> None:
        self.document = document
        self.result = result
        self.language = document.metadata.language or "en"
        # Inline parsing only: block structure is the validator's.
        self.md = MarkdownIt("commonmark", {"html": True})
        self.env: dict = {}
        self.payloads: list[_Payload] = []
        self.raw_html = 0
        self.markers = placed_markers(document)
        nonce = secrets.token_hex(8)
        # A hidden sentinel has its own form ("h"), so the lead character is
        # taken only with one, never from the source before another sentinel.
        self.sentinel_re = re.compile(f"(?:{_HIDDEN_LEAD}{_OPEN}{nonce}h|{_OPEN}{nonce}):(\\d+){_CLOSE}")
        # How markdown-it percent-encodes a sentinel inside a URL.
        self.encoded_sentinel_re = re.compile(
            f"(?:%E2%B8%B1%EE%80%80{nonce}h|%EE%80%80{nonce}):(\\d+)%EE%80%81", re.IGNORECASE)
        self.nonce = nonce

    # -- sentinels ------------------------------------------------------------

    def _sentinel(self, payload: _Payload) -> str:
        self.payloads.append(payload)
        index = len(self.payloads) - 1
        if isinstance(payload, _Hidden):
            return f"{_HIDDEN_LEAD}{_OPEN}{self.nonce}h:{index}{_CLOSE}"
        return f"{_OPEN}{self.nonce}:{index}{_CLOSE}"

    def _protect(self, body: str) -> str:
        """*body* — one block's text — with every directive replaced by a
        sentinel, so that Markdown cannot reinterpret directive syntax. A defined term
        becomes one sentinel and its {{def:}} another that renders nothing
        (see _DefinitionSpan). One pass over the directives, in order."""
        lexed = lex(body)
        spans: dict[int, _DefinitionSpan] = {}
        for anchor in find_definition_anchors(body, language=self.language, lexed=lexed):
            if anchor.term is None or anchor.pair is None:
                continue  # a bare {{def:}}: an Error the validator reports; it renders nothing
            opening = body.index(anchor.pair[0], anchor.start)
            closing = body.rindex(anchor.pair[1], opening + 1, anchor.directive.start)
            # Between the closing mark and the directive the validator allows
            # only emphasis markers, then spacing (§7.2).
            trailing = body[closing + 1:anchor.directive.start].rstrip()
            spans[id(anchor.directive)] = _DefinitionSpan(
                opening, closing, body[anchor.start:opening], trailing, anchor.term)

        out: list[str] = []
        cursor = 0
        pending: list[Directive] = []  # directives inside the next defined term
        starts = sorted((span.start, span.end) for span in spans.values())
        next_span = 0
        for directive in lexed.directives:
            span = spans.get(id(directive))
            if span is None:
                while next_span < len(starts) and starts[next_span][1] <= directive.start:
                    next_span += 1
                if next_span < len(starts) and starts[next_span][0] <= directive.start:
                    pending.append(directive)  # inside a defined term: protected with it
                    continue
                out.append(body[cursor:directive.start])
                out.append(self._sentinel(DirectiveSource(directive)))
            else:
                inner = self._protect_inner(body, span.opening + 1, span.closing, pending)
                pending = []
                out.append(body[cursor:span.start])
                out.append(self._sentinel(_DefinitionPayload(directive, inner, span.term, body[span.start:span.end])))
                out.append(span.kept)
                out.append(self._sentinel(_Hidden(body[span.hidden:directive.end])))
            cursor = directive.end
        out.append(body[cursor:])
        return "".join(out)

    def _protect_inner(self, body: str, start: int, end: int, directives: list[Directive]) -> str:
        """The term text ``body[start:end]`` with *directives*, the ones
        inside it, replaced."""
        out: list[str] = []
        cursor = start
        for directive in directives:
            if start <= directive.start and directive.end <= end:
                out.append(body[cursor:directive.start])
                out.append(self._sentinel(DirectiveSource(directive)))
                cursor = directive.end
        out.append(body[cursor:end])
        return "".join(out).strip()

    def _restore(self, text: str) -> str:
        """*text* with sentinels turned back into their source text, for
        places Markdown shows literally: code, URLs, alt text."""
        def source(match: re.Match[str]) -> str:
            payload = self.payloads[int(match.group(1))]
            return payload.directive.source if isinstance(payload, DirectiveSource) else payload.source
        return self.sentinel_re.sub(source, text)

    def _restore_url(self, url: str) -> str:
        """A link or image URL with its sentinels restored. markdown-it has
        percent-encoded them, so they are decoded first and the URL is
        normalised again afterwards."""
        decoded = self.encoded_sentinel_re.sub(lambda match: unquote(match.group(0)), url)
        return self.md.normalizeLink(self._restore(decoded)) if decoded != url else url

    # -- inlines --------------------------------------------------------------

    def text(self, source: str) -> tuple[Inline, ...]:
        """Inline nodes for *source*, one block's text as the validator
        holds it: directives protected, then parsed as inline Markdown."""
        return self.inlines(self._protect(source))

    def inlines(self, content: str) -> tuple[Inline, ...]:
        """Inline nodes for the inline Markdown *content* (sentinels included)."""
        tokens = self.md.parseInline(content, self.env)
        if not tokens or not tokens[0].children:
            return ()
        root = SyntaxTreeNode(tokens[0].children, create_root=True)
        return self._inline_children(root)

    def _inline_children(self, node: SyntaxTreeNode) -> tuple[Inline, ...]:
        out: list[Inline] = []
        for child in node.children:
            out.extend(self._inline(child))
        return _merge_text(out)

    def _inline(self, node: SyntaxTreeNode) -> list[Inline]:
        match node.type:
            case "text":
                return self._split_sentinels(node.content)
            case "softbreak":
                return [SoftBreak()]
            case "hardbreak":
                return [HardBreak()]
            case "em":
                return [Emphasis(self._inline_children(node))]
            case "strong":
                return [Strong(self._inline_children(node))]
            case "code_inline":
                return [Code(self._restore(node.content))]
            case "link":
                href = self._restore_url(str(node.attrs.get("href", "")))
                return [Link(href, self._inline_children(node), self._title(node))]
            case "image":
                src = self._restore_url(str(node.attrs.get("src", "")))
                return [Image(src, self._inline_children(node), self._title(node))]
            case "html_inline":
                if not node.content.startswith("<!--"):
                    self.raw_html += 1  # never emitted (§8.7)
                return []
            case _:
                return [Text(self._restore(node.content))] if node.content else []

    def _title(self, node: SyntaxTreeNode) -> tuple[Inline, ...]:
        """A link or image title: plain text in which directives resolve."""
        return _merge_text(self._split_sentinels(str(node.attrs.get("title", "") or "")))

    def _split_sentinels(self, text: str) -> list[Inline]:
        out: list[Inline] = []
        cursor = 0
        for match in self.sentinel_re.finditer(text):
            payload = self.payloads[int(match.group(1))]
            if match.start() > cursor:
                out.append(Text(text[cursor:match.start()]))
            if isinstance(payload, _DefinitionPayload):
                out.append(DefinitionSource(payload.directive, self.inlines(payload.inner), payload.term))
            elif isinstance(payload, _Hidden):
                pass
            else:
                out.append(payload)
            cursor = match.end()
        if cursor < len(text):
            out.append(Text(text[cursor:]))
        return out

    # -- blocks ---------------------------------------------------------------

    def blocks(self, blocks: list[ModelBlock], section: int | None, *, markers: bool = True) -> tuple[Block, ...]:
        """Render-tree blocks for the validator's *blocks* of section index
        *section* (None for the preamble). With *markers* False — text the
        validator reads inside a quote or an item — no marker is placed."""
        out: list[Block] = []
        for index, block in enumerate(blocks):
            placed = self._placed(block, section, index) if markers else {}
            built = self.block(block, placed, top_level=section is not None and markers)
            if built is not None:
                out.append(built)
        return tuple(out)

    def _placed(self, block: ModelBlock, section: int | None, index: int) -> dict[int, tuple[int, str, str]]:
        """The markers the validator placed in *block*, by fragment index:
        (offset of the marker in the fragment, identifier, condition)."""
        placed: dict[int, tuple[int, str, str]] = {}
        fragments = block_fragments(block)
        for fragment_index, (fragment, _position) in enumerate(fragments):
            found = self.markers.placed.get((section, index, fragment_index))
            if found is None:
                continue
            first_line = fragment.split("\n", 1)[0]
            offset = first_line.rfind(found.source)
            if offset < 0:
                raise InternalError(f"the validator placed '{found.source}' where the renderer cannot find it")
            identifier = "" if found.include_only else found.marker.identifier
            placed[fragment_index] = (offset, identifier, found.marker.condition)
        return placed

    def block(self, block: ModelBlock, placed: dict[int, tuple[int, str, str]], *, top_level: bool) -> Block | None:
        match block.kind:
            case "paragraph" | "definition" | "ref" | "term":
                block, identifier, condition = _strip_markers(block, placed)
                inlines = self.text(_paragraph_source(block))
                if not inlines and not identifier:
                    return None  # only a comment
                return Paragraph(inlines, anchor_id=identifier, condition=condition, top_level=top_level)
            case "unordered_list" | "ordered_list":
                return self.list(block, placed)
            case "quote":
                return self.quote(block)
            case "code":
                return _code_block(block.text)
            case "table":
                return Table(
                    header=tuple(self.text(cell) for cell in block.headers),
                    rows=tuple(tuple(self.text(cell) for cell in row) for row in block.rows),
                    align=(),
                )
            case "rule":
                return Rule()
        raise InternalError(f"unexpected block kind '{block.kind}' in the validator's model")

    def list(self, block: ModelBlock, placed: dict[int, tuple[int, str, str]]) -> List:
        # Items are fragments after any text, prefix, and suffix, which a
        # list block does not have.
        items: list[ListItem] = []
        for index, item in enumerate(item for item in block.items if item):
            identifier = condition = ""
            if index in placed:
                offset, identifier, condition = placed[index]
                item = _cut_marker(item, offset)
            first, _, rest = item.partition("\n")
            blocks: tuple[Block, ...] = (Paragraph(self.text(first)),)
            if rest:
                # An item keeps a fenced code block's lines (the validator's
                # model); the validator's parser reads them.
                blocks += self.blocks(parse_document(rest).preamble, None, markers=False)
            items.append(ListItem(blocks=blocks, anchor_id=identifier, condition=condition))
        return List(ordered=block.kind == "ordered_list", items=tuple(items))

    def quote(self, block: ModelBlock) -> Block:
        """A block quote, its content read by the validator's parser. A
        drafting note is decided by the validator's own test (§15.6)."""
        text = block.text
        quotes = block_quotes(block)
        drafting = bool(quotes) and quotes[0].start == 0 and quotes[0].is_drafting_note
        if drafting:
            text = text.partition("\n")[2]
        inner = parse_document(text)
        blocks = self.blocks(inner.preamble, None, markers=False)
        for section in inner.sections:
            # A heading inside a quote is not a section (§4.1).
            blocks += (Paragraph((Strong(self.text(section.title)),)),)
            blocks += self.blocks(section.blocks, None, markers=False)
        return DraftingNote(blocks) if drafting else Quote(blocks)

    # -- document -------------------------------------------------------------

    def tree(self) -> RenderTree:
        document = self.document
        sections = tuple(
            Section(
                level=section.level,
                title=self.text(section.title),
                identifier=self.result.sections[index].identifier,
                condition=section.condition,
                blocks=self.blocks(section.blocks, index),
            )
            for index, section in enumerate(document.sections)
        )
        metadata = document.metadata
        return RenderTree(
            title=plain_inlines(metadata.title),
            subtitle=plain_inlines(metadata.subtitle),
            language=self.language,
            preamble=self.blocks(document.preamble, None),
            sections=sections,
            is_template=self.markers.template,
        )


def _strip_markers(block: ModelBlock, placed: dict[int, tuple[int, str, str]]) -> tuple[ModelBlock, str, str]:
    """*block* without its placed marker, and the marker's identifier and
    condition. Fragments are numbered as block_fragments numbers them."""
    fields = [name for name in ("text", "prefix", "suffix") if getattr(block, name)]
    identifier = condition = ""
    for fragment_index, (offset, marker_id, marker_condition) in placed.items():
        if fragment_index >= len(fields):
            raise InternalError(f"the validator placed a marker in fragment {fragment_index} of a {block.kind} block")
        name = fields[fragment_index]
        block = replace(block, **{name: _cut_marker(getattr(block, name), offset)})
        identifier, condition = marker_id, marker_condition
    return block, identifier, condition


def _cut_marker(fragment: str, offset: int) -> str:
    """*fragment* without the marker at *offset* on its first line; a
    comment after the marker stays (it renders nothing)."""
    first, newline, rest = fragment.partition("\n")
    end = first.index("}", offset) + 1
    return (first[:offset].rstrip() + " " + first[end:].lstrip()).strip() + newline + rest


def _paragraph_source(block: ModelBlock) -> str:
    """A paragraph block's text as source. A definition, {{ref:}}, or
    {{term:}} the parser lifted into fields is written back by the
    validator's own serializer."""
    return block.text if block.kind == "paragraph" else render_block(block)


def _code_block(text: str) -> CodeBlock:
    """A fenced code block from the validator's model, fences included."""
    lines = text.split("\n")
    opening = _FENCE_RE.match(lines[0]) if lines else None
    if opening is None:
        return CodeBlock(text)
    fence = opening.group(1)
    body = lines[1:]
    if body and body[-1].strip().startswith(fence[0] * len(fence)) and not body[-1].strip().strip(fence[0]):
        body = body[:-1]
    return CodeBlock("\n".join(body) + ("\n" if body else ""), opening.group(2).strip())


def _merge_text(inlines: list[Inline]) -> tuple[Inline, ...]:
    merged: list[Inline] = []
    for inline in inlines:
        if isinstance(inline, Text) and merged and isinstance(merged[-1], Text):
            merged[-1] = Text(merged[-1].text + inline.text)
        else:
            merged.append(inline)
    return tuple(merged)


def build_tree(document: Document, result: ValidationResult) -> tuple[RenderTree, int]:
    """The render tree for *document*, and the number of raw HTML
    constructs that were dropped (§8.7)."""
    builder = _Builder(document, result)
    tree = builder.tree()
    return tree, builder.raw_html


def plain_inlines(text: str) -> tuple[Inline, ...]:
    """Inline nodes for a frontmatter string: plain text in which only
    directives are recognised (placeholders, §3.10) — never Markdown."""
    out: list[Inline] = []
    cursor = 0
    for directive in lex(text).directives:
        if directive.start > cursor:
            out.append(Text(text[cursor:directive.start]))
        out.append(DirectiveSource(directive))
        cursor = directive.end
    if cursor < len(text):
        out.append(Text(text[cursor:]))
    return tuple(out)
