"""The DOCX planner: from the render tree to the numbering Word will draw (docs/decisions/0008).

``plan_docx(tree, style)`` is the one place that knows how LegalDown numbering becomes Word numbering; the DOCX
writer serialises its :class:`DocxPlan` and decides nothing (docs/decisions/0003). It is a pure function of the
resolved tree: the numbering structure the resolver put on every section, list item and top-level paragraph
(``unit``, ``number``, ``parent_unit``, ``List.family``, ``RenderTree.formats``; §13.1, §13.2) is mapped to
abstractNums, nums and, for each unit, the (numId, ilvl) of its paragraph, and every placement is checked against the
number the resolver gave the unit, by simulating Word's counters (:mod:`.docx_numbering`).

What a plan holds so far: the numbering definitions and the numbering of every unit. The reference fields,
bookmarks and table of contents are further parts of the plan that are empty here.

**Data model.** Frozen data only; the planner assigns bookmark *names*, never ids (the writer allocates those);
names are valid Word bookmark names of at most 40 characters and unique ignoring case. All sequences are tuples, all
mappings are plain dicts built once in document order and never changed afterwards.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, NamedTuple

from legaldown import Diagnostic

from ..diagnostics import by_line, record
from ..errors import InternalError, StyleError
from ..style.model import LevelFormat, Style
from ..tree import (
    DraftingNote,
    Inline,
    List,
    ListItem,
    NumberingFormats,
    Paragraph,
    Quote,
    RenderTree,
    Section,
    plain_text,
)
from ..urls import printable
from .docx_numbering import (
    NUM_FMT,
    SAMPLE_COUNTER,
    LevelParts,
    Step,
    WordNumbering,
    level_text,
    lvl_text,
    split_level_text,
    word_counter,
)
from .numbering import RENUMBERED, format_counter, heading_levels

# ---------------------------------------------------------------------------
# The plan (frozen data)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LevelDef:
    """One ``w:lvl`` of an abstractNum."""

    ilvl: int
    #: ``decimal``, ``lowerLetter``, ``upperLetter``, ``lowerRoman``, ``upperRoman`` or ``bullet``.
    num_fmt: str
    #: In OOXML form (``%1.%2``, ``(%3)``, ``Article %1``, ``•``): ``%k`` is the counter of level ``k-1``.
    lvl_text: str
    start: int = 1
    #: ``Heading1``..``Heading5``, only on levels 0-4 of the heading abstractNum.
    p_style: str | None = None
    suff: Literal["tab", "space"] = "tab"
    #: The level's own ``w:ind`` in twips. On the heading abstractNum this serves the heading; a unit that sits on
    #: the level at another nesting depth carries its own indent (:attr:`UnitPlan.ind`).
    left: int = 0
    hanging: int = 0


@dataclass(frozen=True, slots=True)
class AbstractNum:
    id: int  # 0.., in order of first use (headings first)
    kind: Literal["headings", "enumerated", "renumber", "paragraphs", "bullet"]
    #: Eight hex digits, deterministic (``0x4C440000 + id``). Equal in every LegalDown document, so Word may
    #: merge lists on paste between two of them (cosmetic).
    nsid: str
    multi_level: Literal["multilevel", "hybridMultilevel"]
    #: Always nine levels.
    levels: tuple[LevelDef, ...]


@dataclass(frozen=True, slots=True)
class Num:
    """One ``w:num``. ``nums[0]`` is the heading num (numId 1) only when headings are numbered."""

    id: int  # from 1 (numId 0 means "no numbering" and is never a Num)
    abstract_id: int
    #: ``(ilvl, value)``: the first number of the list at that level; deeper levels restart at 1. Overrides that
    #: are never reached by a paragraph are harmless.
    start_overrides: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class Literal_:
    """Text of a reference or number that is not a counter: delimiters and the words of ``references.format``."""

    text: str


@dataclass(frozen=True, slots=True)
class FieldSeg:
    """A ``REF`` field with its cached result. Instruction: ``REF <bookmark>`` + ``\\n`` if *number* + ``\\t`` if
    *suppress_text* + ``\\h`` if *hyperlink*. Without *number* it returns the bookmarked text (heading text under
    the ``none`` scheme)."""

    bookmark: str
    number: bool
    suppress_text: bool
    hyperlink: bool
    #: What Word computes (the simulator's value).
    cached: str
    #: What LibreOffice computes (it ignores ``\t``); equal to *cached* unless *suppress_text*. For tests only.
    cached_lo: str


Segment = Literal_ | FieldSeg


@dataclass(frozen=True, slots=True)
class AltNumber:
    """The number of a later alternative that shares a number (§15.8): its paragraph has ``numId 0`` and starts
    with these segments (the label as a cover of the holder's number: ``REF holder \\n \\h`` and the stripped
    period), then a tab or space. Its indent is :attr:`UnitPlan.ind`."""

    segments: tuple[Segment, ...]
    suff: Literal["tab", "space"]


@dataclass(frozen=True, slots=True)
class UnitPlan:
    """What the paragraph of one tree unit (section, list item, top-level paragraph) needs, key: ``unit``."""

    unit: str
    kind: Literal["section", "item", "paragraph"]
    #: None: the writer writes no ``w:numPr`` (a heading takes its style's; nothing else is unnumbered).
    num_id: int | None
    ilvl: int | None
    #: True: the writer MUST write ``w:numPr`` (``numId`` 0 for an alternative).
    explicit: bool
    #: ``(left, hanging)`` in twips, written as ``w:ind`` on the paragraph, for a unit whose nesting depth differs
    #: from its level's natural depth (a nested list on the heading abstractNum, an alternative); else None.
    ind: tuple[int, int] | None
    alt: AltNumber | None
    #: Word's number text for the unit (the full ``lvlText`` expansion, trailing period kept: ``1.``, ``(a)``).
    label: str
    #: What ``REF <this unit> \n`` returns (trailing period dropped): the cached text of the unit's own field.
    word_ref: str
    #: Word's counter value (None for an alternative, a bullet and an unnumbered unit).
    count: int | None
    #: Why the unit is not on the plain path: ``resync``, ``alternative``, ``reserved-level``, ``restart-family``,
    #: ``restart-section``, ``restart-alternative``, ``label-fallback``, ``clamped``, ``skipped-level``.
    cause: tuple[str, ...]
    #: Names to wrap around the paragraph's title/first paragraph content: the anchor bookmark first (when the
    #: unit has an anchor), then the helper bookmark a cover needs.
    bookmarks: tuple[str, ...]
    #: ``_Toc`` name of a heading listed in the contents.
    toc_bookmark: str | None


@dataclass(frozen=True, slots=True)
class RefPlan:
    """The fields of every ``{{ref:}}`` to one unit (``CrossRef.target_unit``), wherever it stands."""

    #: ``references.format`` literals around the cover of the designation.
    segments: tuple[Segment, ...]
    #: ``concat(cached) == CrossRef.text``.
    exact: bool


@dataclass(frozen=True, slots=True)
class TocEntryPlan:
    #: The ``ContentsEntry`` it is made from (``ContentsEntry.unit``: the section's unit, None for attachments).
    unit: str | None
    level: int  # 1..5, the TOCn style
    #: Word's number text of the heading incl. the trailing period; ``""`` for attachments.
    number: str
    sep: Literal["\t", " ", ""]
    title: tuple[Inline, ...]  # ContentsEntry.title, already unlinked
    condition_label: str | None
    bookmark: str  # the ``_Toc`` name the entry's hyperlink and PAGEREF point to
    cached_page: str = ""  # always empty


@dataclass(frozen=True, slots=True)
class DocxPlan:
    abstract_nums: tuple[AbstractNum, ...]
    nums: tuple[Num, ...]
    #: The num the heading styles' ``numPr`` points to; None under the ``none`` scheme.
    heading_num_id: int | None
    units: Mapping[str, UnitPlan] = field(default_factory=dict)
    #: Every output anchor of the tree (units, ``DefinedTerm.anchor``, ``AttachmentPart.anchor``,
    #: ``attachments_anchor``) -> bookmark name.
    anchors: Mapping[str, str] = field(default_factory=dict)
    #: ``CrossRef.target_unit`` -> its fields.
    refs: Mapping[str, RefPlan] = field(default_factory=dict)
    toc: tuple[TocEntryPlan, ...] = ()


# ---------------------------------------------------------------------------
# Style checks that belong before rendering (ADR 0006)
# ---------------------------------------------------------------------------

#: The longest ``lvlText`` Word accepts (taken from Word's limit of 255 characters on a level's text).
MAX_LVL_TEXT = 255

#: A literal ``%`` that ``lvlText`` could not tell from a placeholder once the label is filled in: before a digit,
#: or before a field that becomes ``%k``.
_AMBIGUOUS_PERCENT = re.compile(r"%(?=[0-9{])")


def _formats_of(style: Style) -> NumberingFormats:
    """The numbering formats *style* gives (as ``Resolver._formats`` writes them into the tree)."""
    enumeration = style.enumeration
    return NumberingFormats(
        scheme=style.numbering.scheme,
        headings=() if style.numbering.scheme == "none" else heading_levels(style.numbering),
        enumerated=tuple(enumeration.levels) if enumeration.enabled or enumeration.ordered == "enumerate" else (),
        renumber=RENUMBERED if enumeration.ordered == "renumber" else None,
        paragraphs=style.paragraphs.format if style.paragraphs.numbered else None,
        reference=style.references.format,
    )


def docx_style_errors(style: Style) -> list[str]:
    """What in *style* Word's numbering cannot express, one message per problem naming the setting; empty when
    the style is fine for DOCX.

    Word's ``lvlText`` has no way to write a ``%`` before a digit, and a level's text is at most
    :data:`MAX_LVL_TEXT` characters. These are problems of configuration, so a DOCX render calls this before
    rendering and raises :class:`~legaldown_render.errors.StyleError` (ADR 0006); :func:`plan_docx` checks again."""
    problems: list[str] = []
    formats = _formats_of(style)
    templates: list[tuple[str, LevelFormat]] = []
    if formats.headings:
        templates += [(f"numbering.levels[{i}]", f) for i, f in enumerate(style.numbering.levels[:5])]
    if formats.enumerated:
        templates += [(f"enumeration.levels[{i}]", f) for i, f in enumerate(style.enumeration.levels)]
    if formats.paragraphs is not None:
        templates.append(("paragraphs.format", formats.paragraphs))
    for path, level in templates:
        for name, template in (("label", level.label), ("ref", level.ref)):
            if _AMBIGUOUS_PERCENT.search(template):
                problems.append(
                    f"{path}.{name}: a '%' before a digit or a field cannot be written in a Word numbering level "
                    f"(DOCX); write it without the '%'")
    if problems:
        return problems
    for kind, levels in _definitions(formats, set()).items():
        for level in levels:
            if len(level.lvl_text) > MAX_LVL_TEXT:
                problems.append(f"the {kind} numbering level {level.ilvl + 1} is longer than {MAX_LVL_TEXT} "
                                f"characters in Word (DOCX): shorten its label or the refs it shows")
    return problems


# ---------------------------------------------------------------------------
# Level definitions
# ---------------------------------------------------------------------------

_BULLETS = ("•", "–", "◦")
#: Twips: one centimetre, the step of the indent of items.
_STEP = 567
#: Twips per character of a heading's widest label.
_CHARACTER = 113


def _num_fmt(counter: str, unmapped: set[str]) -> str:
    """The ``numFmt`` of *counter*; a counter Word has no format for is written as ``decimal`` and noted."""
    found = NUM_FMT.get(counter)
    if found is None:
        unmapped.add(counter)
        return "decimal"
    return found


def _level(ilvl: int, num_fmt: str, parts: LevelParts, *, p_style: str | None = None,
           widths: Mapping[int, str] | None = None) -> LevelDef:
    """One level. Headings (*widths*: the ``numFmt`` of each level a ``%k`` names) get an indent wide enough for
    their widest label; items step one centimetre per level. A label with a word in it (``Article {n}``) is
    followed by a space, any other by a tab."""
    words = any(isinstance(part, str) and any(c.isalpha() for c in part) for part in parts)
    if widths is None:
        left, hanging = _STEP * (ilvl + 1), _STEP
    else:
        sample = sum(len(part) if isinstance(part, str) else len(SAMPLE_COUNTER[widths[part]]) for part in parts)
        left = hanging = max(_STEP, _CHARACTER * (sample + 2))
    return LevelDef(ilvl, num_fmt, lvl_text(parts), p_style=p_style, suff="space" if words else "tab",
                    left=left, hanging=hanging)


def _neutral(ilvl: int) -> LevelDef:
    """A level nothing uses: its own counter, decimal."""
    return LevelDef(ilvl, "decimal", f"%{ilvl + 1}", left=_STEP * (ilvl + 1), hanging=_STEP)


def _definitions(formats: NumberingFormats, unmapped: set[str]) -> dict[str, list[LevelDef]]:
    """The nine levels of every abstractNum *formats* needs, by kind. A label that needs a number of the section
    (``{section}``, or ``{path}`` of an item or paragraph in one) is written without it, as for a preamble item:
    Word shows only counters of the list itself in a level."""
    textual = formats.scheme == "none"
    out: dict[str, list[LevelDef]] = {}
    if formats.headings:
        refs = [f.ref for f in formats.headings]
        fmts = {j: _num_fmt(f.counter, unmapped) for j, f in enumerate(formats.headings)}
        levels = [_level(k, fmts[k], level_text(f.label, refs, k, section=None), p_style=f"Heading{k + 1}",
                         widths=fmts) for k, f in enumerate(formats.headings)]
        out["headings"] = [*levels, *(_neutral(k) for k in range(5, 9))]
    if formats.enumerated:
        size = len(formats.enumerated)
        refs = [formats.enumerated[j % size].ref for j in range(9)]
        out["enumerated"] = [
            _level(d, _num_fmt(formats.enumerated[d % size].counter, unmapped),
                   level_text(formats.enumerated[d % size].label, refs, d, section=(), textual=textual))
            for d in range(9)]
    if formats.renumber is not None:
        f = formats.renumber
        out["renumber"] = [_level(d, _num_fmt(f.counter, unmapped),
                                  level_text(f.label, [f.ref] * 9, d, section=(), textual=textual))
                           for d in range(9)]
    if formats.paragraphs is not None:
        f = formats.paragraphs
        out["paragraphs"] = [
            _level(0, _num_fmt(f.counter, unmapped), level_text(f.label, [f.ref], 0, section=(), textual=textual)),
            *(_neutral(k) for k in range(1, 9))]
    out["bullet"] = [LevelDef(d, "bullet", _BULLETS[d % 3], left=_STEP * (d + 1), hanging=_STEP) for d in range(9)]
    return out


def _needs_section(level: LevelFormat) -> bool:
    """True if *level*'s label shows a number of the section: ``{section}``, or ``{path}`` (which holds it) of an
    item or paragraph in a section."""
    return "{section}" in level.label or "{path}" in level.label or "{section}" in level.ref


def _reads(level: LevelDef, levels: Sequence[LevelDef]) -> str:
    """*level* as it reads with every counter at 1, for a message."""
    return "".join(word_counter(1, levels[part].num_fmt) if isinstance(part, int) else part
                   for part in split_level_text(level.lvl_text))


# ---------------------------------------------------------------------------
# The planner
# ---------------------------------------------------------------------------

_Unit = Section | ListItem | Paragraph


class _Abstract:
    """An abstractNum being built: the simulator reads its levels while the plan grows."""

    def __init__(self, abstract_id: int, kind: str, levels: list[LevelDef]) -> None:
        self.id, self.kind, self.levels = abstract_id, kind, levels


class _Placed(NamedTuple):
    """Where a list item is numbered, for the lists nested in it."""

    num: Num
    abstract: _Abstract


class _Planner:
    def __init__(self, tree: RenderTree, file: str | None) -> None:
        self.tree, self.file = tree, file or ""
        self.formats = tree.formats
        self.textual = tree.formats.scheme == "none"
        self.diagnostics: list[Diagnostic] = []
        self.sim = WordNumbering()
        self.abstracts: list[_Abstract] = []
        self.nums: list[Num] = []
        self.units: dict[str, UnitPlan] = {}
        self.parent: dict[str, str | None] = {}
        self.section_level: dict[str, int] = {}
        self.placed: dict[str, _Placed] = {}
        self.paragraph_nums: dict[str, Num] = {}
        self.flagged: set[str] = set()
        self.reported: set[tuple[str, object]] = set()
        self.heading_num: Num | None = None
        self.depth_reported = False
        self.unmapped: set[str] = set()
        self.definitions = _definitions(self.formats, self.unmapped)
        self.by_kind: dict[str, _Abstract] = {}

    # -- registry -------------------------------------------------------------------

    def abstract(self, kind: str) -> _Abstract:
        """The abstractNum of *kind*, made on first use (so ids follow the order of use)."""
        found = self.by_kind.get(kind)
        if found is None:
            found = _Abstract(len(self.abstracts), kind, self.definitions[kind])
            self.abstracts.append(found)
            self.by_kind[kind] = found
            self.sim.add_abstract(found.id, found.levels)
        return found

    def new_num(self, abstract: _Abstract, ilvl: int | None = None, first: int = 1) -> Num:
        """A num on *abstract*; with *ilvl*, its list starts there at *first* and deeper levels at 1."""
        overrides = () if ilvl is None else tuple((i, first if i == ilvl else 1) for i in range(ilvl, 9))
        num = Num(len(self.nums) + 1, abstract.id, overrides)
        self.nums.append(num)
        self.sim.add_num(num)
        return num

    def report(self, rule: str, level: str, message: str, line: int | None) -> None:
        record(Diagnostic(rule=rule, level=level, message=message, line=line, file=self.file), self.diagnostics)

    # -- the pass -------------------------------------------------------------------

    def run(self) -> tuple[DocxPlan, list[Diagnostic]]:
        if self.formats.headings:
            self.heading_num = self.new_num(self.abstract("headings"))
        for block in self.tree.preamble:
            self.block(block, None)
        for section in self.tree.sections:
            self.section(section)
            for block in section.blocks:
                self.block(block, section)
        for counter in sorted(self.unmapped):
            self.report("render-docx-counter", "warning",
                        f"The counter '{printable(counter)}' has no Word number format; it is written as decimal.",
                        None)
        abstracts = tuple(
            AbstractNum(a.id, a.kind, f"{0x4C440000 + a.id:08X}",  # type: ignore[arg-type]
                        "multilevel" if a.kind == "headings" else "hybridMultilevel", tuple(a.levels))
            for a in self.abstracts)
        heading_id = self.heading_num.id if self.heading_num else None
        return DocxPlan(abstracts, tuple(self.nums), heading_id, units=self.units), by_line(self.diagnostics)

    def block(self, block: object, section: Section | None) -> None:
        match block:
            case Paragraph(unit=str()) if section is not None:
                self.paragraph(block, section)
            case List():
                self.list_(block, section)
            case Quote(blocks=blocks) | DraftingNote(blocks=blocks):
                for child in blocks:
                    self.block(child, section)

    # -- what a unit gets -----------------------------------------------------------

    def add(self, node: _Unit, kind: Literal["section", "item", "paragraph"], num: Num | None, ilvl: int | None, *,
            explicit: bool, cause: tuple[str, ...] = (), step: Step | None = None) -> None:
        assert node.unit is not None
        self.units[node.unit] = UnitPlan(
            node.unit, kind, num.id if num else None, ilvl, explicit, None, None,
            step.label if step else "", step.word_ref if step else "", step.count if step else None,
            cause, (), None)

    def ancestor_flagged(self, unit: str) -> bool:
        parent = self.parent.get(unit)
        while parent is not None:
            if parent in self.flagged:
                return True
            parent = self.parent.get(parent)
        return False

    def check(self, node: _Unit, kind: str, step: Step, counter: str, group: object, cause: tuple[str, ...]) -> None:
        """Compare what Word gives *node* with what the resolver gave it, and report the first difference of a
        chain (a unit with a flagged ancestor is not reported again). Word's count must be the resolver's; the
        counter must read the same (letters past 26 and Roman numerals from 4000 do not); and the whole label must
        read the same, unless a diagnostic already says why it cannot (an unsupported label, a clamped depth)."""
        assert node.unit is not None and node.number is not None
        fresh = not self.ancestor_flagged(node.unit)
        expected = format_counter(node.number, counter)
        word = kind[0].upper() + kind[1:]
        if step.count == node.number and "clamped" in cause:
            return  # a level 9 in the format of level 9: the clamp is the reason, and it is reported
        if step.count == node.number and step.own == expected:
            if step.label == node.label or "label-fallback" in cause:
                return
            rule = "render-docx-number-mismatch"
            message = f"Word will number the {kind} '{printable(node.label or '')}' as '{printable(step.label)}'."
        elif step.count != node.number:
            rule = "render-docx-number-mismatch"
            message = f"Word will number the {kind} '{printable(node.label or '')}' as '{printable(step.label)}'."
        elif counter.endswith("roman") and node.number >= 4000:
            rule = "render-docx-roman-overflow"
            message = (f"{word} {node.number} is a Roman numeral of 4000 or more: the renderer shows digits "
                       f"('{printable(expected)}'), Word shows '{printable(step.own)}'.")
        elif counter.endswith("alpha"):
            rule = "render-docx-letter-overflow"
            message = (f"{word} {node.number} ('{printable(expected)}') will show as '{printable(step.own)}' in "
                       f"Word: Word repeats the letter after 26 ('aa', 'bb'), the renderer counts 'aa', 'ab'. "
                       f"References to it and to its sub-items read the same way.")
        else:
            rule = "render-docx-number-mismatch"
            message = f"Word will write the counter of the {kind} '{printable(expected)}' as '{printable(step.own)}'."
        self.flagged.add(node.unit)
        # An overflow is reported once for the list, heading level or section it starts in.
        once = rule.endswith("overflow")
        if fresh and not (once and (rule, group) in self.reported):
            self.reported.add((rule, group))
            self.report(rule, "warning", message, node.line)

    def fallback_label(self, level: LevelFormat, family: str, kind: str, line: int | None) -> None:
        """Report a label that needs the section's number where Word can show only the list's own counters."""
        levels = self.definitions[family]
        self.report("render-docx-section-label", "warning",
                    f"The label '{printable(level.label)}' of {kind}s in a section shows the section's number, "
                    f"which Word can show only for headings: in Word it reads '{printable(_reads(levels[0], levels))}'.",
                    line)

    # -- units ----------------------------------------------------------------------

    def section(self, section: Section) -> None:
        unit = section.unit
        assert unit is not None
        self.parent[unit] = section.parent_unit
        self.section_level[unit] = section.level
        if self.heading_num is None or section.number is None:
            self.add(section, "section", None, None, explicit=False)
            return
        ilvl = section.level - 1
        parent = section.parent_unit
        cause: tuple[str, ...] = ()
        if (parent is None and section.level > 1) or (parent is not None
                                                      and self.section_level[parent] + 1 < section.level):
            cause = ("skipped-level",)
            self.report("render-docx-heading-skip", "warning",
                        f"Heading level skipped before '{printable(plain_text(section.title))}': the number shows "
                        f"the skipped level as 1. Word may number the headings after it differently.", section.line)
        step = self.sim.step(self.heading_num.id, ilvl, unit)
        self.add(section, "section", self.heading_num, ilvl, explicit=False, cause=cause, step=step)
        self.check(section, "heading", step, self.formats.headings[ilvl].counter, ("heading", section.level), cause)

    def paragraph(self, paragraph: Paragraph, section: Section) -> None:
        assert paragraph.unit is not None and section.unit is not None
        self.parent[paragraph.unit] = paragraph.parent_unit
        fmt = self.formats.paragraphs
        if paragraph.number is None or fmt is None:
            self.add(paragraph, "paragraph", None, None, explicit=False)
            return
        num = self.paragraph_nums.get(section.unit)
        if num is None:
            num = self.paragraph_nums[section.unit] = self.new_num(self.abstract("paragraphs"), 0)
        cause: tuple[str, ...] = ()
        if _needs_section(fmt):
            cause = ("label-fallback",)
            self.fallback_label(fmt, "paragraphs", "paragraph", paragraph.line)
        step = self.sim.step(num.id, 0, paragraph.unit)
        self.add(paragraph, "paragraph", num, 0, explicit=True, cause=cause, step=step)
        self.check(paragraph, "paragraph", step, fmt.counter, ("paragraphs", section.unit), cause)

    def list_(self, lst: List, section: Section | None) -> None:
        first = lst.items[0]
        abstract = self.abstract(lst.family)
        ilvl = min(lst.depth, 8)
        parent = first.parent_unit
        placed = self.placed.get(parent) if parent is not None else None
        cause: tuple[str, ...] = ()
        # Nested in an item: that item's num, if it is of the same kind and Word counts the list from where the
        # resolver does (a sibling list continues, a list in a new item restarts); otherwise a num of its own.
        if (placed is not None and placed.abstract is abstract and lst.depth <= 8
                and (lst.family == "bullet" or self.sim.peek(placed.num.id, ilvl) == first.number)):
            num = placed.num
        else:
            num = self.new_num(abstract, ilvl, first.number or 1)
            if lst.depth > 8:
                cause = ("clamped",)
                if not self.depth_reported:
                    self.depth_reported = True
                    self.report("render-docx-list-depth", "warning",
                                f"A list is nested {lst.depth} levels deep: Word has 9. Items from level 10 on are "
                                f"numbered in the format of level 9 and restart by fixed values.", first.line)
            elif placed is not None:
                kinds = placed.abstract is not abstract
                cause = ("restart-family",) if kinds else ("resync",)
                self.report("render-docx-static-restart", "info",
                            "A list nested in a list of another kind restarts by a fixed setting (Restart at 1), "
                            "not by structure: an item added above it in Word will not restart it." if kinds else
                            "A list that Word would continue from another list in the same item restarts by a fixed "
                            "setting (Restart at 1), not by structure.", first.line)
        fmt = lst.format
        qualified = fmt is not None and section is not None and _needs_section(fmt)
        if fmt is not None and qualified:
            self.fallback_label(fmt, lst.family, "list item", first.line)
        for index, item in enumerate(lst.items):
            assert item.unit is not None
            self.parent[item.unit] = item.parent_unit
            item_cause = (cause if index == 0 else ()) + (("label-fallback",) if qualified else ())
            if fmt is None:
                self.units[item.unit] = UnitPlan(item.unit, "item", num.id, ilvl, True, None, None, "", "", None,
                                                 item_cause, (), None)
            else:
                step = self.sim.step(num.id, ilvl, item.unit)
                self.add(item, "item", num, ilvl, explicit=True, cause=item_cause, step=step)
                self.check(item, "list item", step, fmt.counter, ("list", first.unit), item_cause)
            self.placed[item.unit] = _Placed(num, abstract)
            for child in item.blocks:
                self.block(child, section)


def plan_docx(tree: RenderTree, style: Style, *, file: str | None = None) -> tuple[DocxPlan, list[Diagnostic]]:
    """The numbering plan of *tree* for Word, and the diagnostics only planning can produce (all
    ``render-docx-*``, in the order of their lines, naming *file*). Pure: the same tree and style give an equal plan.
    *style* is the one the tree was resolved under. Raises :class:`~legaldown_render.errors.StyleError` for a style
    Word's numbering cannot express (:func:`docx_style_errors`; a render checks that first, ADR 0006)."""
    problems = docx_style_errors(style)
    if problems:
        raise StyleError(f"'{style.name}'", problems)
    if tree.formats != _formats_of(style):
        raise InternalError("the tree and the style given to the DOCX planner do not belong together. This is a bug; "
                            "please report it with the document that triggers it.")
    return _Planner(tree, file).run()
