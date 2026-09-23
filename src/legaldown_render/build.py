"""Stage 3: build the render tree from the source (docs/architecture.md).

The LegalDown layer comes only from ``legaldown-validator``
(docs/decisions/0002): the directive lexer, defined-term anchors, heading and
paragraph markers, and the section identifiers the validator resolved.
CommonMark structure — nested lists, block quotes, tables, emphasis, links —
comes from markdown-it-py, because the validator's model does not keep it yet
(ForLegalAI/legaldown-validator#14).

The two meet through **sentinels**. Before markdown-it sees the body, every
directive (and every ``"Term" {{def:}}`` span) is replaced by a private-use
token, ``\\ue000<nonce>:<n>\\ue001``, so Markdown can never reinterpret
directive syntax — an underscore in a party name, a pipe in a table cell. Text
nodes are split on the sentinels afterwards and the directives put back as
tree nodes. The nonce is random for every build, so no text in a document —
written out, as an entity, or percent-encoded — can pass for a sentinel.

The builder then checks that its headings match the validator's sections one
to one. A mismatch means the two parsers disagree about the document, which
must never be papered over: it raises :class:`InternalError`.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from legaldown import Directive, Document, ValidationResult, find_definition_anchors
from legaldown.directives import lex
from legaldown.markers import HTML_COMMENT_RE, Marker, split_heading
from legaldown.parser import FRONTMATTER_RE
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

_OPEN, _CLOSE = "\ue000", "\ue001"
_DRAFTING_RE = re.compile(r"^\[!drafting\][ \t]*$", re.IGNORECASE)
_COMMENT_ONLY_RE = re.compile(r"^\s*(?:<!--.*?-->\s*)+$", re.DOTALL)
_ALIGN_RE = re.compile(r"text-align:\s*(left|center|right)")


def normalize_source(source: str) -> str:
    """*source* with a byte-order mark removed and line endings unified,
    exactly as it is handed to both parsers."""
    return source.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def body_of(source: str) -> str:
    """The body of *source*: everything after the frontmatter."""
    match = FRONTMATTER_RE.match(source)
    return source[match.end():] if match else source


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
    """Where a defined term and its {{def:}} lie in the body (offsets)."""

    start: int  # of the term's sentinel: its opening mark, or emphasis wrapping it
    opening: int  # the opening quotation mark
    closing: int  # the closing quotation mark
    end: int  # end of the term's sentinel
    kept: str  # emphasis markers after the term that pair with text beyond it
    hidden: int  # start of the spacing and {{def:}} that render nothing
    term: str


class _Builder:
    def __init__(self, source: str, document: Document, result: ValidationResult) -> None:
        self.document = document
        self.result = result
        self.language = document.metadata.language or "en"
        self.md = MarkdownIt("commonmark", {"html": True}).enable("table")
        self.env: dict = {}
        self.payloads: list[_Payload] = []
        self.raw_html = 0
        nonce = secrets.token_hex(8)
        self.sentinel_re = re.compile(f"{_OPEN}{nonce}:(\\d+){_CLOSE}")
        # How markdown-it percent-encodes a sentinel inside a URL.
        self.encoded_sentinel_re = re.compile(f"%EE%80%80{nonce}:(\\d+)%EE%80%81", re.IGNORECASE)
        self.nonce = nonce
        self.body = self._protect(body_of(source))

    # -- sentinels ------------------------------------------------------------

    def _sentinel(self, payload: _Payload) -> str:
        self.payloads.append(payload)
        return f"{_OPEN}{self.nonce}:{len(self.payloads) - 1}{_CLOSE}"

    def _protect(self, body: str) -> str:
        """*body* with every directive replaced by a sentinel."""
        lexed = lex(body)
        # A defined term becomes one sentinel, its {{def:}} another that
        # renders nothing. Emphasis markers that wrap just the term go with
        # it — the style decides how a defined term looks (§7.2) — while
        # markers that pair with text beyond the term stay in place.
        definitions = {}
        for anchor in find_definition_anchors(body, language=self.language, lexed=lexed):
            if anchor.term is None or anchor.pair is None:
                continue  # a bare {{def:}}: an Error the validator reports; it renders nothing
            directive = anchor.directive
            opening = body.index(anchor.pair[0], anchor.start)
            closing = body.rindex(anchor.pair[1], opening + 1, directive.start)
            leading = body[anchor.start:opening]
            # Between the closing mark and the directive the validator allows
            # only emphasis markers, then spacing (§7.2).
            trailing = body[closing + 1:directive.start].rstrip()
            wraps_term = bool(leading) and trailing == leading[::-1]
            definitions[id(directive)] = _DefinitionSpan(
                start=anchor.start if wraps_term else opening,
                opening=opening,
                closing=closing,
                end=closing + 1 + (len(trailing) if wraps_term else 0),
                kept="" if wraps_term else trailing,
                hidden=closing + 1 + len(trailing),
                term=anchor.term,
            )
        covered = [(span.start, directive.end) for directive in lexed.directives
                   if (span := definitions.get(id(directive)))]

        out: list[str] = []
        cursor = 0
        for directive in lexed.directives:
            span = definitions.get(id(directive))
            if span is not None:
                inner = self._protect_inner(body, span.opening + 1, span.closing, lexed.directives)
                out.append(body[cursor:span.start])
                out.append(self._sentinel(_DefinitionPayload(directive, inner, span.term, body[span.start:span.end])))
                out.append(span.kept)
                out.append(self._sentinel(_Hidden(body[span.hidden:directive.end])))
            elif any(start <= directive.start and directive.end <= end for start, end in covered):
                continue  # inside a defined term: protected with the term
            else:
                out.append(body[cursor:directive.start])
                out.append(self._sentinel(DirectiveSource(directive)))
            cursor = directive.end
        out.append(body[cursor:])
        return "".join(out)

    def _protect_inner(self, body: str, start: int, end: int, directives: list[Directive]) -> str:
        """The term text ``body[start:end]`` with its own directives replaced."""
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
        decoded = self.encoded_sentinel_re.sub(lambda match: f"{_OPEN}{self.nonce}:{match.group(1)}{_CLOSE}", url)
        return self.md.normalizeLink(self._restore(decoded)) if decoded != url else url

    # -- inlines --------------------------------------------------------------

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
            if match.start() > cursor:
                out.append(Text(text[cursor:match.start()]))
            payload = self.payloads[int(match.group(1))]
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

    def blocks(self, nodes: list[SyntaxTreeNode], *, in_section: bool, top_level: bool,
               in_quote: bool = False) -> tuple[Block, ...]:
        out: list[Block] = []
        for node in nodes:
            block = self._block(node, in_section=in_section, top_level=top_level, in_quote=in_quote)
            if block is not None:
                out.append(block)
        return tuple(out)

    def _block(self, node: SyntaxTreeNode, *, in_section: bool, top_level: bool, in_quote: bool) -> Block | None:
        match node.type:
            case "paragraph":
                position = "top" if top_level else "other"
                return self.paragraph(_inline_content(node), position=position, in_section=in_section)
            case "bullet_list" | "ordered_list":
                return self.list(node, in_section=in_section, in_quote=in_quote)
            case "blockquote":
                return self.quote(node, in_section=in_section)
            case "fence" | "code_block":
                return CodeBlock(self._restore(node.content), node.info.strip() if node.type == "fence" else "")
            case "hr":
                return Rule()
            case "table":
                return self.table(node)
            case "html_block":
                if not _COMMENT_ONLY_RE.match(node.content):
                    self.raw_html += 1
                return None
            case "heading":
                # A heading inside a block quote or list item is not a
                # section (§4.1); it keeps its emphasis as a paragraph.
                return Paragraph((Strong(self.inlines(_inline_content(node))),))
        raise InternalError(f"unexpected Markdown block '{node.type}'")

    def paragraph(self, content: str, *, position: str, in_section: bool) -> Paragraph:
        """A paragraph, with its trailing marker taken off where §5.7 and
        §15.3 allow one: *position* is ``top`` for a paragraph directly in a
        section or the preamble, ``item`` for a list item's first paragraph
        (outside block quotes), and ``other`` for anywhere else."""
        text, marker = (content, Marker()) if position == "other" else _split_marker(content)
        if marker.identifier and not in_section:
            # Before the first heading a marker may hold only a condition
            # (§4.4, §15.3); with an #id it is literal text (anchor-misplaced).
            if not self._is_include_only(text):
                text, marker = content, Marker()
            else:
                marker = Marker(condition=marker.condition)
        if marker.identifier and self._is_include_only(text):
            marker = Marker(condition=marker.condition)  # ignored (§12.2)
        return Paragraph(
            self.inlines(text),
            anchor_id=marker.identifier,
            condition=marker.condition,
            top_level=position == "top" and in_section,
        )

    def _is_include_only(self, text: str) -> bool:
        match = self.sentinel_re.fullmatch(HTML_COMMENT_RE.sub("", text).strip())
        if not match:
            return False
        payload = self.payloads[int(match.group(1))]
        return isinstance(payload, DirectiveSource) and payload.directive.name == "include"

    def list(self, node: SyntaxTreeNode, *, in_section: bool, in_quote: bool) -> List:
        items: list[ListItem] = []
        for item in node.children:
            children = list(item.children)
            first: Paragraph | None = None
            if children and children[0].type == "paragraph" and not in_quote:
                first = self.paragraph(_inline_content(children[0]), position="item", in_section=in_section)
                children = children[1:]
            rest = self.blocks(children, in_section=in_section, top_level=False, in_quote=in_quote)
            # The marker belongs to the item, not to its first paragraph.
            blocks = rest if first is None else (Paragraph(first.inlines),) + rest
            items.append(ListItem(
                blocks=blocks,
                anchor_id=first.anchor_id if first else "",
                condition=first.condition if first else "",
            ))
        return List(ordered=node.type == "ordered_list", items=tuple(items))

    def quote(self, node: SyntaxTreeNode, *, in_section: bool) -> Block:
        children = list(node.children)
        if children and children[0].type == "paragraph":
            first_line, _, rest = _inline_content(children[0]).partition("\n")
            if _DRAFTING_RE.match(first_line.strip()):
                blocks = self.blocks(children[1:], in_section=in_section, top_level=False, in_quote=True)
                if rest.strip():
                    blocks = (Paragraph(self.inlines(rest)),) + blocks
                return DraftingNote(blocks)
        return Quote(self.blocks(children, in_section=in_section, top_level=False, in_quote=True))

    def table(self, node: SyntaxTreeNode) -> Table:
        header: tuple = ()
        rows: list = []
        align: tuple[str, ...] = ()
        for part in node.children:
            for row in part.children:
                cells = tuple(self.inlines(_inline_content(cell)) for cell in row.children)
                if part.type == "thead":
                    header = cells
                    align = tuple(_cell_align(cell) for cell in row.children)
                else:
                    rows.append(cells)
        return Table(header, tuple(rows), align)

    # -- document -------------------------------------------------------------

    def tree(self) -> RenderTree:
        root = SyntaxTreeNode(self.md.parse(self.body, self.env))
        preamble_nodes: list[SyntaxTreeNode] = []
        headings: list[tuple[SyntaxTreeNode, list[SyntaxTreeNode]]] = []
        for node in root.children:
            if node.type == "heading":
                headings.append((node, []))
            elif headings:
                headings[-1][1].append(node)
            else:
                preamble_nodes.append(node)
        self._check_outline([int(node.tag[1]) for node, _ in headings])

        sections: list[Section] = []
        for index, (heading, nodes) in enumerate(headings):
            title_text, marker = split_heading(_inline_content(heading))
            title = self.inlines(title_text)
            sections.append(Section(
                level=int(heading.tag[1]),
                title=title,
                identifier=self.result.sections[index].identifier,
                condition=marker.condition,
                blocks=self.blocks(nodes, in_section=True, top_level=True),
            ))
        metadata = self.document.metadata
        return RenderTree(
            title=plain_inlines(metadata.title),
            subtitle=plain_inlines(metadata.subtitle),
            language=self.language,
            preamble=self.blocks(preamble_nodes, in_section=False, top_level=True),
            sections=tuple(sections),
            is_template=metadata.questions is not None,
        )

    def _check_outline(self, levels: list[int]) -> None:
        expected = [section.level for section in self.document.sections]
        if levels != expected or len(self.result.sections) != len(expected):
            raise InternalError(
                "the renderer and legaldown-validator disagree about the document's headings "
                f"(renderer: {levels}, validator: {expected}). This is a bug; please report it "
                "with the document that triggers it."
            )


def _inline_content(node: SyntaxTreeNode) -> str:
    """The raw inline source of a paragraph, heading, or table cell."""
    for child in node.children:
        if child.type == "inline":
            return child.content
    return ""


def _split_marker(content: str) -> tuple[str, Marker]:
    """*content* without a trailing marker, and the marker. Only the last
    line can carry one."""
    head, newline, last = content.rpartition("\n")
    text, marker = split_heading(last)
    if marker == Marker():
        return content, marker
    return head + newline + text, marker


def _cell_align(cell: SyntaxTreeNode) -> str:
    match = _ALIGN_RE.search(str(cell.attrs.get("style", "")))
    return match.group(1) if match else ""


def _merge_text(inlines: list[Inline]) -> tuple[Inline, ...]:
    merged: list[Inline] = []
    for inline in inlines:
        if isinstance(inline, Text) and merged and isinstance(merged[-1], Text):
            merged[-1] = Text(merged[-1].text + inline.text)
        else:
            merged.append(inline)
    return tuple(merged)


def build_tree(source: str, document: Document, result: ValidationResult) -> tuple[RenderTree, int]:
    """The render tree for *source* (already normalised), and the number of
    raw HTML constructs that were dropped (§8.7)."""
    builder = _Builder(source, document, result)
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
