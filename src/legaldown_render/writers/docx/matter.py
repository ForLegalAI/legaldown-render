"""The front and back matter of the document (§13.8, §3.6, §13.6): the header rows and the parties,
the table of contents, the attachments and the signature blocks, laid out as the HTML writer shows them
and in the order it shows them.

Like the rest of the writer (docs/decisions/0008) it holds no rule: the entries of the table of contents,
their numbers and their ``_Toc`` bookmarks are the plan's (:attr:`~legaldown_render.resolve.docx_plan.DocxPlan.toc`),
and what the tree says is what is written.

* **Table of contents (§2.7 of the plan).** One complex field, ``TOC \\o "1-N" \\h \\z \\u``, whose cached
  result is the planned entries: one ``TOC1`` to ``TOC5`` paragraph each, a hyperlink to the entry's bookmark
  holding the number, its separator, the title, the condition label and a ``PAGEREF`` field whose cached page
  is empty (Word fills it in when it updates the field). The field opens in the first entry's paragraph and
  closes in a paragraph of its own after the last, as Word writes it. The attachments are listed by outline
  level, not by a style list: the attachments heading and each attachment's title are paragraphs of styles
  that carry one (``\\u``), so that no list separator (a comma or a semicolon, by locale) is in the field. The
  contents label is a paragraph of the ``ContentsHeading`` style: no outline level, no numbering, so not listed.
* **Attachments.** The heading and each title are bookmarked: by the plan's name for the anchor, so that
  ``{{attach:}}`` is a ``REF`` field or a link that resolves, and by the ``_Toc`` name of the contents entry.
  ``attachments.separator`` is a page break before the part (a paragraph property: no empty paragraph is left
  on the page before, and no blank page when the part falls at a page top) or a rule above each attachment.
* **Signature blocks.** Borderless tables (each party a cell, two to a row) kept on one page: a row does not split
  and every paragraph but the last keeps with the next.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ...errors import InternalError
from ...resolve.docx_plan import DocxPlan, TocEntryPlan
from ...tree import (
    AttachmentPart,
    ContentsEntry,
    Inline,
    PartyInfo,
    RenderTree,
    SideInfo,
    SignatureParty,
    plain_text,
)
from .ooxml import (
    Bookmarks,
    el,
    field_end,
    field_start,
    hyperlink_internal,
    p,
    r,
    tbl_pr,
    tc_pr,
    text_run,
    tr_pr,
    val,
)
from .runs import InlineWriter
from .styles import (
    ATTACHMENT_FILE,
    ATTACHMENT_TITLE,
    ATTACHMENTS_HEADING,
    CONDITION_LABEL,
    CONTENTS_HEADING,
    PARTY_NAME,
    PARTY_SIDE,
    SIGNATURE_LINE,
    SIGNATURE_SIDE,
    SIGNATURE_TEXT,
    TITLE_BLOCK_LABEL,
    TITLE_BLOCK_TEXT,
    toc_style,
)

#: The share of the text width the label column of the header rows and party details takes, in percent.
_LABEL_SHARE = 30
#: The gap between two cells, in twips, and the share of a signature cell the signing line leaves free, in percent.
_GUTTER = 283
_LINE_FREE = 20
#: Signature blocks in a row.
_SIGNATURE_COLUMNS = 2
#: What a character of a number takes in an entry of the contents, and the gap after it, in em; where the text
#: starts after a number's tab stop is the longest number of its level (a guess for a font the writer does
#: not measure: Word recomputes the stops when it updates the field).
_NUMBER_EM = 0.7
_NUMBER_GAP_EM = 0.6


@dataclass(frozen=True, slots=True)
class MatterSettings:
    """What the matter needs besides the tree and the plan: the page and the style's contents and attachments."""

    #: The width between the margins, in twips: where the page numbers' right tab goes, and the width of tables.
    text_width: int
    #: The body font size in points (the width of a number in the contents is estimated from it).
    points: float = 11.0
    #: ``contents.depth``: the deepest heading level the ``TOC`` field lists.
    depth: int = 2
    #: ``attachments.separator``: ``page-break``, ``rule`` or ``none``.
    separator: str = "page-break"


class MatterWriter:
    """Writes the matter around the sections. *inlines* and *bookmarks* are the body writer's, so that
    every bookmark written here counts for the links and references that point at it (the document is
    written twice, :meth:`.body.BodyWriter.body`)."""

    def __init__(self, tree: RenderTree, plan: DocxPlan, inlines: InlineWriter, bookmarks: Bookmarks,
                 settings: MatterSettings) -> None:
        self.tree = tree
        self.plan = plan
        self.inlines = inlines
        self.bookmarks = bookmarks
        self.settings = settings
        if tree.contents and len(tree.contents) != len(plan.toc):
            raise InternalError("the plan's table of contents does not hold the entries of the tree's "
                                "(a bug in the DOCX plan)")
        #: The ``_Toc`` names of the contents entries that are not a section's (a section's is the plan's
        #: ``UnitPlan.toc_bookmark``, written with its heading), in order: the attachments heading when the style
        #: gives it a label, then one per attachment. Paired by position, not by anchor: an attachment with no id
        #: or the same id as another (validator Errors, still rendered) has no anchor of its own.
        self._toc_of = [planned.bookmark for entry, planned in zip(tree.contents, plan.toc, strict=False)
                        if entry.unit is None]
        wanted = (1 if tree.attachments_label else 0) + len(tree.attachments)
        if self._toc_of and len(self._toc_of) != wanted:
            raise InternalError("the table of contents does not list the attachments heading and each attachment "
                                "(a bug in the DOCX plan)")

    # -- header rows and parties ----------------------------------------------------------

    def title_block(self) -> list[ET.Element]:
        """The header rows (label and value, as ``dt`` and ``dd``) and the parties: each side's label, then
        each party's name and its details (§13.5)."""
        tree = self.tree
        content: list[ET.Element] = []
        if tree.header:
            content.append(self._rows([(label, value) for label, value in tree.header]))
        for side in tree.sides:
            content += self._side(side)
        return content

    def _side(self, side: SideInfo) -> list[ET.Element]:
        content = [p(self.inlines.runs(side.label), style=PARTY_SIDE)]
        for party in side.parties:
            content += self._party(party)
        return content

    def _party(self, party: PartyInfo) -> list[ET.Element]:
        content = [p(self.inlines.runs(party.name), style=PARTY_NAME)]
        if party.details:
            content.append(self._rows(list(party.details)))
        return content

    def _rows(self, rows: Sequence[tuple[str, tuple[Inline, ...]]]) -> ET.Element:
        """A borderless table of label and value rows: the label in italics (a style blanked by the label
        leaves its cell empty, as the ``dt`` is)."""
        label = self.settings.text_width * _LABEL_SHARE // 100
        widths = (label, self.settings.text_width - label)
        return _table(widths, [
            [[p(text_run(name, style=TITLE_BLOCK_LABEL), style=TITLE_BLOCK_TEXT)] if name
             else [p(style=TITLE_BLOCK_TEXT)],
             [p(self.inlines.runs(value), style=TITLE_BLOCK_TEXT)]]
            for name, value in rows])

    # -- table of contents ----------------------------------------------------------------

    def contents(self) -> list[ET.Element]:
        """The contents label and the ``TOC`` field with its cached entries; nothing when the tree lists
        nothing (the HTML writer writes no contents then, label included)."""
        tree = self.tree
        if not tree.contents:
            return []
        content: list[ET.Element] = []
        if tree.contents_label:
            content.append(p(text_run(tree.contents_label), style=CONTENTS_HEADING))
        depth = max(1, min(self.settings.depth, 5))
        instruction = f'TOC \\o "1-{depth}" \\h \\z \\u'
        stops = self._number_stops(tree.contents, depth)
        for index, (entry, planned) in enumerate(zip(tree.contents, self.plan.toc, strict=True)):
            level = self._level(entry, depth)
            content.append(self._entry(planned, level, stops.get(level),
                                       lead=field_start(instruction) if index == 0 else ()))
        content.append(p(field_end()))
        return content

    @staticmethod
    def _level(entry: ContentsEntry, depth: int) -> int:
        """The level Word lists *entry* at (``TOCn``): a section's own, and an attachment's outline level
        clamped to the field's depth (below, the field does not list it at all), so that the cached result is the
        one an update gives."""
        return entry.level if entry.unit is not None else max(1, min(entry.level, depth))

    def _outline(self, level: int) -> ET.Element | None:
        """The outline level of an attachments paragraph the contents lists at *level* (``\\u`` lists by it), never
        deeper than the field's depth, which would drop it from the list. Where the contents exists and does not
        list the attachments (``contents.attachments``), the paragraphs are body text, as their style's outline
        level would list them; with no contents the style's level stands (the navigation pane)."""
        if not self.tree.contents:
            return None
        if not any(entry.unit is None for entry in self.tree.contents):
            return val("outlineLvl", 9)
        return val("outlineLvl", max(0, min(level - 1, self.settings.depth - 1)))

    def _number_stops(self, entries: Iterable[ContentsEntry], depth: int) -> dict[int, int]:
        """The position of the tab after the number, by level: left of the text of every entry of that level
        whose number is followed by a tab, so that their titles line up."""
        stops: dict[int, int] = {}
        em = self.settings.points * 20
        for entry, planned in zip(entries, self.plan.toc, strict=True):
            if planned.sep != "\t" or not planned.number:
                continue
            level = self._level(entry, depth)
            width = len(planned.number) * _NUMBER_EM * em + _NUMBER_GAP_EM * em + 240 * (level - 1)
            stops[level] = min(max(stops.get(level, 0), round(width)), self.settings.text_width // 2)
        return stops

    def _entry(self, planned: TocEntryPlan, level: int, stop: int | None,
               lead: Iterable[ET.Element] = ()) -> ET.Element:
        """One entry's paragraph: a hyperlink to its bookmark with the number, the title, the condition label,
        and the page number as a ``PAGEREF`` field with an empty cached result. *lead* are runs before the
        hyperlink (the opening of the ``TOC`` field, in the first entry)."""
        name = self._placed(planned.bookmark)
        runs: list[ET.Element] = []
        if planned.number:
            runs.append(text_run(planned.number, props=[el("noProof")]))
            runs.append(r(el("tab"), props=[el("noProof")]) if planned.sep == "\t"
                        else text_run(" ", props=[el("noProof")]))
        runs += self.inlines.runs(planned.title)
        if planned.condition_label:
            runs += [text_run(" "), text_run(planned.condition_label, style=CONDITION_LABEL)]
        runs.append(r(el("tab"), props=[el("noProof")]))
        runs += [*field_start(f"PAGEREF {name} \\h"), field_end()]
        tabs = [stop is not None and bool(planned.number) and planned.sep == "\t" and el("tab", {"val": "left", "pos": stop}),
                el("tab", {"val": "right", "leader": "dot", "pos": self.settings.text_width})]
        return p(*lead, hyperlink_internal(name, *runs), style=toc_style(level), props=[el("tabs", None, tabs)])

    def _placed(self, name: str) -> str:
        """*name*, a bookmark an entry points at: the document must write it (not known in the dry run)."""
        placed = self.inlines.placed
        if placed is not None and name.lower() not in placed:
            raise InternalError(f"a table of contents entry points at the bookmark '{name}', which the document "
                                "does not contain (a bug in the DOCX plan)")
        return name

    # -- attachments ----------------------------------------------------------------------

    def attachments(self) -> list[ET.Element]:
        """The attachments heading and each attachment's title, condition label and file (§13.8)."""
        tree = self.tree
        if not tree.attachments:
            return []
        content: list[ET.Element] = []
        toc = list(self._toc_of)
        separator = self.settings.separator
        # The page break goes before the first thing of the part, whichever that is, and before every title after the
        # first: the heading stays with the first attachment.
        breaking = separator == "page-break"
        level = 1
        if tree.attachments_label:
            level = 2
            names = self._names(tree.attachments_anchor, toc.pop(0) if toc else None)
            content.append(p(self.inlines.wrap(names, [text_run(tree.attachments_label)]) if names
                             else text_run(tree.attachments_label),
                             style=ATTACHMENTS_HEADING,
                             props=[breaking and el("pageBreakBefore"),
                                    self._outline(1)]))
            breaking = False
        for attachment in tree.attachments:
            content += self._attachment(attachment, level, toc.pop(0) if toc else None, page_break=breaking,
                                        rule=separator == "rule")
            breaking = separator == "page-break"
        return content

    def _names(self, anchor: str | None, toc: str | None) -> list[str]:
        """The bookmarks of an attachments paragraph: the plan's name for its *anchor*, unless the document already
        holds a bookmark of that name (a second attachment with the same id, or an id that is a section's), and
        the contents entry's ``_Toc`` name *toc*."""
        name = self.plan.anchors.get(anchor) if anchor else None
        names = [name] if name is not None and name.lower() not in self.bookmarks.names else []
        return [*names, *([toc] if toc else [])]

    def _attachment(self, attachment: AttachmentPart, level: int, toc: str | None, *, page_break: bool,
                    rule: bool) -> list[ET.Element]:
        names = self._names(attachment.anchor, toc)
        title = self.inlines.runs(attachment.title)
        shown = self.inlines.wrap(names, title) if names else title
        if attachment.condition_label:
            shown += [text_run(" "), text_run(attachment.condition_label, style=CONDITION_LABEL)]
        content = [p(shown, style=ATTACHMENT_TITLE, props=[
            page_break and el("pageBreakBefore"),
            rule and el("pBdr", None, el("top", {"val": "single", "sz": 4, "space": 8, "color": "C8C8C8"})),
            self._outline(level)])]
        if attachment.file:
            content.append(p(text_run(attachment.file), style=ATTACHMENT_FILE))
        return content

    # -- signature blocks -----------------------------------------------------------------

    def signatures(self) -> list[ET.Element]:
        """The signature blocks (§3.6) as a borderless table, a party to a cell, two to a row, kept together."""
        tree = self.tree
        if not tree.signatures:
            return []
        columns = min(_SIGNATURE_COLUMNS, len(tree.signatures))
        cell = self.settings.text_width // _SIGNATURE_COLUMNS
        cells = [self._signature(signature, cell) for signature in tree.signatures]
        rows = [cells[start:start + columns] for start in range(0, len(cells), columns)]
        for row in rows:
            row += [[p(style=SIGNATURE_TEXT)] for _ in range(columns - len(row))]
        # Keep the table on one page: every paragraph of it but the last keeps with the next.
        for paragraph in [paragraph for row in rows for blocks in row for paragraph in blocks][:-1]:
            _keep_next(paragraph)
        return [_table((cell,) * columns, rows)]

    def _signature(self, signature: SignatureParty, cell: int) -> list[ET.Element]:
        """One party's block: its side, its name, a signing line with the signatory's name and title (one blank
        line when none is declared) for each representative, then the date and place to be written in."""
        labels, colon = self.tree.signature_labels, self.tree.colon
        blocks = [p(self.inlines.runs(signature.side), style=SIGNATURE_SIDE),
                  p(self.inlines.runs(signature.name), style=SIGNATURE_TEXT)]

        def row(key: str, value: tuple[Inline, ...] = (), *, write_in: bool = False) -> list[ET.Element]:
            # As the text writer: a blanked label leaves the value alone and a row with neither is left out. Date
            # and place (*write_in*) are to be written in, so they keep a no-break space of the colon; a name and
            # a title without a value lose all the space after the label.
            shown = plain_text(value)
            label = f"{labels.get(key, '')}{colon}" if labels.get(key) else ""
            if not (label + shown).strip():
                return []
            lead = label if shown else label.rstrip(" " if write_in else None)
            return [p(text_run(lead) if lead else None, self.inlines.runs(value), style=SIGNATURE_TEXT)]

        for name, title in signature.signatories or (((), ()),):
            blocks.append(p(style=SIGNATURE_LINE, props=[
                el("ind", {"right": cell * _LINE_FREE // 100 + _GUTTER})]))
            blocks += [*row("name", name), *row("title", title)]
        blocks += [*row("date", write_in=True), *row("place", write_in=True)]
        return blocks


_PPR = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pPr"


def _keep_next(paragraph: ET.Element) -> None:
    """Add ``w:keepNext`` to *paragraph*'s properties, in the place the schema gives it (right after the style)."""
    properties = paragraph.find(_PPR)
    if properties is None:
        properties = el("pPr")
        paragraph.insert(0, properties)
    keep = el("keepNext")
    properties.insert(1 if properties.find("{*}pStyle") is not None else 0, keep)


def _table(widths: Sequence[int], rows: Sequence[Sequence[Sequence[ET.Element]]]) -> ET.Element:
    """A borderless table: one ``w:tc`` per entry of a row, holding its paragraphs, *widths* twips wide;
    rows do not split across pages."""
    return el(
        "tbl", None,
        tbl_pr(el("tblW", {"w": sum(widths), "type": "dxa"}), el("tblLayout", {"type": "fixed"}),
               el("tblCellMar", None, el("left", {"w": 0, "type": "dxa"}), el("right", {"w": _GUTTER, "type": "dxa"})),
               el("tblLook", {"val": "0000", "firstRow": 0, "lastRow": 0, "firstColumn": 0, "lastColumn": 0,
                              "noHBand": 1, "noVBand": 1})),
        el("tblGrid", None, [el("gridCol", {"w": width}) for width in widths]),
        [el("tr", None, tr_pr(el("cantSplit")),
            [el("tc", None, tc_pr(el("tcW", {"w": width, "type": "dxa"})), paragraphs)
             for paragraphs, width in zip(row, widths, strict=True)])
         for row in rows])
