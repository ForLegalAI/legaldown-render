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
from typing import Literal

from legaldown.syntax import Directive

from .errors import InternalError
from .style.model import LevelFormat

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
    """A directive as lexed by ``legaldown.syntax``, not yet resolved."""

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
    """A resolved ``{{ref:}}``: display text and the output anchor it links to.

    ``target_unit`` and ``section_unit`` name the units the reference points
    at (see :class:`Section`), for a writer that builds the
    reference from the numbers instead of using ``text``:

    * ``target_unit`` is the unit the identifier is on: a section, a list item
      or a numbered paragraph. When alternatives share the identifier
      (§15.4), it is the **first** of them, the one that keeps the anchor, as
      ``target`` does. An item or paragraph the style does not number
      (``number`` is ``None``) is still the target; ``text`` is then the
      designation of its section (§6.3, ``ref-not-enumerated``).
    * ``section_unit`` is the section that qualifies the designation: the
      section holding the item or paragraph, and for a section target the
      target itself (so it equals ``target_unit``).
    * Under the ``none`` scheme (§13.3) both are set as above: ``text`` is the
      section's heading text with the item or paragraph piece after it, so
      the section's number is ``None`` and its title is what the designation
      starts from.

    Both are ``None`` where there is no unit to name: for a copy that no longer
    links (:func:`unlinked`, as in the table of contents). A reference that does
    not resolve is a :class:`FailureMarker`, not a ``CrossRef``, and so is a
    ``{{ref:}}`` to an attachment id, which is a broken reference (§5.6);
    ``{{attach:}}`` is a :class:`Value` of kind ``attach``, whose ``href`` is
    ``"#"`` and the attachment's anchor.
    """

    text: str
    target: str
    target_unit: str | None = None
    section_unit: str | None = None


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
    #: False for a copy shown elsewhere, such as in the table of contents:
    #: it looks the same but does not define the term.
    defining: bool = True


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
    #: The numbering of a top-level paragraph of a section (see Section):
    #: its unit id, counter value (None when the style does not number
    #: paragraphs), the section's unit, and the first alternative it shares
    #: its number with. Other paragraphs are not units: all four are None.
    unit: str | None = None
    number: int | None = None
    parent_unit: str | None = None
    shares_number_with: str | None = None
    #: Its first line in the document's file, counted from 1 (§16.9), which the
    #: renderer's diagnostics name; None when the document has no source to
    #: name a line of. Not part of equality: the same content is equal
    #: wherever it stood. A block inside a list item or a quote has the item's
    #: or the quote's line: the validator addresses nothing deeper.
    line: int | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class ListItem:
    blocks: tuple[Block, ...]
    anchor_id: str = ""
    condition: str = ""
    label: str | None = None
    anchor: str | None = None
    condition_label: str | None = None
    #: The item's numbering (see Section): its unit id, counter value (None in
    #: a list that is not enumerated), the unit its designation is built on (the
    #: section for an item of a list that is not nested in an item; the enclosing
    #: item for a nested one; None in the preamble), and the first alternative
    #: it shares its number with.
    unit: str | None = None
    number: int | None = None
    parent_unit: str | None = None
    shares_number_with: str | None = None
    #: The item's marker line (see Paragraph.line).
    line: int | None = field(default=None, compare=False)


#: How a list's items are numbered (§13.2): ``renumber``, an ordered list
#: counted 1., 2., ... whatever its source says; ``enumerated``, items counted
#: in the format of their nesting depth (``enumeration.levels``); ``bullet``,
#: no numbers.
ListFamily = Literal["renumber", "enumerated", "bullet"]


@dataclass(frozen=True, slots=True)
class List:
    ordered: bool
    items: tuple[ListItem, ...]
    #: Set by the resolve stage: whether items carry generated labels.
    enumerated: bool = False
    #: Set by the resolve stage: which numbering the list is under. It is
    #: ``bullet`` exactly when ``enumerated`` is False.
    family: ListFamily = "bullet"
    #: Set by the resolve stage: how deep the list is nested, from 0 for a list
    #: that is not inside an item (a list in a quote in an item counts as one
    #: level deeper, as its format does).
    depth: int = 0
    #: Set by the resolve stage: the format its items are numbered in (the
    #: counter, ``label`` and ``ref`` templates of §13.2), None for ``bullet``.
    #: It is the entry of :attr:`RenderTree.formats` that the list's family
    #: and depth select, so that nobody has to select it again.
    format: LevelFormat | None = None


@dataclass(frozen=True, slots=True)
class Quote:
    blocks: tuple[Block, ...]
    #: Its first line (see Paragraph.line); the blocks inside it have it too.
    line: int | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class DraftingNote:
    """A ``> [!DRAFTING]`` block quote (§15.6)."""

    blocks: tuple[Block, ...]
    label: str | None = None
    #: Its first line (see Paragraph.line); the blocks inside it have it too.
    line: int | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class CodeBlock:
    text: str
    info: str = ""
    #: Its first line (see Paragraph.line).
    line: int | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class Table:
    header: tuple[tuple[Inline, ...], ...]
    rows: tuple[tuple[tuple[Inline, ...], ...], ...]
    #: Per column: "left", "center", "right", or "".
    align: tuple[str, ...]
    #: Its first line (see Paragraph.line); every cell has it too.
    line: int | None = field(default=None, compare=False)


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
    flat, in document order; ``level`` gives the hierarchy.

    **Unit numbering.** The resolve stage names the numbered units of the
    document (sections, list items, and the top-level paragraphs of a section)
    so that a writer can follow the structure behind the numbers without
    working out a rule of §13 again. Four fields, the same on :class:`Section`,
    :class:`ListItem` and :class:`Paragraph`:

    * ``unit``: an id that is unique in the document and the same on every
      render of it: ``s<k>`` for the k-th section (from 1), ``u<k>`` for the
      k-th item or paragraph in document order (the preamble first). It is not
      the anchor (which alternatives share and some units lack) and carries no
      meaning beyond identity.
    * ``number``: the unit's own counter value, the one :attr:`RenderTree.formats`
      writes with the unit's counter style: the last part of a section's number
      ("2.1" gives 1), the position of an item in its list, of a paragraph in its
      section. ``None`` for a unit that is not numbered: any section under the
      ``none`` scheme, an item of a list that is not enumerated, a paragraph
      when the style does not number paragraphs.
    * ``parent_unit``: the unit whose designation this one's is built on: for a
      section the nearest section above it with a smaller ``level`` (a skipped
      level counts as 1 and is not a unit); for an item of a list that is not
      inside an item, and for a paragraph, the section (None in the preamble);
      for a nested item, the item it is nested in.
    * ``shares_number_with``: for a later alternative that shares its number
      (§15.8; same identifier and exclusive conditions, §15.4) the unit of the
      **first** alternative, else None. It is set only on a numbered unit; the
      first alternative is a unit like any other, and each alternative has a
      ``unit`` of its own.
    """

    #: The heading's level as written; after resolution, how many parts
    #: its number has (1 for "2", 2 for "2.1"), clamped to 1-5.
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
    unit: str | None = None
    number: int | None = None
    parent_unit: str | None = None
    shares_number_with: str | None = None
    #: The heading's first line (see Paragraph.line).
    line: int | None = field(default=None, compare=False)


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
class NumberingFormats:
    """The numbering formats in effect, after the style is resolved (§13.1,
    §13.2): the same :class:`~legaldown_render.style.model.LevelFormat` the
    resolver wrote the labels and designations with, so that a writer builds
    its own numbering from the same templates.

    A level's ``counter`` styles its number, ``label`` is what shows next to the
    unit, ``ref`` is its piece of a designation. ``{n}`` is the unit's own
    number, ``{path}`` the designation up to and including the unit, and
    ``{section}`` (items and paragraphs) the designation of the enclosing section
    (nothing for a preamble item, and a ``.`` right after it goes too). A
    designation is the parent's designation followed by the unit's ``ref``
    filled in; under the ``none`` scheme the heading text stands in for a
    section's designation and a space goes before the piece.
    """

    #: The heading scheme: ``decimal``, ``legal-outline``, ``mixed`` or ``none``.
    scheme: str = "decimal"
    #: One format per heading level, 1 to 5 (§4.1); empty under the ``none``
    #: scheme, where headings are not numbered. The n-th part of a section's
    #: number takes the n-th format.
    headings: tuple[LevelFormat, ...] = ()
    #: The formats of ``enumerated`` lists by nesting depth; a list nested
    #: deeper than there are formats goes back to the first (``depth %
    #: len(enumerated)``). Empty when no list is enumerated this way.
    enumerated: tuple[LevelFormat, ...] = ()
    #: The format of every ``renumber`` list, at any depth; None when ordered
    #: lists are enumerated (``enumeration.ordered: enumerate``).
    renumber: LevelFormat | None = None
    #: The format of a section's top-level paragraphs; None when the style does
    #: not number them.
    paragraphs: LevelFormat | None = None
    #: What a reference shows around the designation (``references.format``,
    #: with ``{designation}`` for it).
    reference: str = "{designation}"


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
    #: The numbering formats the labels and designations were made with.
    formats: NumberingFormats = NumberingFormats()


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
    for inline, _ in iter_located_block_inlines(block):
        yield inline


def iter_located_block_inlines(block: Block, line: int | None = None) -> Iterator[tuple[Inline, int | None]]:
    """Every inline inside *block*, depth first, with the line of the unit
    holding it: that of the nearest enclosing node that has one, else *line*."""
    line = getattr(block, "line", None) or line
    match block:
        case Paragraph(inlines=inlines):
            yield from ((inline, line) for inline in iter_inlines(inlines))
        case List(items=items):
            for item in items:
                for child in item.blocks:
                    yield from iter_located_block_inlines(child, item.line or line)
        case Quote(blocks=blocks) | DraftingNote(blocks=blocks):
            for child in blocks:
                yield from iter_located_block_inlines(child, line)
        case Table(header=header, rows=rows):
            for cell in header:
                yield from ((inline, line) for inline in iter_inlines(cell))
            for row in rows:
                for cell in row:
                    yield from ((inline, line) for inline in iter_inlines(cell))


def iter_located_body_inlines(tree: RenderTree) -> Iterator[tuple[Inline, int | None]]:
    """Every inline in the document's body (the preamble, then each section's
    heading and blocks), with its line (:func:`iter_located_block_inlines`)."""
    for block in tree.preamble:
        yield from iter_located_block_inlines(block)
    for section in tree.sections:
        yield from ((inline, section.line) for inline in iter_inlines(section.title))
        for block in section.blocks:
            yield from iter_located_block_inlines(block, section.line)


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
                inline = replace(inline, target="", target_unit=None, section_unit=None)
            case TermRef():
                inline = replace(inline, target=None)
            case DefinedTerm():
                inline = replace(inline, anchor=None, defining=False)
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


def has_visible_content(inlines: tuple[Inline, ...]) -> bool:
    """True if resolved *inlines* show anything: text other than white space,
    or an image (which has a picture even when its alt text is empty)."""
    if plain_text(inlines).strip():
        return True
    return any(isinstance(inline, Image) or has_visible_content(getattr(inline, "children", None) or ())
               for inline in inlines)
