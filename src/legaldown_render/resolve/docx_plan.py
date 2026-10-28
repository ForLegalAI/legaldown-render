"""The DOCX planner: from the render tree to the numbering Word will draw (docs/decisions/0008).

``plan_docx(tree)`` is the one place that knows how LegalDown numbering becomes Word numbering; the DOCX writer
serialises its :class:`DocxPlan` and decides nothing (docs/decisions/0003). It is a pure function of the resolved
tree: the numbering structure the resolver put on every section, list item and top-level paragraph (``unit``,
``number``, ``parent_unit``, ``List.family``, ``RenderTree.formats``; §13.1, §13.2) is mapped to abstractNums, nums
and, for each unit, the (numId, ilvl) of its paragraph, and every placement is checked against the number the
resolver gave the unit, by simulating Word's counters (:mod:`.docx_numbering`).

A plan holds the numbering definitions and the numbering of every unit, the bookmark of every anchor of the tree
(``anchors``, ``UnitPlan.bookmarks``; :mod:`.docx_bookmarks`), the fields of every cross-reference (``refs``: the
renderer's designation of the target covered by ``REF`` fields and the delimiters between them, with Word's value as
each field's cached result; :mod:`.docx_cover`) and the entries of the table of contents (``toc``,
``UnitPlan.toc_bookmark``), with the diagnostics for what Word will not show as the renderer does.
``UnitPlan.ind`` is never set. :func:`docx_style_errors` is the part that runs on every DOCX render, before it
(ADR 0006).

**Data model.** Frozen data only; the planner assigns bookmark *names*, never ids (the writer allocates those);
names are valid Word bookmark names of at most 40 characters and unique ignoring case. All sequences are tuples, all
mappings are plain dicts built once in document order and never changed afterwards.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, NamedTuple

from legaldown import Diagnostic

from ..diagnostics import by_line
from ..style.model import LevelFormat, Style
from ..tree import (
    CrossRef,
    DefinedTerm,
    DraftingNote,
    Inline,
    List,
    ListItem,
    NumberingFormats,
    Paragraph,
    Quote,
    RenderTree,
    Section,
    iter_located_body_inlines,
    iter_tree_inlines,
)
from ..urls import printable
from .docx_bookmarks import ATTACHMENTS_HEADING, BookmarkNames
from .docx_cover import Field, Shown, Title, atoms, cover, suppressed, text_of, uncovered
from .docx_numbering import (
    NUM_FMT,
    SAMPLE_COUNTER,
    Count,
    Ghost,
    LevelParts,
    Lit,
    Step,
    WordNumbering,
    extend_tokens,
    fill_tokens,
    level_text,
    level_text_problem,
    lvl_text,
    shows_section,
    split_level_text,
    word_counter,
)
from .numbering import format_counter, formats_of

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
    #: ``(ilvl, value)``: the first number of the list at that level; the deeper levels restart by themselves when
    #: that level counts.
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
    #: Names to wrap around the paragraph's title/first paragraph content: the bookmark of the unit's anchor when it
    #: has one, else a hidden one when a reference field points at it, else none.
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

#: The setting a kind of abstractNum is made from, for a message.
_SETTING = {"headings": "numbering.levels", "enumerated": "enumeration.levels", "renumber": "enumeration.ordered",
            "paragraphs": "paragraphs.format"}


def docx_style_errors(style: Style) -> list[str]:
    """What in *style* Word's numbering cannot express, one message per problem naming the setting; empty when
    the style is fine for DOCX.

    Word's ``lvlText`` has no way to write a ``%`` before a digit or a counter, whether it stands in one template
    or meets a counter where a label and the refs it shows are put together, and a level's text is at most
    :data:`MAX_LVL_TEXT` characters. These are problems of configuration, so a DOCX render calls this before
    rendering (``style.docx.docx_settings``) and raises :class:`~legaldown_render.errors.StyleError` (ADR 0006)."""
    problems: list[str] = []
    formats = formats_of(style)
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
                problems.append(f"{path}.{name}: a '%' before a digit or a field cannot be written in a Word "
                                f"numbering level (DOCX); write it without the '%'")
    if problems:
        return problems
    unwritable: list[tuple[str, int]] = []
    definitions = _definitions(formats, unwritable)
    first_level: dict[str, int] = {}
    for kind, level in unwritable:
        first_level.setdefault(kind, level)
    for kind, level in first_level.items():
        problems.append(f"{_SETTING[kind]}: a '%' meets a digit or a counter where the label and the refs of level "
                        f"{level + 1} are put together, which a Word numbering level cannot write (DOCX)")
    for kind, levels in definitions.items():
        for level in levels:
            if len(level.lvl_text) > MAX_LVL_TEXT:
                problems.append(f"{_SETTING.get(kind, kind)}: the numbering level {level.ilvl + 1} is longer than "
                                f"{MAX_LVL_TEXT} characters in Word (DOCX): shorten its label or the refs it shows")
    return problems


# ---------------------------------------------------------------------------
# Level definitions
# ---------------------------------------------------------------------------

_BULLETS = ("•", "–", "◦")
#: Twips: one centimetre, the step of the indent of items.
_STEP = 567
#: Twips per character of a heading's widest label.
_CHARACTER = 113


def _level(ilvl: int, num_fmt: str, parts: LevelParts, *, p_style: str | None = None,
           widths: Mapping[int, str] | None = None, unwritable: list[tuple[str, int]] | None = None,
           kind: str = "") -> LevelDef:
    """One level. Headings (*widths*: the ``numFmt`` of each level a ``%k`` names) get an indent wide enough for
    their widest label; items step one centimetre per level. A label with a word in it (``Article {n}``) is
    followed by a space, any other by a tab. With *unwritable*, parts a level text cannot write are noted there
    (and written as nothing) instead of raising."""
    words = any(isinstance(part, str) and any(c.isalpha() for c in part) for part in parts)
    if widths is None:
        left, hanging = _STEP * (ilvl + 1), _STEP
    else:
        sample = sum(len(part) if isinstance(part, str) else len(SAMPLE_COUNTER[widths[part]]) for part in parts)
        left = hanging = max(_STEP, _CHARACTER * (sample + 2))
    if unwritable is not None and level_text_problem(parts) is not None:
        unwritable.append((kind, ilvl))
        parts = ()
    return LevelDef(ilvl, num_fmt, lvl_text(parts), p_style=p_style, suff="space" if words else "tab",
                    left=left, hanging=hanging)


def _neutral(ilvl: int) -> LevelDef:
    """A level nothing uses: its own counter, decimal."""
    return LevelDef(ilvl, "decimal", f"%{ilvl + 1}", left=_STEP * (ilvl + 1), hanging=_STEP)


def _definitions(formats: NumberingFormats, unwritable: list[tuple[str, int]] | None = None
                 ) -> dict[str, list[LevelDef]]:
    """The nine levels of every abstractNum *formats* needs, by kind. A label that needs a number of the section
    (``{section}``, or ``{path}`` of an item or paragraph in one) is written without it, as for a preamble item:
    Word shows only counters of the list itself in a level."""
    textual = formats.scheme == "none"
    out: dict[str, list[LevelDef]] = {}
    if formats.headings:
        refs = [f.ref for f in formats.headings]
        fmts = {j: NUM_FMT[f.counter] for j, f in enumerate(formats.headings)}
        levels = [_level(k, fmts[k], level_text(f.label, refs, k, section=None), p_style=f"Heading{k + 1}",
                         widths=fmts, unwritable=unwritable, kind="headings")
                  for k, f in enumerate(formats.headings)]
        out["headings"] = [*levels, *(_neutral(k) for k in range(5, 9))]
    if formats.enumerated:
        size = len(formats.enumerated)
        refs = [formats.enumerated[j % size].ref for j in range(9)]
        out["enumerated"] = [
            _level(d, NUM_FMT[formats.enumerated[d % size].counter],
                   level_text(formats.enumerated[d % size].label, refs, d, section=(), textual=textual),
                   unwritable=unwritable, kind="enumerated")
            for d in range(9)]
    if formats.renumber is not None:
        f = formats.renumber
        out["renumber"] = [_level(d, NUM_FMT[f.counter],
                                  level_text(f.label, [f.ref] * 9, d, section=(), textual=textual),
                                  unwritable=unwritable, kind="renumber") for d in range(9)]
    if formats.paragraphs is not None:
        f = formats.paragraphs
        out["paragraphs"] = [
            _level(0, NUM_FMT[f.counter], level_text(f.label, [f.ref], 0, section=(), textual=textual),
                   unwritable=unwritable, kind="paragraphs"),
            *(_neutral(k) for k in range(1, 9))]
    out["bullet"] = [LevelDef(d, "bullet", _BULLETS[d % 3], left=_STEP * (d + 1), hanging=_STEP) for d in range(9)]
    return out


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


class _Reference(NamedTuple):
    """The ``{{ref:}}`` to one unit that the plan builds fields for: the unit, its section, what the renderer shows and
    the first line it stands at (None outside the body)."""

    target: str
    section: str | None
    text: str
    line: int | None


class _Built(NamedTuple):
    """What the plan makes of one reference: its pieces (literal text and fields), whether they read exactly as the
    renderer's text, and what LibreOffice shows for them when that is not what Word shows (a field with ``\\t``),
    else None."""

    pieces: list[str | Field]
    exact: bool
    lo_text: str | None


class _Drift:
    """A run of units of one level that Word counts off from the resolver by the same amount."""

    def __init__(self, node: _Unit, kind: str, step: Step, delta: int) -> None:
        self.node, self.kind, self.step, self.delta, self.more = node, kind, step, delta, 0


class _Planner:
    def __init__(self, tree: RenderTree, file: str | None) -> None:
        self.tree, self.file = tree, file or ""
        self.formats = tree.formats
        self.textual = tree.formats.scheme == "none"
        self.diagnostics: list[Diagnostic] = []
        self.found: dict[tuple[str, str, str], int] = {}
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
        self.drifts: list[_Drift] = []
        self.open_drift: dict[tuple[int, int], _Drift] = {}
        self.heading_num: Num | None = None
        self.bullet_num: Num | None = None
        self.depth_reported = False
        self.definitions = _definitions(self.formats)
        self.by_kind: dict[str, _Abstract] = {}
        #: Every unit's node, in document order; what Word numbers each as (:class:`Step`); and its designation as
        #: the resolver wrote it, as tokens (§13.1-§13.3), each counter with the unit that holds it.
        self.nodes: dict[str, _Unit] = {}
        self.steps: dict[str, Step] = {}
        self.desig: dict[str, list[Shown]] = {}
        #: The parts of the number of the open section, one per level: the unit that holds it (a skipped level has a
        #: :class:`Ghost`) and its value.
        self.owners: list[tuple[object, int]] = []

    # -- registry -------------------------------------------------------------------

    def abstract(self, kind: str) -> _Abstract:
        """The abstractNum of *kind*, made on first use (so ids follow the order of use). Only headings can skip a
        level (the renderer counts it as 1, as LibreOffice does); in any other list a level that nothing numbers
        is read as Word reads it, 0, so that a label that needs it is reported."""
        found = self.by_kind.get(kind)
        if found is None:
            found = _Abstract(len(self.abstracts), kind, self.definitions[kind])
            self.abstracts.append(found)
            self.by_kind[kind] = found
            self.sim.add_abstract(found.id, found.levels, skipped="lo" if kind == "headings" else "word")
        return found

    def new_num(self, abstract: _Abstract, ilvl: int | None = None, first: int = 1) -> Num:
        """A num on *abstract*; with *ilvl*, its list starts there at *first* (the deeper levels restart by
        themselves when that level counts)."""
        num = Num(len(self.nums) + 1, abstract.id, () if ilvl is None else ((ilvl, first),))
        self.nums.append(num)
        self.sim.add_num(num)
        return num

    def report(self, rule: str, level: str, message: str, line: int | None) -> None:
        """Record a finding; the same one (rule, level, message) again is one, at the lowest line."""
        key = (rule, level, message)
        index = self.found.get(key)
        found = Diagnostic(rule=rule, level=level, message=message, line=line, file=self.file)
        if index is None:
            self.found[key] = len(self.diagnostics)
            self.diagnostics.append(found)
        else:
            known = self.diagnostics[index]
            if line is not None and (known.line is None or line < known.line):
                self.diagnostics[index] = found

    # -- references, bookmarks and the table of contents -----------------------------

    def words(self, unit: str) -> tuple[Shown, ...]:
        """What ``REF <unit> \\n`` returns, as tokens: the number Word draws for the unit without its trailing periods
        (:attr:`Step.word_ref` with the unit that holds each counter)."""
        tokens = list(self.steps[unit].tokens)
        while tokens and isinstance(tokens[-1], Lit):
            stripped = tokens[-1].text.rstrip(".")
            if stripped == tokens[-1].text:
                break
            if stripped:
                tokens[-1] = Lit(stripped)
                break
            tokens.pop()
        return tuple(tokens)

    def fields(self, designation: Sequence[Shown]) -> list[Field]:
        """The fields that can stand for parts of *designation*: for each unit that holds a counter of it, ``REF \\n``
        of its bookmark, and ``REF \\n \\t`` where that leaves the counters bare (:func:`.docx_cover.suppressed`); for a
        heading under the ``none`` scheme the bookmarked text. A later alternative takes its number from the unit that
        holds it and is never a field's target (§15.8)."""
        fields: list[Field] = []
        seen: set[str] = set()
        for token in designation:
            owner = None if isinstance(token, Lit) else token.owner
            if not isinstance(owner, str) or owner in seen or owner not in self.units:
                continue
            seen.add(owner)
            if self.units[owner].num_id == 0 or self.units[owner].alt is not None:
                continue
            if isinstance(token, Title):
                fields.append(Field(owner, False, False, (token,)))
            elif owner in self.steps:
                words = self.words(owner)
                fields.append(Field(owner, True, False, words))
                bare = suppressed(words)
                if bare is not None and atoms(bare) != atoms(words):
                    fields.append(Field(owner, True, True, bare))
        return fields

    def why_uncovered(self, designation: Sequence[Shown], fields: Sequence[Field]) -> str:
        """Why no combination of *fields* and delimiters reads like *designation* (§13.1)."""
        wanted = atoms(designation)
        kind, owner = wanted[uncovered(designation, fields) or 0]
        if kind == "c" and isinstance(owner, Ghost):
            return "a heading level is skipped before it, and no field stands for the level that is missing"
        for candidate in fields:
            if candidate.number and any(isinstance(t, Count) and t.owner == owner for t in candidate.tokens):
                return (f"Word draws its number as '{printable(text_of(candidate.tokens))}', with text or punctuation "
                        f"around the counter that the designation does not have and that Word's \\t switch cannot "
                        f"leave out")
        return "no combination of Word's fields reads like it"

    def own_field(self, unit: str | None) -> Field | None:
        """The one field for the number Word draws for *unit* (its heading text under the ``none`` scheme), which a
        reference falls back to when its designation cannot be covered; None for a unit with neither."""
        if unit is None or unit not in self.units:
            return None
        if unit in self.steps:
            return Field(unit, True, False, self.words(unit))
        title = self.desig.get(unit, [])
        return Field(unit, False, False, (title[0],)) if title and isinstance(title[0], Title) else None

    def reference(self, found: _Reference) -> _Built | None:
        """The fields of the reference *found*: its designation covered by fields and delimiters, inside the text of
        ``references.format``; None for a target the plan knows nothing of. A designation that cannot be covered is one
        field for the number Word draws for the target, and is reported, as is a reference that reads differently
        in Word for a reason no diagnostic of a unit already gives."""
        node = self.nodes.get(found.target)
        if node is None or found.target not in self.units:
            return None
        numbered = isinstance(node, Section) or node.number is not None
        designation = self.desig[found.target] if numbered else self.desig.get(found.section or "", [])
        fields = self.fields(designation)
        covered = cover(designation, fields)
        reason = None
        if covered is None:
            reason = self.why_uncovered(designation, fields)
            own = self.own_field(found.target if numbered else found.section)
            if own is None:
                return None
            covered = [own]
        pieces: list[str | Field] = []
        parts = self.formats.reference.split("{designation}")
        for index, literal in enumerate(parts):
            for piece in ([literal] if literal else []) + (covered if index < len(parts) - 1 else []):
                if pieces and isinstance(piece, str) and isinstance(pieces[-1], str):
                    pieces[-1] += piece
                else:
                    pieces.append(piece)
        shown = "".join(piece if isinstance(piece, str) else text_of(piece.tokens) for piece in pieces)
        text = printable(found.text)
        if reason is not None:
            self.report("render-docx-reference", "warning",
                        f"The cross-reference '{text}' cannot be built from Word's numbering fields: {reason}. It is "
                        f"one field for the target's own number, which reads '{printable(shown)}' in Word.",
                        found.line)
        elif shown != found.text:
            chain = {t.owner for t in designation if not isinstance(t, Lit) and isinstance(t.owner, str)}
            if not chain & self.flagged and found.target not in self.flagged:
                self.report("render-docx-reference", "warning",
                            f"The cross-reference '{text}' will read '{printable(shown)}' in Word: a part of it is "
                            f"numbered or written differently there.", found.line)
        lo_text = None
        if any(isinstance(piece, Field) and piece.suppress_text for piece in pieces):
            lo_text = "".join(piece if isinstance(piece, str) else text_of(self.words(piece.owner) if piece.number
                                                                           else piece.tokens) for piece in pieces)
        return _Built(pieces, shown == found.text, lo_text)

    def finish(self) -> tuple[dict[str, UnitPlan], dict[str, str], dict[str, RefPlan], tuple[TocEntryPlan, ...]]:
        """The bookmarks, the cross-reference fields and the table of contents, which need the numbering of every
        unit: the bookmark of every anchor (:class:`.docx_bookmarks.BookmarkNames`, given out in the order of the
        document so that the names are the same on every run), a hidden one for a unit a field needs that has no
        anchor, the fields of every ``{{ref:}}`` target, and the table of contents entries."""
        found: dict[str, _Reference] = {}
        for inline in iter_tree_inlines(self.tree):
            if isinstance(inline, CrossRef) and inline.target_unit is not None:
                found.setdefault(inline.target_unit, _Reference(inline.target_unit, inline.section_unit,
                                                                inline.text, None))
        for inline, line in iter_located_body_inlines(self.tree):
            if isinstance(inline, CrossRef) and inline.target_unit in found and line is not None:
                known = found[inline.target_unit]
                if known.line is None or line < known.line:
                    found[inline.target_unit] = known._replace(line=line)
        built: dict[str, _Built] = {}
        for target, reference in found.items():
            made = self.reference(reference)
            if made is not None:
                built[target] = made
        needed = {piece.owner for made in built.values() for piece in made.pieces if isinstance(piece, Field)}
        word_only = [(found[target].text, made.lo_text) for target, made in built.items() if made.lo_text is not None]
        if word_only:
            first, lo = word_only[0]
            more = f"; so do {len(word_only) - 1} more" if len(word_only) > 1 else ""
            self.report("render-docx-reference-word-only", "info",
                        f"The cross-reference '{printable(first)}' leaves out the words of a number with Word's \\t "
                        f"switch, which LibreOffice ignores: there it reads '{printable(lo)}'{more}.", None)

        names = BookmarkNames()
        anchors: dict[str, str] = {}
        bookmark: dict[str, str] = {}
        units = dict(self.units)
        for unit, node in self.nodes.items():
            anchor = node.anchor
            if anchor:
                name = names.anchor("ld_", anchor)
                anchors.setdefault(anchor, name)
            elif unit in needed:
                name = names.hidden()
            else:
                continue
            bookmark[unit] = name
            units[unit] = replace(units[unit], bookmarks=(name,))
        for inline in iter_tree_inlines(self.tree):
            if isinstance(inline, DefinedTerm) and inline.defining and inline.anchor and inline.anchor not in anchors:
                anchors[inline.anchor] = names.anchor("lddef_", inline.anchor.removeprefix("def:"))
        for attachment in self.tree.attachments:
            if attachment.anchor and attachment.anchor not in anchors:
                anchors[attachment.anchor] = names.anchor("ldatt_", attachment.anchor)
        if self.tree.attachments_anchor and self.tree.attachments_anchor not in anchors:
            anchors[self.tree.attachments_anchor] = names.fixed(ATTACHMENTS_HEADING)

        refs: dict[str, RefPlan] = {}
        for target, made in built.items():
            segments: list[Segment] = []
            for piece in made.pieces:
                if isinstance(piece, str):
                    segments.append(Literal_(piece))
                    continue
                cached = text_of(piece.tokens)
                plain = text_of(self.words(piece.owner)) if piece.number else cached
                segments.append(FieldSeg(bookmark[piece.owner], piece.number, piece.suppress_text, True, cached,
                                         plain if piece.suppress_text else cached))
            refs[target] = RefPlan(tuple(segments), made.exact)

        toc: list[TocEntryPlan] = []
        for entry in self.tree.contents:
            name = names.toc()
            number = ""
            sep: Literal["\t", " ", ""] = ""
            mine = units.get(entry.unit) if entry.unit is not None else None
            if mine is not None:
                mine = units[mine.unit] = replace(mine, toc_bookmark=name)
                suff: str | None = None
                if mine.alt is not None:
                    number = "".join(piece.text if isinstance(piece, Literal_) else piece.cached
                                     for piece in mine.alt.segments)
                    suff = mine.alt.suff
                elif mine.ilvl is not None:
                    number, suff = mine.label, self.definitions["headings"][mine.ilvl].suff
                if number:
                    sep = "\t" if suff == "tab" else " "
            toc.append(TocEntryPlan(entry.unit, entry.level, number, sep, entry.title, entry.condition_label, name))
        return units, anchors, refs, tuple(toc)

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
        for drift in self.drifts:
            self.report_drift(drift)
        units, anchors, refs, toc = self.finish()
        abstracts = tuple(
            AbstractNum(a.id, a.kind, f"{0x4C440000 + a.id:08X}",  # type: ignore[arg-type]
                        "multilevel" if a.kind == "headings" else "hybridMultilevel", tuple(a.levels))
            for a in self.abstracts)
        heading_id = self.heading_num.id if self.heading_num else None
        return (DocxPlan(abstracts, tuple(self.nums), heading_id, units=units, anchors=anchors, refs=refs, toc=toc),
                by_line(self.diagnostics))

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
        self.nodes[node.unit] = node
        if step is not None:
            self.steps[node.unit] = step
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

    def check(self, node: _Unit, kind: str, step: Step, counter: str, group: object, cause: tuple[str, ...],
              key: tuple[int, int]) -> None:
        """Compare what Word gives *node* with what the resolver gave it. Word's count must be the resolver's (a run
        of units off by the same amount is one finding, :meth:`report_drift`); the counter must read the same
        (letters past 26 and Roman numerals from 4000 do not); and the whole label must read the same unless a
        diagnostic already says why it cannot (a label that needs the section, a clamped depth). A unit whose
        ancestor already differs is not reported again. *counter* is the one the level Word uses is written in."""
        assert node.unit is not None and node.number is not None
        fresh = not self.ancestor_flagged(node.unit)
        expected = format_counter(node.number, counter)
        word = kind[0].upper() + kind[1:]
        if step.count != node.number:
            self.flagged.add(node.unit)
            if fresh:
                delta = step.count - node.number
                drift = self.open_drift.get(key)
                if drift is not None and drift.delta == delta:
                    drift.more += 1
                else:
                    drift = self.open_drift[key] = _Drift(node, kind, step, delta)
                    self.drifts.append(drift)
            return
        self.open_drift.pop(key, None)
        if step.own == expected:
            if step.label == node.label or "label-fallback" in cause or "clamped" in cause:
                return
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

    def report_drift(self, drift: _Drift) -> None:
        """One finding for a run of units that Word counts off by the same amount: the first, and how many follow."""
        node, step = drift.node, drift.step
        message = (f"Word will number the {drift.kind} '{printable(node.label or '')}' as '{printable(step.label)}', "
                   f"{abs(drift.delta)} {'later' if drift.delta > 0 else 'earlier'} than the renderer")
        if drift.more:
            message += f"; so will the following {drift.more}"
        self.report("render-docx-number-mismatch", "warning", message + ".", node.line)

    def fallback_label(self, level: LevelFormat, family: str, ilvl: int, kind: str, line: int | None) -> None:
        """Report a label that shows the section's number where Word can show only the list's own counters."""
        levels = self.definitions[family]
        self.report("render-docx-section-label", "warning",
                    f"The label '{printable(level.label)}' of {kind}s in a section shows the section's number, "
                    f"which Word can show only for headings: in Word it reads "
                    f"'{printable(_reads(levels[ilvl], levels))}'.", line)

    def shared_counter(self, message: str, line: int | None) -> None:
        self.report("render-docx-shared-counter", "warning", message, line)

    # -- units ----------------------------------------------------------------------

    def section_designation(self, section: Section) -> list[Shown]:
        """The designation of *section* as tokens, written as the resolver writes it (§13.1): the ``ref`` of every part
        of its number after the one before; a part the document skipped is a :class:`Ghost` that no unit holds. Under
        the ``none`` scheme it is the heading's text (§13.3), and empty when that is."""
        assert section.unit is not None
        if self.textual:
            text = section.designation or ""
            return [Title(section.unit, text)] if text else []
        assert section.number is not None
        del self.owners[section.level - 1:]
        while len(self.owners) < section.level - 1:
            self.owners.append((Ghost(section.unit, len(self.owners)), 1))
        self.owners.append((section.unit, section.number))
        designation: list[Shown] = []
        for part, (owner, value) in enumerate(self.owners):
            level = self.formats.headings[min(part, len(self.formats.headings) - 1)]
            designation = extend_tokens(
                designation, fill_tokens(level.ref, n=[Count(owner, format_counter(value, level.counter))]),
                textual=False)
        return designation

    def section(self, section: Section) -> None:
        unit = section.unit
        assert unit is not None
        self.parent[unit] = section.parent_unit
        self.section_level[unit] = section.level
        self.desig[unit] = self.section_designation(section)
        if self.heading_num is None or section.number is None:
            self.add(section, "section", None, None, explicit=False)
            return
        ilvl = section.level - 1
        parent = section.parent_unit
        skipped = (parent is None and section.level > 1) or (
            parent is not None and self.section_level[parent] + 1 < section.level)
        cause = ("skipped-level",) if skipped else ()
        step = self.sim.step(self.heading_num.id, ilvl, unit)
        self.add(section, "section", self.heading_num, ilvl, explicit=False, cause=cause, step=step)
        self.check(section, "heading", step, self.formats.headings[ilvl].counter, ("heading", section.level), cause,
                   (0, ilvl))

    def paragraph(self, paragraph: Paragraph, section: Section) -> None:
        assert paragraph.unit is not None and section.unit is not None
        self.parent[paragraph.unit] = paragraph.parent_unit
        fmt = self.formats.paragraphs
        base = self.desig[section.unit]
        if paragraph.number is None or fmt is None:
            self.desig[paragraph.unit] = base
            self.add(paragraph, "paragraph", None, None, explicit=False)
            return
        piece = fill_tokens(fmt.ref, n=[Count(paragraph.unit, format_counter(paragraph.number, fmt.counter))],
                            section=base)
        self.desig[paragraph.unit] = extend_tokens(base, piece, textual=self.textual)
        abstract = self.abstract("paragraphs")
        num = self.paragraph_nums.get(section.unit)
        if num is None:
            num = self.paragraph_nums[section.unit] = self.new_num(abstract, 0)
        cause: tuple[str, ...] = ()
        if shows_section(fmt.label, [fmt.ref], 0, textual=self.textual):
            cause = ("label-fallback",)
            self.fallback_label(fmt, "paragraphs", 0, "paragraph", paragraph.line)
        step = self.sim.step(num.id, 0, paragraph.unit)
        self.add(paragraph, "paragraph", num, 0, explicit=True, cause=cause, step=step)
        self.check(paragraph, "paragraph", step, fmt.counter, ("paragraphs", section.unit), cause, (abstract.id, 0))

    def list_(self, lst: List, section: Section | None) -> None:
        first = lst.items[0]
        if lst.family == "bullet":
            self.bullets(lst, section)
            return
        fmt = lst.format
        assert fmt is not None
        abstract = self.abstract(lst.family)
        ilvl = min(lst.depth, 8)
        # The counter of the level Word uses: past level 9 that is level 9's, not the resolver's cycle.
        levels = self.formats.enumerated if lst.family == "enumerated" else (fmt,)
        counter = levels[min(lst.depth, 8) % len(levels)].counter
        parent = first.parent_unit
        placed = self.placed.get(parent) if parent is not None else None
        cause: tuple[str, ...] = ()
        # Nested in an item: that item's num, if it is of the same kind and Word counts the list from where the
        # resolver does (a sibling list continues, a list in a new item restarts); otherwise a num of its own.
        if (placed is not None and placed.abstract is abstract and lst.depth <= 8
                and self.sim.peek(placed.num.id, ilvl) == first.number):
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
            elif placed is not None and first.number > 1:
                cause = ("resync",)
                self.shared_counter(
                    "A list that goes on from an earlier list in the same item (the resolver counts them as one) "
                    "starts at a fixed number in Word: it will not follow items added or removed before it.",
                    first.line)
            elif placed is not None:
                kinds = placed.abstract is not abstract
                cause = ("restart-family",) if kinds else ("resync",)
                self.report("render-docx-static-restart", "info",
                            "A list nested in a list of another kind restarts by a fixed setting (Restart at 1), "
                            "not by structure: an item added above it in Word will not restart it." if kinds else
                            "A list that Word would continue from another list in the same item restarts by a fixed "
                            "setting (Restart at 1), not by structure.", first.line)
        refs = ([f.ref for f in (self.formats.enumerated[j % len(self.formats.enumerated)]
                                 for j in range(lst.depth + 1))]
                if lst.family == "enumerated" else [fmt.ref] * (lst.depth + 1))
        qualified = section is not None and shows_section(fmt.label, refs, lst.depth, textual=self.textual)
        if qualified:
            self.fallback_label(fmt, lst.family, ilvl, "list item", first.line)
        parent_des = self.desig.get(parent, []) if parent is not None else []
        section_des = self.desig[section.unit] if section is not None and section.unit is not None else []
        for index, item in enumerate(lst.items):
            assert item.unit is not None and item.number is not None
            self.parent[item.unit] = item.parent_unit
            piece = fill_tokens(fmt.ref, n=[Count(item.unit, format_counter(item.number, fmt.counter))],
                                section=section_des)
            self.desig[item.unit] = extend_tokens(parent_des, piece, textual=self.textual)
            item_cause = cause if index == 0 else ()
            if lst.depth > 8 and "clamped" not in item_cause:
                item_cause += ("clamped",)
            if qualified:
                item_cause += ("label-fallback",)
            # Another list used Word's counter of this level in between: restart it where the resolver is.
            if index and item.shares_number_with is None and self.sim.peek(num.id, ilvl) != item.number:
                num = self.new_num(abstract, ilvl, item.number)
                item_cause += ("resync",)
                self.shared_counter(
                    "Word's counter for a level of this list was also used by another list in between; the numbers "
                    "are kept as the renderer shows them by restarting at a fixed number, which will not follow "
                    "items added or removed before it.", item.line)
            step = self.sim.step(num.id, ilvl, item.unit)
            if any(isinstance(token, Count) and isinstance(token.owner, Ghost) for token in step.tokens):
                # The label shows a level of its list that nothing numbers here: Word shows it as 0, and the
                # resolver's label is that of the enclosing item of another kind. Not left to Word's reading.
                item_cause += ("label-fallback",)
                self.report("render-docx-section-label", "warning",
                            f"The label '{printable(fmt.label)}' of list items shows the number of an enclosing item "
                            f"that Word cannot show here (the list is not nested in one of its own kind): in Word "
                            f"it reads '{printable(step.label)}'.", item.line)
            self.add(item, "item", num, ilvl, explicit=True, cause=item_cause, step=step)
            self.check(item, "list item", step, counter, ("list", first.unit), item_cause, (abstract.id, ilvl))
            self.placed[item.unit] = _Placed(num, abstract)
            for child in item.blocks:
                self.block(child, section)

    def bullets(self, lst: List, section: Section | None) -> None:
        """Bullets count nothing, so every bullet list of the document is numbered by one num."""
        abstract = self.abstract("bullet")
        if self.bullet_num is None:
            self.bullet_num = self.new_num(abstract)
        ilvl = min(lst.depth, 8)
        parent = lst.items[0].parent_unit
        for item in lst.items:
            assert item.unit is not None
            self.parent[item.unit] = item.parent_unit
            self.nodes[item.unit] = item
            self.desig[item.unit] = self.desig.get(parent, []) if parent is not None else []
            cause = ("clamped",) if lst.depth > 8 else ()
            self.units[item.unit] = UnitPlan(item.unit, "item", self.bullet_num.id, ilvl, True, None, None, "", "",
                                             None, cause, (), None)
            self.placed[item.unit] = _Placed(self.bullet_num, abstract)
            for child in item.blocks:
                self.block(child, section)


def plan_docx(tree: RenderTree, *, file: str | None = None) -> tuple[DocxPlan, list[Diagnostic]]:
    """The numbering plan of *tree* for Word, and the diagnostics only planning can produce (all
    ``render-docx-*``, in the order of their lines, naming *file*). Pure: the same tree gives an equal plan, and
    ``tree.formats`` carries the style's numbering. A style Word's numbering cannot express (:func:`docx_style_errors`)
    is refused before rendering (ADR 0006), so it never reaches here."""
    return _Planner(tree, file).run()
