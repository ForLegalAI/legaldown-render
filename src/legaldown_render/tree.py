"""The render tree: a format-neutral tree of blocks and inlines.

The build stage (:mod:`.build`) produces the tree with **source** inline
nodes — :class:`DirectiveSource` and :class:`DefinitionSource` — that still
carry LegalDown semantics. The resolve stage (:mod:`.resolve`) replaces every
one of them with a **resolved** node that holds only display text and link
targets, and fills in numbers, labels, and anchors. Writers accept only the
resolved tree (docs/decisions/0003): :func:`assert_resolved` rejects a tree
that still holds a source node.

All nodes are frozen; stages build new trees rather than mutate.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace

from legaldown import Directive

from .errors import InternalError

# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Text:
    text: str


@dataclass(frozen=True, slots=True)
class SoftBreak:
    """A line break in the source that is not a hard break."""


@dataclass(frozen=True, slots=True)
class HardBreak:
    pass


@dataclass(frozen=True, slots=True)
class Emphasis:
    children: tuple[Inline, ...]


@dataclass(frozen=True, slots=True)
class Strong:
    children: tuple[Inline, ...]


@dataclass(frozen=True, slots=True)
class Code:
    text: str


@dataclass(frozen=True, slots=True)
class Link:
    href: str
    children: tuple[Inline, ...]
    #: The link's title, which may hold directives like any other text.
    title: tuple[Inline, ...] = ()


@dataclass(frozen=True, slots=True)
class Image:
    src: str
    #: The alt text, which may hold directives like any other text.
    children: tuple[Inline, ...]
    title: tuple[Inline, ...] = ()


# Source inlines (build stage only) -----------------------------------------


@dataclass(frozen=True, slots=True)
class DirectiveSource:
    """A directive as lexed by ``legaldown.directives``, not yet resolved."""

    directive: Directive


@dataclass(frozen=True, slots=True)
class DefinitionSource:
    """``"Term" {{def: id}}``: the quoted term and its anchor (§7.2). The
    term's own inline content is in ``children``; the quotation marks are
    source-only and are gone."""

    directive: Directive
    children: tuple[Inline, ...]
    term: str


# Resolved inlines -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CrossRef:
    """A resolved ``{{ref:}}``: display text and the output anchor it links to."""

    text: str
    target: str


@dataclass(frozen=True, slots=True)
class TermRef:
    """A resolved ``{{term:}}``. ``target`` is ``None`` when the definition is
    known but not in this document's body (e.g. imported by an amendment)."""

    text: str
    target: str | None
    style: str


@dataclass(frozen=True, slots=True)
class DefinedTerm:
    """The defining occurrence of a term, with its anchor."""

    children: tuple[Inline, ...]
    anchor: str | None
    style: str


@dataclass(frozen=True, slots=True)
class Value:
    """A formatted field spec or name: ``kind`` is the directive name
    (date, money, duration, field, party, side, attach)."""

    kind: str
    text: str
    href: str | None = None


@dataclass(frozen=True, slots=True)
class Blank:
    """A fillable placeholder (§10.7, §13.5)."""

    id: str
    text: str
    prompt: str = ""


@dataclass(frozen=True, slots=True)
class Choice:
    """A ``{{choose:}}`` in the template view (§15.8): every phrase, in the
    order written."""

    question: str
    options: tuple[str, ...]
    separator: str


@dataclass(frozen=True, slots=True)
class FailureMarker:
    """A bracketed failure marker such as ``[BROKEN REF: id]`` (§11.5, §13)."""

    text: str


Inline = (
    Text | SoftBreak | HardBreak | Emphasis | Strong | Code | Link | Image
    | DirectiveSource | DefinitionSource
    | CrossRef | TermRef | DefinedTerm | Value | Blank | Choice | FailureMarker
)

SOURCE_INLINES = (DirectiveSource, DefinitionSource)

# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Paragraph:
    inlines: tuple[Inline, ...]
    #: From the paragraph's marker (§5.7, §15.3), as written.
    anchor_id: str = ""
    condition: str = ""
    #: True for a top-level paragraph in a section (numbered when the style
    #: numbers paragraphs).
    top_level: bool = False
    # Filled in by the resolve stage:
    label: str | None = None
    anchor: str | None = None
    condition_label: str | None = None


@dataclass(frozen=True, slots=True)
class ListItem:
    blocks: tuple[Block, ...]
    anchor_id: str = ""
    condition: str = ""
    label: str | None = None
    anchor: str | None = None
    condition_label: str | None = None


@dataclass(frozen=True, slots=True)
class List:
    ordered: bool
    items: tuple[ListItem, ...]
    #: Set by the resolve stage: whether items carry generated labels.
    enumerated: bool = False


@dataclass(frozen=True, slots=True)
class Quote:
    blocks: tuple[Block, ...]


@dataclass(frozen=True, slots=True)
class DraftingNote:
    """A ``> [!DRAFTING]`` block quote (§15.6)."""

    blocks: tuple[Block, ...]
    label: str | None = None


@dataclass(frozen=True, slots=True)
class CodeBlock:
    text: str
    info: str = ""


@dataclass(frozen=True, slots=True)
class Table:
    header: tuple[tuple[Inline, ...], ...]
    rows: tuple[tuple[tuple[Inline, ...], ...], ...]
    #: Per column: "left", "center", "right", or "".
    align: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Rule:
    pass


Block = Paragraph | List | Quote | DraftingNote | CodeBlock | Table | Rule

# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Section:
    """One heading and the blocks up to the next heading. Sections are kept
    flat, in document order; ``level`` gives the hierarchy."""

    level: int
    title: tuple[Inline, ...]
    #: The identifier the validator resolved (explicit, or generated by §5.3).
    identifier: str
    condition: str
    blocks: tuple[Block, ...]
    # Filled in by the resolve stage:
    label: str | None = None
    designation: str | None = None
    anchor: str | None = None
    condition_label: str | None = None
    #: How deep its number goes: 1 for "2", 2 for "2.1". Less than
    #: ``level`` when a heading level is skipped.
    depth: int = 0


@dataclass(frozen=True, slots=True)
class PartyInfo:
    name: tuple[Inline, ...]
    #: (label, value) rows: identification, address, custom fields, and
    #: one row per representative.
    details: tuple[tuple[str, tuple[Inline, ...]], ...]


@dataclass(frozen=True, slots=True)
class SideInfo:
    label: tuple[Inline, ...]
    parties: tuple[PartyInfo, ...]


@dataclass(frozen=True, slots=True)
class AttachmentPart:
    id: str
    title: tuple[Inline, ...]
    file: str
    anchor: str | None
    condition_label: str | None = None


@dataclass(frozen=True, slots=True)
class SignatureParty:
    side: tuple[Inline, ...]
    name: tuple[Inline, ...]
    #: (name, title) per representative; empty when none are declared.
    signatories: tuple[tuple[tuple[Inline, ...], tuple[Inline, ...]], ...]


@dataclass(frozen=True, slots=True)
class ContentsEntry:
    """One line of the table of contents: a section, the attachments
    heading, or an attachment. Its title reads as in the body, without the
    body's links and anchors (see :func:`unlinked`)."""

    level: int
    label: str | None
    title: tuple[Inline, ...]
    anchor: str | None
    condition_label: str | None = None


@dataclass(frozen=True, slots=True)
class RenderTree:
    """The whole document. Built with source inlines; resolved in place of
    them by :mod:`.resolve`."""

    title: tuple[Inline, ...]
    subtitle: tuple[Inline, ...]
    language: str
    preamble: tuple[Block, ...]
    sections: tuple[Section, ...]
    #: True when the document declares ``questions`` or uses template
    #: constructs, so it renders as a template view (§15.8).
    is_template: bool = False
    # Filled in by the resolve stage:
    locale: str = ""
    header: tuple[tuple[str, tuple[Inline, ...]], ...] = ()
    sides: tuple[SideInfo, ...] = ()
    attachments: tuple[AttachmentPart, ...] = ()
    attachments_label: str = ""
    signatures: tuple[SignatureParty, ...] = ()
    signature_labels: dict[str, str] = field(default_factory=dict)
    #: The table of contents; empty unless the style enables it.
    contents: tuple[ContentsEntry, ...] = ()
    contents_label: str = ""
    #: The attachments heading's anchor, when there are attachments.
    attachments_anchor: str = ""
    #: Between a generated label and its value ("Name: ..."), per language.
    colon: str = ": "


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------


def iter_inlines(inlines: tuple[Inline, ...]) -> Iterator[Inline]:
    """Every inline in *inlines*, depth first."""
    for inline in inlines:
        yield inline
        for nested in (getattr(inline, "children", None), getattr(inline, "title", None)):
            if isinstance(nested, tuple):
                yield from iter_inlines(nested)


def iter_block_inlines(block: Block) -> Iterator[Inline]:
    """Every inline inside *block*, depth first."""
    match block:
        case Paragraph(inlines=inlines):
            yield from iter_inlines(inlines)
        case List(items=items):
            for item in items:
                for child in item.blocks:
                    yield from iter_block_inlines(child)
        case Quote(blocks=blocks) | DraftingNote(blocks=blocks):
            for child in blocks:
                yield from iter_block_inlines(child)
        case Table(header=header, rows=rows):
            for cell in header:
                yield from iter_inlines(cell)
            for row in rows:
                for cell in row:
                    yield from iter_inlines(cell)


def iter_tree_inlines(tree: RenderTree) -> Iterator[Inline]:
    yield from iter_inlines(tree.title)
    yield from iter_inlines(tree.subtitle)
    for block in tree.preamble:
        yield from iter_block_inlines(block)
    for section in tree.sections:
        yield from iter_inlines(section.title)
        for block in section.blocks:
            yield from iter_block_inlines(block)
    for _, value in tree.header:
        yield from iter_inlines(value)
    for side in tree.sides:
        yield from iter_inlines(side.label)
        for party in side.parties:
            yield from iter_inlines(party.name)
            for _, value in party.details:
                yield from iter_inlines(value)
    for attachment in tree.attachments:
        yield from iter_inlines(attachment.title)
    for entry in tree.contents:
        yield from iter_inlines(entry.title)
    for signature in tree.signatures:
        yield from iter_inlines(signature.side)
        yield from iter_inlines(signature.name)
        for name, title in signature.signatories:
            yield from iter_inlines(name)
            yield from iter_inlines(title)


def map_inlines(inlines: tuple[Inline, ...], fn: Callable[[Inline], Inline]) -> tuple[Inline, ...]:
    """*inlines* with *fn* applied to every inline, innermost first."""
    out: list[Inline] = []
    for inline in inlines:
        children = getattr(inline, "children", None)
        if isinstance(children, tuple):
            inline = replace(inline, children=map_inlines(children, fn))
        title = getattr(inline, "title", None)
        if isinstance(title, tuple):
            inline = replace(inline, title=map_inlines(title, fn))
        out.append(fn(inline))
    return tuple(out)


def map_block_inlines(block: Block, fn: Callable[[Inline], Inline]) -> Block:
    """*block* with *fn* applied to every inline inside it."""
    match block:
        case Paragraph(inlines=inlines):
            return replace(block, inlines=map_inlines(inlines, fn))
        case List(items=items):
            return replace(block, items=tuple(
                replace(item, blocks=tuple(map_block_inlines(child, fn) for child in item.blocks)) for item in items))
        case Quote(blocks=blocks) | DraftingNote(blocks=blocks):
            return replace(block, blocks=tuple(map_block_inlines(child, fn) for child in blocks))
        case Table(header=header, rows=rows):
            return replace(block, header=tuple(map_inlines(cell, fn) for cell in header),
                           rows=tuple(tuple(map_inlines(cell, fn) for cell in row) for row in rows))
    return block


def map_tree_inlines(tree: RenderTree, fn: Callable[[Inline], Inline]) -> RenderTree:
    """*tree* with *fn* applied to every inline it holds, wherever it is."""
    def pair(value: tuple[Inline, ...]) -> tuple[Inline, ...]:
        return map_inlines(value, fn)

    return replace(
        tree,
        title=pair(tree.title),
        subtitle=pair(tree.subtitle),
        preamble=tuple(map_block_inlines(block, fn) for block in tree.preamble),
        sections=tuple(replace(section, title=pair(section.title),
                               blocks=tuple(map_block_inlines(block, fn) for block in section.blocks))
                       for section in tree.sections),
        header=tuple((label, pair(value)) for label, value in tree.header),
        sides=tuple(replace(side, label=pair(side.label), parties=tuple(
            replace(party, name=pair(party.name),
                    details=tuple((label, pair(value)) for label, value in party.details))
            for party in side.parties)) for side in tree.sides),
        attachments=tuple(replace(attachment, title=pair(attachment.title)) for attachment in tree.attachments),
        contents=tuple(replace(entry, title=pair(entry.title)) for entry in tree.contents),
        signatures=tuple(replace(signature, side=pair(signature.side), name=pair(signature.name),
                                 signatories=tuple((pair(name), pair(title)) for name, title in signature.signatories))
                         for signature in tree.signatures),
    )


def unlinked(inlines: tuple[Inline, ...]) -> tuple[Inline, ...]:
    """*inlines* as they read, without links or anchors: for text shown a
    second time, such as a heading in the table of contents, which links
    to the heading itself and must not repeat an anchor from the body."""
    out: list[Inline] = []
    for inline in inlines:
        match inline:
            case Link(children=children):
                out.extend(unlinked(children))
                continue
            case CrossRef():
                inline = replace(inline, target="")
            case TermRef():
                inline = replace(inline, target=None)
            case DefinedTerm(children=children, style=style):
                # Shown in its style, but not as a second definition.
                inline = TermRef(text=plain_text(children), target=None, style=style)
            case Value():
                inline = replace(inline, href=None)
        children = getattr(inline, "children", None)
        if isinstance(children, tuple) and not isinstance(inline, Image):
            inline = replace(inline, children=unlinked(children))
        out.append(inline)
    return tuple(out)


def assert_resolved(tree: RenderTree) -> None:
    """Raise :class:`InternalError` if *tree* still holds a source inline: a
    writer must never see LegalDown semantics (docs/decisions/0003)."""
    for inline in iter_tree_inlines(tree):
        if isinstance(inline, SOURCE_INLINES):
            raise InternalError(f"an unresolved {type(inline).__name__} reached a writer. This is a bug; "
                                "please report it with the document that triggers it.")


def plain_text(inlines: tuple[Inline, ...]) -> str:
    """The visible text of resolved *inlines*, without formatting."""
    parts: list[str] = []
    for inline in inlines:
        match inline:
            case Text(text=text) | Code(text=text) | CrossRef(text=text) | TermRef(text=text) \
                    | Value(text=text) | Blank(text=text) | FailureMarker(text=text):
                parts.append(text)
            case SoftBreak() | HardBreak():
                parts.append(" ")
            case Choice(options=options, separator=separator):
                parts.append("[" + separator.join(options) + "]")
            case DirectiveSource(directive=directive):
                parts.append(directive.source)
            case _ if getattr(inline, "children", None) is not None:
                parts.append(plain_text(inline.children))
    return "".join(parts)
