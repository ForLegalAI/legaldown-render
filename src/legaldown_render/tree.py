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

from collections.abc import Iterator
from dataclasses import dataclass, field

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


@dataclass(frozen=True, slots=True)
class PartyInfo:
    name: tuple[Inline, ...]
    details: tuple[tuple[str, tuple[Inline, ...]], ...]
    representatives: tuple[tuple[tuple[Inline, ...], tuple[Inline, ...]], ...]


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
            for name, title in party.representatives:
                yield from iter_inlines(name)
                yield from iter_inlines(title)
    for attachment in tree.attachments:
        yield from iter_inlines(attachment.title)
    for signature in tree.signatures:
        yield from iter_inlines(signature.side)
        yield from iter_inlines(signature.name)
        for name, title in signature.signatories:
            yield from iter_inlines(name)
            yield from iter_inlines(title)


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
