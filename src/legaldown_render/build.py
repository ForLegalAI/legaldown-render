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
from urllib.parse import unquote

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
# Leads the sentinel of source that renders nothing (a {{def:}}). It is
# Unicode punctuation, so an emphasis closer just before it still counts as
# right-flanking and closes (CommonMark); a bare sentinel reads like a letter.
_HIDDEN_LEAD = "\u2e31"
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
    def __init__(self, source: str, document: Document, result: ValidationResult) -> None:
        self.document = document
        self.result = result
        self.language = document.metadata.language or "en"
        self.md = MarkdownIt("commonmark", {"html": True}).enable("table")
        self.env: dict = {}
        self.payloads: list[_Payload] = []
        self.raw_html = 0
        # Whether the document is a template; decided once the sections are
        # built, before the preamble (see tree()).
        self.template = True
        nonce = secrets.token_hex(8)
        # A hidden sentinel has its own form ("h"), so the lead character is
        # taken only with one, never from the source before another sentinel.
        self.sentinel_re = re.compile(f"(?:{_HIDDEN_LEAD}{_OPEN}{nonce}h|{_OPEN}{nonce}):(\\d+){_CLOSE}")
        # How markdown-it percent-encodes a sentinel inside a URL.
        self.encoded_sentinel_re = re.compile(
            f"(?:%E2%B8%B1%EE%80%80{nonce}h|%EE%80%80{nonce}):(\\d+)%EE%80%81", re.IGNORECASE)
        self.nonce = nonce
        self.body = self._protect(body_of(source))

    # -- sentinels ------------------------------------------------------------

    def _sentinel(self, payload: _Payload) -> str:
        self.payloads.append(payload)
        index = len(self.payloads) - 1
        if isinstance(payload, _Hidden):
            return f"{_HIDDEN_LEAD}{_OPEN}{self.nonce}h:{index}{_CLOSE}"
        return f"{_OPEN}{self.nonce}:{index}{_CLOSE}"

    def _protect(self, body: str) -> str:
        """*body* with every directive replaced by a sentinel. A defined term
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

    def blocks(self, nodes: list[SyntaxTreeNode], *, in_section: bool, top_level: bool,
               in_quote: bool = False, loose: bool = False) -> tuple[Block, ...]:
        out: list[Block] = []
        for node in nodes:
            block = self._block(node, in_section=in_section, top_level=top_level, in_quote=in_quote,
                                loose=loose)
            if block is not None:
                out.append(block)
        return tuple(out)

    def _block(self, node: SyntaxTreeNode, *, in_section: bool, top_level: bool, in_quote: bool,
               loose: bool) -> Block | None:
        match node.type:
            case "paragraph":
                position = "top" if top_level else "loose" if loose else "other"
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
        §15.3 place one, exactly as the validator places it. *position* is:

        * ``top`` — directly in a section or the preamble;
        * ``item`` — a list item's first paragraph, outside block quotes;
        * ``loose`` — a later paragraph of a list item, outside block
          quotes. The validator's model ends the list at the blank line
          before it and reads it as a top-level paragraph
          (ForLegalAI/legaldown-validator#14), so its marker is placed the
          same way; it is not numbered as a top-level paragraph;
        * ``other`` — anywhere else.

        Anywhere a marker is not placed it stays literal text, and the
        validator reports it (anchor-misplaced).
        """
        if position == "other":
            return Paragraph(self.inlines(content))
        text, marker = _split_marker(content)
        if marker != Marker() and position in ("top", "loose") and self._is_include_only(text):
            marker = Marker(condition=marker.condition)  # the #id is ignored (§12.2)
        elif not in_section and (position == "item" or marker.identifier or not self.template):
            # Before the first heading (§4.4, §15.3): no anchors, no
            # conditions on list items, and a paragraph's condition only in
            # a template.
            text, marker = content, Marker()
        return Paragraph(
            self.inlines(text),
            anchor_id=marker.identifier,
            condition=marker.condition,
            top_level=position == "top" and in_section,
        )

    def _is_include_only(self, text: str) -> bool:
        """True for a paragraph holding a single well-formed {{include:}} and
        nothing else but comments — the validator's is_include_only."""
        match = self.sentinel_re.fullmatch(HTML_COMMENT_RE.sub("", text).strip())
        if not match:
            return False
        payload = self.payloads[int(match.group(1))]
        return (isinstance(payload, DirectiveSource) and payload.directive.name == "include"
                and not payload.directive.malformed)

    def list(self, node: SyntaxTreeNode, *, in_section: bool, in_quote: bool) -> List:
        items: list[ListItem] = []
        for item in node.children:
            children = list(item.children)
            first: Paragraph | None = None
            if children and children[0].type == "paragraph" and not in_quote:
                first = self.paragraph(_inline_content(children[0]), position="item", in_section=in_section)
                children = children[1:]
            rest = self.blocks(children, in_section=in_section, top_level=False, in_quote=in_quote,
                               loose=not in_quote)
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
        # Only now is it known whether the document is a template, which
        # decides whether a preamble paragraph's condition applies (§5.7).
        self.template = self._is_template(sections, preamble_nodes)
        metadata = self.document.metadata
        return RenderTree(
            title=plain_inlines(metadata.title),
            subtitle=plain_inlines(metadata.subtitle),
            language=self.language,
            preamble=self.blocks(preamble_nodes, in_section=False, top_level=True),
            sections=tuple(sections),
            is_template=self.template,
        )

    def _is_template(self, sections: list[Section], preamble: list[SyntaxTreeNode]) -> bool:
        """Whether the document is a template, decided as the validator
        decides it: declared questions, a conditional attachment, section,
        or unit, or a {{choose:}}. A preamble paragraph's own condition
        does not count — it applies only once the document is a template."""
        metadata = self.document.metadata
        return (
            metadata.questions is not None
            or any(attachment.when for attachment in metadata.attachments)
            or any(section.condition for section in sections)
            or any(_has_condition(block) for section in sections for block in section.blocks)
            or any(isinstance(payload, DirectiveSource) and payload.directive.name == "choose"
                   for payload in self.payloads)
            # A {{choose:}} in frontmatter is misplaced, but still makes a
            # template (§15.1), over the same fields the validator reads.
            or any(directive.name == "choose" for text in _frontmatter_texts(metadata)
                   for directive in lex(text or "").directives)
            or any(self._conditional_include(node) for node in preamble)
        )

    def _conditional_include(self, node: SyntaxTreeNode) -> bool:
        """True for a preamble paragraph holding only an {{include:}} and a
        condition, whose condition applies in any document (§12.2)."""
        if node.type != "paragraph":
            return False
        text, marker = _split_marker(_inline_content(node))
        return bool(marker.condition) and self._is_include_only(text)

    def _check_outline(self, levels: list[int]) -> None:
        expected = [section.level for section in self.document.sections]
        if levels != expected or len(self.result.sections) != len(expected):
            raise InternalError(
                "the renderer and legaldown-validator disagree about the document's headings "
                f"(renderer: {levels}, validator: {expected}). This is a bug; please report it "
                "with the document that triggers it."
            )


def _frontmatter_texts(metadata) -> list[str]:
    """Every frontmatter text the validator reads for misplaced directives.
    Private to legaldown-validator 0.2 (roadmap U3: to become public API)."""
    from legaldown.validator.core import _frontmatter_fields

    structural, values = _frontmatter_fields(metadata)
    return [text for _label, text in structural] + list(values)


def _has_condition(block: Block) -> bool:
    """True if a placed condition is on *block* or on a unit inside it."""
    match block:
        case Paragraph(condition=condition):
            return bool(condition)
        case List(items=items):
            return any(item.condition or any(_has_condition(child) for child in item.blocks) for item in items)
        case Quote(blocks=blocks) | DraftingNote(blocks=blocks):
            return any(_has_condition(child) for child in blocks)
    return False


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
