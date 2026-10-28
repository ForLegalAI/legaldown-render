"""The body of the document: ``w:body`` and its closing ``w:sectPr``.

:class:`BodyWriter` lays out the resolved tree as the plan says (docs/decisions/0008). It decides
nothing: which paragraph carries which number, which bookmarks wrap it and what a cross-reference
is made of are the plan's (:mod:`~legaldown_render.resolve.docx_plan`), looked up by the unit id
the tree gives each section, list item and top-level paragraph:

* a unit's paragraph gets ``w:numPr`` when its :class:`~legaldown_render.resolve.docx_plan.UnitPlan`
  says it is ``explicit`` (a heading otherwise takes the numbering of its style), ``numId`` 0 and
  the segments of its :class:`~legaldown_render.resolve.docx_plan.AltNumber` for a later
  alternative that shares a number (§15.8), and ``w:ind`` where the plan sets one;
* its bookmarks wrap the title or first paragraph's content, not the condition label or the
  alternative's number;
* a list item whose first block is not a paragraph is an empty numbered paragraph, so that the
  number and the bookmarks have a paragraph to stand on, followed by its blocks;
* the other blocks of an item, and a quote's or a drafting note's, are plain paragraphs at the indent
  of what they belong to.

**Interim.** The title block (the header rows and parties), the table of contents, the attachments
and the signature blocks are not written yet: only the title and the subtitle are. The
:func:`section_properties` and the builders in :mod:`.ooxml` are what the rest will keep.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, replace

from ...errors import InternalError
from ...resolve.docx_plan import AltNumber, DocxPlan, UnitPlan
from ...style.docx import PageGeometry
from ...tree import (
    Block,
    CodeBlock,
    DraftingNote,
    Inline,
    List,
    ListItem,
    Paragraph,
    Quote,
    RenderTree,
    Rule,
    Section,
    Table,
    plain_text,
)
from .ooxml import Bookmarks, el, p, tbl_pr, tc_pr, text_run, tr_pr, val
from .package import Relationships
from .runs import InlineWriter
from .styles import (
    CODE,
    CODE_INDENT,
    CONDITION_LABEL,
    DRAFTING_NOTE,
    DRAFTING_NOTE_LABEL,
    NOTE_INDENT,
    QUOTE,
    QUOTE_INDENT,
    SUBTITLE,
    TABLE_GRID,
    TITLE,
    container_bar,
    heading_style,
)

#: The left indent the paragraph styles set, in twips: what a paragraph in that style has without an indent of its own.
_STYLE_LEFT = {QUOTE: QUOTE_INDENT, DRAFTING_NOTE: NOTE_INDENT}

#: Limits on what a plan's indents can do to the page, in twips: Word does not take a left indent past 31680
#: (22 inches), and a paragraph needs room to be read in; a column needs room for a word.
_MAX_INDENT = 31680
_MIN_TEXT = 2268
_MIN_COLUMN = 1134

_RULE_COLOR = "C8C8C8"
#: The space above and below the text of a table cell, in twips.
_CELL_PADDING = 40


def section_properties(page: PageGeometry, *, header_id: str | None = None, footer_id: str | None = None) -> ET.Element:
    """The ``w:sectPr`` of the one section: the running header and footer, by the
    relationship ids of their parts, then the paper and the margins in twips.
    ``w:pgSz`` of a landscape page has the long edge as its width, and says so
    (``w:orient``), which Word needs to print it so."""
    return el(
        "sectPr", None,
        header_id and el("headerReference", {"type": "default", "r:id": header_id}),
        footer_id and el("footerReference", {"type": "default", "r:id": footer_id}),
        el("pgSz", {"w": page.width, "h": page.height, "orient": "landscape" if page.landscape else None}),
        el("pgMar", {"top": page.top, "right": page.right, "bottom": page.bottom, "left": page.left,
                     "header": page.header, "footer": page.footer, "gutter": 0}),
    )


def document_title(tree: RenderTree) -> str:
    """The document's title as plain text, trimmed: the one text the title paragraph, the
    ``{title}`` of a header or footer and the document properties all show."""
    return plain_text(tree.title).strip()


@dataclass(frozen=True, slots=True)
class Context:
    """Where a block is written: the paragraph style of a quote or drafting note it is in, how far
    its paragraphs are indented, and how much the quotes and notes around it add to a numbered unit."""

    #: The style of the plain paragraphs here (``Quote``, ``DraftingNote``), None at the top.
    style: str | None = None
    #: Where the text of a plain paragraph starts, in twips from the margin: the text of the list item
    #: it continues, or what the enclosing quotes and notes indent.
    left: int = 0
    #: What the quotes and notes around a list item add to its own indent (§13.2).
    extra: int = 0


class BodyWriter:
    """Writes the body of one document: see the module docstring.

    *bookmarks* and *relationships* are the document's (the bookmark ids are unique across the package;
    the relationships receive the external links); *text_width* is the width between the margins in
    twips, which tables take."""

    def __init__(self, tree: RenderTree, plan: DocxPlan, bookmarks: Bookmarks, relationships: Relationships,
                 text_width: int, link_color: str) -> None:
        self.tree = tree
        self.plan = plan
        self.bookmarks = bookmarks
        self.relationships = relationships
        self.text_width = text_width
        self.link_color = link_color
        abstracts = {abstract.id: abstract for abstract in plan.abstract_nums}
        self._levels = {num.id: abstracts[num.abstract_id].levels for num in plan.nums if num.abstract_id in abstracts}
        attachment_titles = {a.anchor: plain_text(a.title) for a in tree.attachments if a.anchor}
        #: Not known until a first, dry run has written the document: see :meth:`body`.
        self.inlines = InlineWriter(plan, relationships, bookmarks, None, attachment_titles, link_color)

    def _left(self, left: int) -> int:
        """*left* twips of indent, kept where a paragraph can still be read: not past Word's limit, and leaving
        :data:`_MIN_TEXT` of the text width (a deeply nested list, or a plan's wide indent, cannot push it off the page)."""
        return max(0, min(left, self.text_width - _MIN_TEXT, _MAX_INDENT))

    # -- the document ---------------------------------------------------------------------

    def body(self, page: PageGeometry, *, header_id: str | None = None, footer_id: str | None = None) -> ET.Element:
        """``w:body`` for the tree, closed by the section properties of *page*.

        The document is written twice. The first, dry run learns which bookmarks it writes (a name in the plan
        is not enough: a defined term inside alt text or a link title is never written), and the second writes
        it for real: a ``REF`` field to a name the document does not hold is then a bug in the plan
        (:class:`~legaldown_render.errors.InternalError`), and a link or a term reference to one shows its text
        without the link. Whatever :meth:`content` writes counts, so the attachments' bookmarks need no list of
        their own."""
        dry = BodyWriter(self.tree, self.plan, Bookmarks(), Relationships(), self.text_width, self.link_color)
        dry.content()
        self.inlines.placed = dry.bookmarks.names
        return el("body", None, self.content(), section_properties(page, header_id=header_id, footer_id=footer_id))

    def content(self) -> list[ET.Element]:
        """The block-level content of the body: the title and the subtitle, the preamble and the sections."""
        tree = self.tree
        content: list[ET.Element] = []
        title = document_title(tree)
        if title:
            content.append(p(text_run(title), style=TITLE))
        subtitle = plain_text(tree.subtitle).strip()
        if subtitle:
            content.append(p(text_run(subtitle), style=SUBTITLE))
        content += self.blocks(tree.preamble, Context())
        for section in tree.sections:
            content += self.section(section)
        if content and content[-1].tag.endswith("}tbl"):
            content.append(p())  # a table cannot be the last thing before the section properties
        return content

    def section(self, section: Section) -> list[ET.Element]:
        """The heading paragraph of *section* (``heading N``: the resolver has clamped the level to
        1-5) and its blocks. The condition label (§15.8) is a run inside the heading, after the title,
        so that Word's table of contents keeps it."""
        level = min(max(section.level, 1), 5)
        unit = self.plan.units.get(section.unit or "")
        title = self.inlines.title_runs(section.title, unit.literal_refs if unit is not None else ())
        heading = self._unit(section.unit, title, style=heading_style(level),
                             context=Context(), conditions=(section.condition_label,), after=True)
        return [heading, *self.blocks(section.blocks, Context())]

    # -- blocks ---------------------------------------------------------------------------

    def blocks(self, blocks: Iterable[Block], context: Context) -> list[ET.Element]:
        """The elements of *blocks*. Two tables in a row get an empty paragraph between them, which
        Word needs to keep them apart."""
        content: list[ET.Element] = []
        for block in blocks:
            for element in self.block(block, context):
                if element.tag.endswith("}tbl") and content and content[-1].tag.endswith("}tbl"):
                    content.append(p())
                content.append(element)
        return content

    def block(self, block: Block, context: Context) -> Iterator[ET.Element]:
        match block:
            case Paragraph():
                yield self._paragraph(block, context)
            case List(items=items):
                for item in items:
                    yield from self._item(item, context)
            case Quote(blocks=blocks):
                yield from self.blocks(blocks, replace(context, style=QUOTE, left=context.left + QUOTE_INDENT,
                                                       extra=context.extra + QUOTE_INDENT))
            case DraftingNote(blocks=blocks, label=label):
                inner = replace(context, style=DRAFTING_NOTE, left=context.left + NOTE_INDENT,
                                extra=context.extra + NOTE_INDENT)
                if label:
                    yield p(text_run(label, style=DRAFTING_NOTE_LABEL), style=DRAFTING_NOTE,
                            props=[self._plain_indent(inner)])
                yield from self.blocks(blocks, inner)
            case CodeBlock(text=text):
                # One paragraph per line: Word shades and keeps them together as one block.
                # Inside a quote or a note the paragraph stays at the container's indent and keeps its bar.
                bar = container_bar(context.style) if context.style else None
                left = self._left(context.left if bar is not None else context.left + CODE_INDENT)
                for line in text.rstrip("\n").split("\n"):
                    yield p(text_run(line), style=CODE,
                            props=[el("ind", {"left": left}) if context.left else None,
                                   bar is not None and el("pBdr", None, bar)])
            case Table():
                yield from self._table(block, context)
            case Rule():
                bar = container_bar(context.style) if context.style else None
                yield p(style=context.style, props=[
                    el("pBdr", None, bar, el("bottom", {"val": "single", "sz": 4, "space": 1, "color": _RULE_COLOR})),
                    self._plain_indent(context)])
            case _:
                raise InternalError(f"a {type(block).__name__} reached the DOCX writer, which does not write it "
                                    "(a bug in the DOCX writer)")

    def _plain_indent(self, context: Context) -> ET.Element | None:
        """``w:ind`` for a plain paragraph in *context*, unless its style already indents so far."""
        left = self._left(context.left)
        return el("ind", {"left": left}) if left != _STYLE_LEFT.get(context.style or "", 0) else None

    def _paragraph(self, block: Paragraph, context: Context) -> ET.Element:
        content = self.inlines.runs(block.inlines)
        if block.unit is not None:
            return self._unit(block.unit, content, style=context.style, context=context,
                              conditions=(block.condition_label,))
        return p(self._conditions((block.condition_label,), content), content, style=context.style,
                 props=[self._plain_indent(context)])

    def _item(self, item: ListItem, context: Context) -> Iterator[ET.Element]:
        """A list item: its first paragraph is the numbered paragraph, or an empty numbered paragraph
        stands for it when the item opens with another block (or has none)."""
        first = item.blocks[0] if item.blocks and isinstance(item.blocks[0], Paragraph) else None
        rest = item.blocks[1:] if first is not None else item.blocks
        conditions = (item.condition_label, first.condition_label if first is not None else None)
        content = self.inlines.runs(first.inlines) if first is not None else []
        yield self._unit(item.unit, content, style=context.style, context=context, conditions=conditions)
        inner = replace(context, left=self._text_left(self.plan.units.get(item.unit or ""), context))
        yield from self.blocks(rest, inner)

    # -- numbered paragraphs --------------------------------------------------------------

    def _indent(self, unit: UnitPlan) -> tuple[int, int] | None:
        """``(left, hanging)`` of the unit's paragraph: its own indent from the plan, else the indent of its
        level in the numbering definition; None when the paragraph has neither."""
        if unit.ind is not None:
            return unit.ind
        levels = self._levels.get(unit.num_id) if unit.num_id else None
        if levels is None or unit.ilvl is None:
            return None
        return levels[unit.ilvl].left, levels[unit.ilvl].hanging

    def _text_left(self, unit: UnitPlan | None, context: Context) -> int:
        """Where the text of a unit's paragraph starts: what its continuation blocks are indented to."""
        indent = self._indent(unit) if unit is not None else None
        return self._left(context.left if indent is None else indent[0] + context.extra)

    def _unit(self, unit_id: str | None, content: Sequence[ET.Element], *, style: str | None, context: Context,
              conditions: Sequence[str | None], after: bool = False) -> ET.Element:
        """The paragraph of the unit *unit_id* (None, or a unit the plan does not know, has no numbering) with
        *content*, the title or first paragraph's runs. The condition labels lead the paragraph, or follow the
        content with *after* (a heading); the unit's bookmarks wrap the content alone, or, where the plan says they are
        collapsed, stand as points at the start of the paragraph."""
        unit = self.plan.units.get(unit_id) if unit_id else None
        props: list[ET.Element | None] = []
        number: list[ET.Element] = []
        names: list[str] = []
        points: list[str] = []
        if unit is not None:
            if unit.explicit or unit.alt is not None:
                props.append(self._numbering(unit))
            indent = self._indent(unit)
            if indent is not None and (unit.ind is not None or context.extra):
                left = self._left(indent[0] + context.extra)
                hanging = min(indent[1], left)
                props.append(el("ind", {"left": left, "hanging": hanging or None}))
            if unit.alt is not None:
                number = self._alternative(unit.alt)
            (points if unit.collapsed else names).extend(unit.bookmarks)
            names.extend([unit.toc_bookmark] if unit.toc_bookmark else [])
        else:
            props.append(self._plain_indent(context))
        wrapped = [*self.inlines.points(points), *(self.inlines.wrap(names, content) if names else content)]
        labels = self._conditions(conditions, content, after=after)
        return p(number, wrapped + labels if after else [*labels, *wrapped], style=style, props=props)

    def _numbering(self, unit: UnitPlan) -> ET.Element:
        """``w:numPr`` of an explicit unit: its num and level, ``numId`` 0 (no numbering) for an alternative."""
        num_id = 0 if unit.alt is not None else unit.num_id
        if num_id is None:
            raise InternalError(f"the unit {unit.unit} must carry numbering but the plan gives no num "
                                "(a bug in the DOCX plan)")
        return el("numPr", None, unit.ilvl is not None and val("ilvl", unit.ilvl), val("numId", num_id))

    def _alternative(self, alt: AltNumber) -> list[ET.Element]:
        """The number of a later alternative as text and fields (§15.8), then its tab or space."""
        separator = el("r", None, el("tab")) if alt.suff == "tab" else text_run(" ")
        return [*self.inlines.segments(alt.segments), separator]

    def _conditions(self, labels: Sequence[str | None], content: Sequence[ET.Element], *,
                    after: bool = False) -> list[ET.Element]:
        """The condition labels (§15.8) as ``ConditionLabel`` runs, each one apart from the text by a space
        (none after an empty paragraph's labels)."""
        runs: list[ET.Element] = []
        for label in filter(None, labels):
            run = text_run(label, style=CONDITION_LABEL)
            if after:
                runs += [text_run(" "), run]
            else:
                runs += [run, text_run(" ")] if content else [run]
        return runs

    # -- tables ---------------------------------------------------------------------------

    def _table(self, table: Table, context: Context) -> Iterator[ET.Element]:
        """A table: ``TableGrid``, the header row repeated on every page (``w:tblHeader``), columns aligned as
        the table says, and indented with the list item or quote it is in. A row shorter than the header is
        completed with empty cells."""
        columns = max(len(table.header), *(len(row) for row in table.rows), 0)
        if not columns:
            return
        left = self._left(context.left)
        width = self.text_width - left
        if width < columns * _MIN_COLUMN:  # no room to indent: the columns come first
            left, width = 0, self.text_width
        grid = [width // columns] * columns
        grid[-1] += width - sum(grid)
        align = (*table.align, *[""] * columns)[:columns]

        def row(cells: tuple[tuple[Inline, ...], ...], *, header: bool = False) -> ET.Element:
            padded = (*cells, *[()] * (columns - len(cells)))
            return el("tr", None, tr_pr(header and el("tblHeader")), [
                el("tc", None, tc_pr(el("tcW", {"w": size, "type": "dxa"})),
                   p(self.inlines.runs(cell), props=[
                       el("spacing", {"before": 0, "after": 0}), el("jc", {"val": alignment or "left"})]))
                for cell, size, alignment in zip(padded, grid, align, strict=True)])

        yield el(
            "tbl", None,
            tbl_pr(val("tblStyle", TABLE_GRID), el("tblW", {"w": width, "type": "dxa"}),
                   left > 0 and el("tblInd", {"w": left, "type": "dxa"}),
                   el("tblCellMar", None, el("top", {"w": _CELL_PADDING, "type": "dxa"}),
                      el("bottom", {"w": _CELL_PADDING, "type": "dxa"})),
                   el("tblLook", {"val": "0620", "firstRow": 1, "lastRow": 0, "firstColumn": 0, "lastColumn": 0,
                                  "noHBand": 1, "noVBand": 1})),
            el("tblGrid", None, [el("gridCol", {"w": size}) for size in grid]),
            row(table.header, header=True) if table.header else None,
            [row(cells) for cells in table.rows])
