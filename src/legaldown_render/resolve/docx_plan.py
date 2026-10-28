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
A label that needs the section's number is numbered on the heading abstractNum's level after the section's, and a list
nested in such an item on its reserved levels (§13.2); a later alternative that shares a number (§15.8) is a ``numId 0``
paragraph whose number is a field of the unit that holds it (``UnitPlan.alt``); a unit Word would count off the
resolver's is restarted at a fixed value (``UnitPlan.ind`` is set for the units that carry an indent of their own).
:func:`docx_style_errors` is the part that runs on every DOCX render, before it (ADR 0006).

**Data model.** Frozen data only; the planner assigns bookmark *names*, never ids (the writer allocates those);
names are valid Word bookmark names of at most 40 characters and unique ignoring case. All sequences are tuples, all
mappings are plain dicts built once in document order and never changed afterwards.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, NamedTuple

from legaldown import Diagnostic

from ..diagnostics import by_line
from ..style.model import LevelFormat, Style
from ..tree import (
    Blank,
    Choice,
    Code,
    CrossRef,
    DefinedTerm,
    DraftingNote,
    Emphasis,
    FailureMarker,
    HardBreak,
    Image,
    Inline,
    Link,
    List,
    ListItem,
    NumberingFormats,
    Paragraph,
    Quote,
    RenderTree,
    Section,
    SoftBreak,
    Strong,
    TermRef,
    Text,
    Value,
    has_visible_content,
    iter_inlines,
    iter_located_body_inlines,
    iter_tree_inlines,
    plain_text,
)
from ..urls import displayed_url, printable
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
    Token,
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
    #: ``detached``: the enumerated levels with each level's own counter alone, for a list nested where the enumerated list
    #: would have to show the counter of a unit that is not on it (§13.2). ``enumerated+1`` to ``enumerated+3`` (and
    #: ``renumber+k``): Word has nine levels, so a list nested deeper takes the next abstractNum of its family, whose level
    #: *j* is the family's level of depth ``9k + j`` with its own counter alone.
    kind: Literal["headings", "enumerated", "detached", "renumber", "paragraphs", "bullet",
                  "enumerated+1", "enumerated+2", "enumerated+3", "renumber+1", "renumber+2", "renumber+3"]
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
    #: Why the unit is not on the plain path: ``resync`` (restarted at a fixed value), ``alternative`` (``numId`` 0),
    #: ``reserved-level`` (a list nested in an item on the heading list, on one of its reserved levels),
    #: ``restart-family`` / ``restart-section`` / ``restart-alternative`` / ``restart-depth`` (a nested list in a list of
    #: another kind, in an item whose number carries the section, in an alternative, past Word's nine levels: a num of
    #: its own that restarts by a fixed setting), ``label-fallback`` (the label is written without a number Word cannot
    #: show there), ``clamped`` (nested past :data:`MAX_DEPTH`, or a bullet past Word's nine levels), ``skipped-level``.
    cause: tuple[str, ...]
    #: Names to wrap around the paragraph's title/first paragraph content: the bookmark of the unit's anchor when it
    #: has one, else a hidden one when a reference field points at it, else none.
    bookmarks: tuple[str, ...]
    #: ``_Toc`` name of a heading listed in the contents.
    toc_bookmark: str | None
    #: True: *bookmarks* are points at the start of the paragraph, not around its text (no field returns that
    #: text, and a field must never stand inside the bookmark it points at).
    collapsed: bool = False
    #: Targets of the ``{{ref:}}`` in this heading's title that are written as their text, not as fields, because
    #: their fields would lead back into the title (see :meth:`_Planner.cyclic_titles`).
    literal_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RefPlan:
    """The fields of every ``{{ref:}}`` to one unit (``CrossRef.target_unit``), wherever it stands."""

    #: ``references.format`` literals around the cover of the designation.
    segments: tuple[Segment, ...]
    #: ``concat(cached) == CrossRef.text``.
    exact: bool
    #: No cover exists, so the reference is the renderer's text with no field: right when the file is opened, but
    #: fixed (an edit in Word does not change it). The plan reports each (``render-docx-reference``).
    fixed: bool = False


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
_SETTING = {"headings": "numbering.levels", "enumerated": "enumeration.levels", "detached": "enumeration.levels",
            "renumber": "enumeration.ordered", "paragraphs": "paragraphs.format"}


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
                problems.append(f"{_SETTING.get(kind.split('+')[0], kind)}: the numbering level {level.ilvl + 1} is longer than "
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
#: Word's levels in one list, and how many lists of them a nesting of items can use: a list nested deeper than nine
#: levels goes on in the next abstractNum of its family (``enumerated+1`` ...), up to this many levels in all; past them
#: the last level is used again and the lists restart by fixed values.
_LEVELS = 9
MAX_DEPTH = 4 * _LEVELS
#: Twips: the indent items stop stepping at (thirteen centimetres), so that a list nested very deeply stays on the page.
_MAX_LEFT = 13 * _STEP


def _level(ilvl: int, num_fmt: str, parts: LevelParts, *, p_style: str | None = None,
           widths: Mapping[int, str] | None = None, unwritable: list[tuple[str, int]] | None = None,
           kind: str = "", depth: int | None = None) -> LevelDef:
    """One level. Headings (*widths*: the ``numFmt`` of each level a ``%k`` names) get an indent wide enough for
    their widest label; items step one centimetre per level of nesting (*depth*, the level's own by default: a level of
    a list that goes on past Word's nine levels is nested deeper). A label with a word in it (``Article {n}``) is
    followed by a space, any other by a tab. With *unwritable*, parts a level text cannot write are noted there
    (and written as nothing) instead of raising."""
    words = any(isinstance(part, str) and any(c.isalpha() for c in part) for part in parts)
    depth = ilvl if depth is None else depth
    if widths is None:
        left, hanging = min(_STEP * (depth + 1), _MAX_LEFT), _STEP
    else:
        sample = sum(len(part) if isinstance(part, str) else len(SAMPLE_COUNTER[widths[part]]) for part in parts)
        left = hanging = max(_STEP, _CHARACTER * (sample + 2))
    if unwritable is not None and level_text_problem(parts) is not None:
        unwritable.append((kind, depth))
        parts = ()
    return LevelDef(ilvl, num_fmt, lvl_text(parts), p_style=p_style, suff="space" if words else "tab",
                    left=left, hanging=hanging)


def _neutral(ilvl: int) -> LevelDef:
    """A level nothing uses: its own counter, decimal."""
    return LevelDef(ilvl, "decimal", f"%{ilvl + 1}", left=_STEP * (ilvl + 1), hanging=_STEP)


def _own(formats: NumberingFormats, ilvl: int, depth: int, textual: bool, unwritable: list[tuple[str, int]] | None
         ) -> LevelDef:
    """Level *ilvl* of an enumerated list nested *depth* levels deep, with its own counter alone (a ``{path}`` shows
    its own ``ref`` and nothing of the units it is nested in): the levels of the detached list and of the lists that go
    on past Word's nine levels, where no unit of the list holds the levels above."""
    levels = formats.enumerated
    here = levels[depth % len(levels)]
    return _level(ilvl, NUM_FMT[here.counter],
                  level_text(here.label, [""] * ilvl + [here.ref], ilvl, section=(), textual=textual),
                  unwritable=unwritable, kind="enumerated", depth=depth)


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
        # The same levels with nothing but their own counter (a ``{path}`` shows its own ``ref`` alone): where a list is
        # nested in a unit that is not on the enumerated list, Word has no counter of the enclosing units to show.
        out["detached"] = [_own(formats, d, d, textual, unwritable) for d in range(_LEVELS)]
        for k in range(1, MAX_DEPTH // _LEVELS):
            out[f"enumerated+{k}"] = [_own(formats, d, _LEVELS * k + d, textual, unwritable) for d in range(_LEVELS)]
    if formats.renumber is not None:
        f = formats.renumber
        for k in range(MAX_DEPTH // _LEVELS):
            out["renumber" if k == 0 else f"renumber+{k}"] = [
                _level(d, NUM_FMT[f.counter], level_text(f.label, [f.ref] * 9, d, section=(), textual=textual),
                       unwritable=unwritable, kind="renumber", depth=_LEVELS * k + d) for d in range(_LEVELS)]
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


def _shown(inlines: tuple[Inline, ...], ref_text: Callable[[CrossRef], str] | None = None) -> str:
    """What the runs of *inlines* show in the document (``writers/docx/runs.py``): the text of each, a hard break as a
    line break, a blank with its prompt. A heading's bookmark returns this, which is not always the heading's
    designation (white space is collapsed in that). A cross-reference shows what *ref_text* says its fields give (its
    own text by default)."""
    out: list[str] = []
    for inline in inlines:
        match inline:
            case CrossRef() if ref_text is not None:
                out.append(ref_text(inline))
            case Text(text=text) | Code(text=text) | CrossRef(text=text) | TermRef(text=text) | Value(text=text) \
                    | FailureMarker(text=text):
                out.append(text)
            case SoftBreak():
                out.append(" ")
            case HardBreak():
                out.append("\n")
            case Blank(text=text, prompt=prompt):
                out.append(text + (f" ({prompt})" if prompt else ""))
            case Choice(options=options, separator=separator):
                out.append("[" + separator.join(options) + "]")
            case Image(children=children):
                out.append(plain_text(children))
            case Link(href=href, children=children):
                out.append(_shown(children, ref_text) if has_visible_content(children) else displayed_url(href))
            case Emphasis(children=children) | Strong(children=children) | DefinedTerm(children=children):
                out.append(_shown(children, ref_text))
    return "".join(out)


def _components(successors: Mapping[str, Sequence[str]]) -> tuple[dict[str, int], list[list[str]]]:
    """The strongly connected components of a directed graph (Tarjan, iterative, so that a chain of any length is
    fine): the component of each node, and the components in the order they are completed, which is every node's
    successors first."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    component: dict[str, int] = {}
    order: list[list[str]] = []
    nodes = list(dict.fromkeys([*successors, *(n for out in successors.values() for n in out)]))
    for root in nodes:
        if root in index:
            continue
        index[root] = low[root] = len(index)
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(successors.get(root, ())))]
        while work:
            node, following = work[-1]
            for nxt in following:
                if nxt not in index:
                    index[nxt] = low[nxt] = len(index)
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(successors.get(nxt, ()))))
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    members = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component[member] = len(order)
                        members.append(member)
                        if member == node:
                            break
                    order.append(members)
    return component, order


_Unit = Section | ListItem | Paragraph


class _Abstract:
    """An abstractNum being built: the simulator reads its levels while the plan grows."""

    def __init__(self, abstract_id: int, kind: str, levels: list[LevelDef]) -> None:
        self.id, self.kind, self.levels = abstract_id, kind, levels


class _Placed(NamedTuple):
    """Where a list item is numbered, for the lists nested in it."""

    num: Num
    abstract: _Abstract


class _Paragraphs(NamedTuple):
    """Where the numbered paragraphs of one section go: their num, the abstractNum and level it is on, and whether
    their label is the foreign-free one for want of a place where Word can show the section."""

    num: Num
    abstract: _Abstract
    ilvl: int
    fallback: bool


class _Alternative:
    """A later alternative that shares a number, for the sections under it: they restart in a num of their own, made
    when the first of them is numbered."""

    def __init__(self, level: int) -> None:
        self.level = level
        self.num: Num | None = None


#: The owner of the counter of the unit a level text is being made for.
_SELF = object()

#: What the list of a nested list that restarts by a fixed setting is called (``UnitPlan.cause``), and what is reported
#: about it (information: Word's own "Restart at 1", which cannot be off when the document is opened).
_RESTARTS: dict[str, tuple[tuple[str, ...], str]] = {
    "restart-family": (("restart-family",), "A list nested in a list of another kind restarts by a fixed setting (Restart "
                       "at 1), not by structure: an item added above it in Word will not restart it."),
    "restart-section": (("restart-section",), "A list nested in an item whose number carries the section's cannot "
                        "restart by structure here: it restarts by a fixed setting (Restart at 1), and an item "
                        "added above it in Word will not restart it."),
    "restart-alternative": (("restart-alternative",), "A list nested in an alternative that shares a number restarts by a "
                            "fixed setting (Restart at 1), not by structure: an item added above it in Word will not "
                            "restart it."),
    "resync": (("resync",), "A list that Word would continue from another list in the same item restarts by a fixed "
               "setting (Restart at 1), not by structure."),
    "restart-depth": (("restart-depth",), "A list nested past Word's nine levels goes on in a list of its own, which "
                      "restarts by a fixed setting (Restart at 1), not by structure: an item added above it in Word will "
                      "not restart it."),
}


class _Reference(NamedTuple):
    """The ``{{ref:}}`` to one unit that the plan builds fields for: the unit, its section, what the renderer shows and
    the first line it stands at (None outside the body)."""

    target: str
    section: str | None
    text: str
    line: int | None


class _Built(NamedTuple):
    """What the plan makes of one reference: its pieces (literal text and fields), whether they read exactly as the
    renderer's text, what LibreOffice shows for them when that is not what Word shows (a field with ``\\t``), else
    None, and whether it is fixed text for want of a cover."""

    pieces: list[str | Field]
    exact: bool
    lo_text: str | None
    fixed: bool


class _Finding(NamedTuple):
    """A reference that is fixed text (*kind* ``fixed``, *reason* why) or reads differently in Word (``differs``)."""

    kind: str
    reason: str
    found: _Reference
    shown: str


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
        self.paragraph_nums: dict[str, _Paragraphs] = {}
        #: A later alternative that shares a number -> the unit it repeats and the suffix of its level; and every such
        #: unit (items and paragraphs too) -> the unit that holds its number, for designations and fields.
        self.alts: dict[str, tuple[str, Literal["tab", "space"]]] = {}
        self.canon: dict[str, str] = {}
        self.alternatives: list[_Alternative] = []
        #: The levels of the heading abstractNum a unit other than a heading has defined, and the units on it that
        #: Word would number off the resolver's (reported once).
        self.claimed: set[int] = set()
        self.collisions: list[tuple[_Unit, str]] = []
        #: Items and paragraphs, and where the text of each on the heading list starts (twips), for its nested items.
        self.item_units: set[str] = set()
        self.text_left: dict[str, int] = {}
        #: The level after the sections' fifth is kept for units of a level-5 section when the document has one and
        #: a label that needs the section.
        self.reserve5 = False
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
        #: What a heading's bookmark returns (``REF bookmark \\h``): its title as the runs show it.
        self.heading_text: dict[str, str] = {}
        self.unreferable: list[_Finding] = []

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
                fields.append(Field(owner, False, False, (Title(owner, self.heading_text.get(owner, token.text)),)))
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

    def reference(self, found: _Reference) -> _Built | None:
        """The fields of the reference *found*: the whole text it shows (the designation inside the text of
        ``references.format``) covered by fields and delimiters, so that a field may stand for the words of the format
        when the number it returns holds the same words (``Article {designation}`` and a label ``Article {n}``: one
        plain ``REF``, no ``\\t``); None for a target the plan knows nothing of. A designation that cannot be covered is
        written as the renderer's text, which is right at open and not a field (``fixed``), and noted for the
        report, as is a cover that reads differently in Word for a reason no diagnostic of a unit already gives."""
        node = self.nodes.get(found.target)
        if node is None or found.target not in self.units:
            return None
        numbered = isinstance(node, Section) or node.number is not None
        designation = self.desig[found.target] if numbered else self.desig.get(found.section or "", [])
        fields = self.fields(designation)
        whole: list[Shown] = []
        parts = self.formats.reference.split("{designation}")
        for index, literal in enumerate(parts):
            if literal:
                whole.append(Lit(literal))
            if index < len(parts) - 1:
                whole.extend(designation)
        pieces = cover(whole, fields)
        if pieces is None:
            self.unreferable.append(_Finding("fixed", self.why_uncovered(designation, fields), found, found.text))
            return _Built([found.text] if found.text else [], True, None, True)
        shown = "".join(piece if isinstance(piece, str) else text_of(piece.tokens) for piece in pieces)
        if shown != found.text:
            chain = {t.owner for t in designation if not isinstance(t, Lit) and isinstance(t.owner, str)}
            if not chain & self.flagged and found.target not in self.flagged:
                self.unreferable.append(_Finding("differs", "", found, shown))
        lo_text = None
        if any(isinstance(piece, Field) and piece.suppress_text for piece in pieces):
            lo_text = "".join(piece if isinstance(piece, str) else text_of(self.words(piece.owner) if piece.number
                                                                           else piece.tokens) for piece in pieces)
        return _Built(pieces, shown == found.text, lo_text, False)

    def report_references(self) -> None:
        """One finding for the references that fail in the same way (the first, by line, and how many more), as
        for a run of units counted off by the same amount."""
        groups: dict[tuple[str, str], list[_Finding]] = {}
        for finding in self.unreferable:
            groups.setdefault((finding.kind, finding.reason), []).append(finding)
        for (kind, reason), members in groups.items():
            members.sort(key=lambda m: (m.found.line is None, m.found.line or 0))
            first = members[0]
            text, shown = printable(first.found.text), printable(first.shown)
            more = f" {len(members) - 1} more cross-references fail in the same way." if len(members) > 1 else ""
            if kind == "fixed":
                message = (f"The cross-reference '{text}' cannot be built from Word's numbering fields: {reason}. It is "
                           f"written as the text '{text}', which is right now but is not a field: it will not follow "
                           f"edits made in Word.{more}")
            else:
                message = (f"The cross-reference '{text}' will read '{shown}' in Word: a part of it is numbered or "
                           f"written differently there.{more}")
            self.report("render-docx-reference", "warning", message, first.found.line)

    def cyclic_titles(self, built: dict[str, _Built]) -> tuple[dict[str, tuple[str, ...]], list[str]]:
        """The cross-references in a heading's title that must be text, by heading unit, and the headings in the
        order their texts can be worked out (those a title's fields return first). Under the ``none`` scheme a heading
        is designated by its text, and a field for it (``REF bookmark \\h``) returns what its bookmark holds; a
        reference in the title that leads back to the heading (directly, or through the titles of other headings)
        would be a field inside the bookmark it points at, which Word cannot show (a self-reference) and LibreOffice
        expands without end. Such a reference is written as its text and reported. The headings are the nodes of a
        graph, an edge for each heading whose text a field in another's title returns; a reference in a title is
        cyclic exactly when such a field's heading is in the title's own strongly connected component (the heading
        itself included)."""
        if not any(not p.number for made in built.values() for p in made.pieces if isinstance(p, Field)):
            return {}, []  # no field returns the text of a heading: nothing can lead back into one
        inside: dict[str, list[str]] = {}
        for section in self.tree.sections:
            if section.unit is not None:
                targets = [i.target_unit for i in iter_inlines(section.title)
                           if isinstance(i, CrossRef) and i.target_unit in built]
                inside[section.unit] = list(dict.fromkeys(t for t in targets if t is not None))
        owners = {target: tuple(dict.fromkeys(p.owner for p in made.pieces if isinstance(p, Field) and not p.number))
                  for target, made in built.items()}
        component, completed = _components({heading: list(dict.fromkeys(o for t in targets for o in owners[t]))
                                            for heading, targets in inside.items()})
        found: dict[str, tuple[str, ...]] = {}
        for heading, targets in inside.items():
            cyclic = tuple(t for t in targets if any(component[o] == component[heading] for o in owners[t]))
            if cyclic:
                found[heading] = cyclic
        return found, [heading for members in completed for heading in members]

    def heading_texts(self, built: dict[str, _Built], cyclic: Mapping[str, tuple[str, ...]], order: list[str]) -> None:
        """What a field that returns a heading's text gives, worked out from the runs the heading is written with
        (§13.3): a reference in the title is the text of the fields it is written as (which return the text of other
        headings, worked out first), or its own text where it is written as text."""
        sections = {section.unit: section for section in self.tree.sections if section.unit is not None}
        for heading in order:
            section = sections.get(heading)
            if section is None:
                continue

            def shown(ref: CrossRef, heading: str = heading) -> str:
                made = built.get(ref.target_unit or "")
                if made is None or ref.target_unit in cyclic.get(heading, ()):
                    return ref.text
                return "".join(piece if isinstance(piece, str) else
                               text_of(piece.tokens) if piece.number else self.heading_text[piece.owner]
                               for piece in made.pieces)

            self.heading_text[heading] = _shown(section.title, shown)

    def references(self, found: Mapping[str, _Reference]) -> dict[str, _Built]:
        built: dict[str, _Built] = {}
        for target, reference in found.items():
            made = self.reference(reference)
            if made is not None:
                built[target] = made
        return built

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
        if found:
            for inline, line in iter_located_body_inlines(self.tree):
                if isinstance(inline, CrossRef) and inline.target_unit in found and line is not None:
                    known = found[inline.target_unit]
                    if known.line is None or line < known.line:
                        found[inline.target_unit] = known._replace(line=line)
        built = self.references(found)
        cyclic, order = self.cyclic_titles(built)
        if order:
            # a heading's text is made of the fields in its title: the first pass found which are text and in what
            # order the texts can be worked out, and the references are made again from them
            self.heading_texts(built, cyclic, order)
            self.unreferable = []
            built = self.references(found)
        self.report_references()
        needed = {piece.owner for made in built.values() for piece in made.pieces if isinstance(piece, Field)}
        needed |= {holder for holder, _ in self.alts.values()}
        #: A bookmark holds its paragraph's text only if a field returns that text; any other is a point at the start
        #: of the paragraph, so that no field ever stands inside the bookmark it points at.
        enclosing = {piece.owner for made in built.values() for piece in made.pieces
                     if isinstance(piece, Field) and not piece.number}
        word_only = [(found[target].text, made.lo_text) for target, made in built.items() if made.lo_text is not None]
        if word_only:
            first, lo = word_only[0]
            more = f"; so do {len(word_only) - 1} more" if len(word_only) > 1 else ""
            self.report("render-docx-reference-word-only", "info",
                        f"The cross-reference '{printable(first)}' leaves out the words of a number with Word's \\t "
                        f"switch, which LibreOffice ignores: there it reads '{printable(lo)}'{more}.", None)
        for section in self.tree.sections:
            if section.unit in cyclic:
                where = f"line {section.line}" if section.line is not None else f"section {section.unit}"
                self.report("render-docx-reference-cycle", "warning",
                            f"The heading '{printable(plain_text(section.title)[:40])}' ({where}) holds a "
                            f"cross-reference that leads back to the heading itself, which a field cannot show (the heading's text is what "
                            f"the reference returns): it is written as text, so it will not follow edits made in Word.",
                            section.line)

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
            units[unit] = replace(units[unit], bookmarks=(name,), collapsed=unit not in enclosing)
        for unit, targets in cyclic.items():
            units[unit] = replace(units[unit], literal_refs=targets)
        for unit, (holder, alt_suff) in self.alts.items():
            # The number of a later alternative is the number of the unit it repeats (§15.8).
            shared = self.units[holder]
            tail = shared.label[len(shared.word_ref):]
            alt_segments: list[Segment] = [
                FieldSeg(bookmark[holder], True, False, True, shared.word_ref, shared.word_ref)]
            units[unit] = replace(units[unit], alt=AltNumber(
                (*alt_segments, *([Literal_(tail)] if tail else [])), alt_suff))
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
            refs[target] = RefPlan(tuple(segments), made.exact, made.fixed)

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
            formats = [f for f in (*self.formats.enumerated[:1], self.formats.paragraphs) if f is not None]
            self.reserve5 = any(section.level == 5 for section in self.tree.sections) and any(
                shows_section(f.label, [f.ref], 0) for f in formats)
        for block in self.tree.preamble:
            self.block(block, None)
        for section in self.tree.sections:
            self.section(section)
            for block in section.blocks:
                self.block(block, section)
        for drift in self.drifts:
            self.report_drift(drift)
        self.report_collisions()
        self.report_alternatives()
        units, anchors, refs, toc = self.finish()
        abstracts = tuple(
            AbstractNum(a.id, a.kind, f"{0x4C440000 + a.id:08X}",  # type: ignore[arg-type]
                        "multilevel" if a.kind == "headings" else "hybridMultilevel", tuple(a.levels))
            for a in self.abstracts)
        heading_id = self.heading_num.id if self.heading_num else None
        return (DocxPlan(abstracts, tuple(self.nums), heading_id, units=units, anchors=anchors, refs=refs, toc=toc),
                by_line(self.diagnostics))

    def block(self, block: object, section: Section | None, quoted: bool = False) -> None:
        """Plan *block*; *quoted*: it stands in a quote or a drafting note (§13.2), whose items are numbered like any
        other but, being built on the section whatever they are nested in, never ride the heading list."""
        match block:
            case Paragraph(unit=str()) if section is not None:
                self.paragraph(block, section)
            case List():
                self.list_(block, section, quoted)
            case Quote(blocks=blocks) | DraftingNote(blocks=blocks):
                for child in blocks:
                    self.block(child, section, True)

    # -- what a unit gets -----------------------------------------------------------

    def add(self, node: _Unit, kind: Literal["section", "item", "paragraph"], num: Num | None, ilvl: int | None, *,
            explicit: bool, cause: tuple[str, ...] = (), step: Step | None = None,
            ind: tuple[int, int] | None = None, holder: str | None = None) -> None:
        """Record the plan of *node*. A later alternative (*holder*: the unit whose number it repeats) takes the
        label of its holder and ``numId`` 0; its number is written as fields by :meth:`finish`."""
        assert node.unit is not None
        self.nodes[node.unit] = node
        num_id = num.id if num else None
        label = word_ref = ""
        count = None
        if step is not None:
            self.steps[node.unit] = step
            label, word_ref, count = step.label, step.word_ref, step.count
        if holder is not None:
            num_id, label, word_ref = 0, self.units[holder].label, self.units[holder].word_ref
        self.units[node.unit] = UnitPlan(node.unit, kind, num_id, ilvl, explicit, ind, None, label, word_ref, count,
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

    def detaches(self, depth: int, parent: str | None, placed: _Placed | None) -> bool:
        """True if a list of the enumerated family at *depth* in *parent* goes on the detached abstractNum: it is nested
        in an item on that abstractNum (its structure restarts it), or the enumerated level it would take names a
        level that no unit of its enclosing chain holds, so that Word could show the counter of nothing, or of what
        another list counted last. (The same condition as :meth:`numbered` reports a label whose counter Word cannot
        show; here it is met before the list is placed.)"""
        if placed is not None and placed.abstract.kind == "detached":
            return True
        named = {part for part in split_level_text(self.definitions["enumerated"][depth].lvl_text)
                 if isinstance(part, int) and part != depth}
        enumerated = self.by_kind.get("enumerated")
        held: set[int] = set()
        above = parent
        while above is not None:
            unit = self.canon.get(above, above)
            on = self.placed.get(unit)
            if enumerated is not None and on is not None and on.abstract is enumerated \
                    and self.units[unit].num_id not in (None, 0):
                held.add(self.units[unit].ilvl)  # type: ignore[arg-type]
            above = self.parent.get(above)
        return not named <= held

    def lost_counters(self, fmt: LevelFormat, section: Section | None, parent: str | None) -> list[object]:
        """The owners of the counters the resolver's label of a unit of *fmt* shows besides its own, in order: what the
        detached abstractNum leaves out."""
        owners = [t.owner for t in self.label_tokens(fmt, section, parent)
                  if isinstance(t, Count | Title) and t.owner is not _SELF]  # a Title: the heading's text, under ``none``
        return list(dict.fromkeys(owners))

    def describe(self, owners: Sequence[object]) -> str:
        """What the units that hold *owners* are, for a message: the section, an enclosing item, an enclosing ordered
        item."""
        names: list[str] = []
        if any(isinstance(owner, Ghost) or owner not in self.units or self.units[owner].kind == "section"  # type: ignore[index]
               for owner in owners):
            names.append("section")
        kinds = [self.placed[owner].abstract.kind.split("+")[0]  # type: ignore[index]
                 for owner in owners if owner in self.placed]
        if "renumber" in kinds:
            names.append("ordered item")
        if any(kind != "renumber" for kind in kinds):
            names.append("item")
        if not names:
            return "an enclosing unit"
        return "the enclosing " + (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0])

    def detached_label(self, fmt: LevelFormat, abstract: _Abstract, ilvl: int, kind: str, lost: Sequence[object],
                       placed: _Placed | None, first: ListItem) -> None:
        """Report a list on an abstractNum that shows each level's own counter alone (the detached one, or the next of
        its family past Word's nine levels) although the resolver's label shows the number of an enclosing unit: Word
        draws the unit's own counter, and a reference to it is still made of fields."""
        levels = abstract.levels
        if abstract.kind != "detached":
            why = "the list is nested past Word's nine levels and goes on in a list of its own"
        elif placed is None:
            why = "the list stands outside the unit it belongs to"
        elif placed.abstract.kind == "headings":
            why = "the levels of the heading list kept for nested lists are used up or read otherwise"
        else:
            why = "the list is nested in a unit that is on another list"
        self.report("render-docx-section-label", "warning",
                    f"The label '{printable(fmt.label)}' of {kind}s shows the number of {self.describe(lost)}, "
                    f"which Word cannot show in this list ({why}): in Word it reads "
                    f"'{printable(_reads(levels[ilvl], levels))}'. A reference to such an item is still made of "
                    f"fields.", first.line)

    def shared_counter(self, message: str, line: int | None) -> None:
        self.report("render-docx-shared-counter", "warning", message, line)

    # -- units ----------------------------------------------------------------------

    def section_designation(self, section: Section) -> list[Shown]:
        """The designation of *section* as tokens, written as the resolver writes it (§13.1): the ``ref`` of every part
        of its number after the one before; a part the document skipped is a :class:`Ghost` that no unit holds. Under
        the ``none`` scheme it is the heading's text (§13.3), and empty when that is. A later alternative that shares
        a number with another holds none of its own: its part is the counter of the unit it repeats (§15.8)."""
        assert section.unit is not None
        if self.textual:
            text = section.designation or ""
            return [Title(section.unit, text)] if text else []
        assert section.number is not None
        del self.owners[section.level - 1:]
        while len(self.owners) < section.level - 1:
            self.owners.append((Ghost(section.unit, len(self.owners)), 1))
        self.owners.append((self.canon.get(section.unit, section.unit), section.number))
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
        self.heading_text[unit] = _shown(section.title)
        if self.heading_num is None or section.number is None:
            self.desig[unit] = self.section_designation(section)
            self.add(section, "section", None, None, explicit=False)
            return
        abstract = self.by_kind["headings"]
        ilvl = section.level - 1
        parent = section.parent_unit
        skipped = (parent is None and section.level > 1) or (
            parent is not None and self.section_level[parent] + 1 < section.level)
        # The sections under a later alternative restart in a num of their own: the alternative counts nothing, so
        # on the headings' num they would go on from the alternative it repeats (§15.8).
        while self.alternatives and self.alternatives[-1].level >= section.level:
            self.alternatives.pop()
        num, explicit = self.heading_num, False
        if self.alternatives:
            context = self.alternatives[-1]
            if context.num is None:
                context.num = self.new_num(abstract, ilvl, section.number)
            num, explicit = context.num, True
        self.numbered(section, "section", num, abstract, ilvl, counter=self.formats.headings[ilvl].counter,
                      group=("heading", section.level), cause=("skipped-level",) if skipped else (),
                      explicit=explicit)
        if unit in self.alts:
            self.alternatives.append(_Alternative(section.level))
        self.desig[unit] = self.section_designation(section)

    def paragraph(self, paragraph: Paragraph, section: Section) -> None:
        assert paragraph.unit is not None and section.unit is not None
        self.parent[paragraph.unit] = paragraph.parent_unit
        fmt = self.formats.paragraphs
        base = self.desig[section.unit]
        if paragraph.number is None or fmt is None:
            self.desig[paragraph.unit] = base
            self.add(paragraph, "paragraph", None, None, explicit=False)
            return
        shows = shows_section(fmt.label, [fmt.ref], 0, textual=self.textual)
        entry = self.paragraph_nums.get(section.unit)
        if entry is None:
            slot = self.qualified(fmt, section) if shows else None
            abstract, ilvl = slot or (self.abstract("paragraphs"), 0)
            entry = self.paragraph_nums[section.unit] = _Paragraphs(
                self.new_num(abstract, ilvl), abstract, ilvl, shows and slot is None)
        cause: tuple[str, ...] = ()
        if entry.fallback:
            cause = ("label-fallback",)
            self.fallback_label(fmt, "paragraphs", 0, "paragraph", paragraph.line)
        self.numbered(paragraph, "paragraph", entry.num, entry.abstract, entry.ilvl, counter=fmt.counter,
                      group=("paragraphs", section.unit), cause=cause)
        piece = fill_tokens(fmt.ref, n=[self.counted(paragraph, fmt.counter)], section=base)
        self.desig[paragraph.unit] = extend_tokens(base, piece, textual=self.textual)

    def counted(self, node: ListItem | Paragraph, counter: str) -> Count:
        """The counter token of *node* in a designation: the unit's own, or, for a later alternative, the one of the
        unit it repeats."""
        assert node.unit is not None and node.number is not None
        return Count(self.canon.get(node.unit, node.unit), format_counter(node.number, counter))

    # -- the heading list's free levels (§2.5) ----------------------------------------

    def label_tokens(self, fmt: LevelFormat, section: Section | None, parent: str | None, *, designation: bool = False
                     ) -> list[Token]:
        """The label of a unit of *fmt* directly in *parent* (a section or an item; None in the preamble), filled in as
        the resolver does (§13.2), as tokens: the counter of the unit itself is :data:`_SELF`'s, every other counter is
        that of the unit that holds it (the parts of the section's number and the items on the way). With
        *designation*, the unit's designation (§13.1) in the same form."""
        section_des = self.desig[section.unit] if section is not None and section.unit is not None else []
        own = [Count(_SELF, "")]
        path = extend_tokens(self.desig.get(parent or "", []), fill_tokens(fmt.ref, n=own, section=section_des),
                             textual=self.textual)
        return path if designation else fill_tokens(fmt.label, n=own, path=path, section=section_des)

    def reads_as_designation(self, fmt: LevelFormat, section: Section, parent: str | None) -> bool:
        """True if what Word draws for a unit of *fmt* in *parent*, the label with its trailing periods dropped (what
        ``REF \\n`` returns), is the end of the unit's designation: a field for the unit then stands for that part of
        every reference to it and to what is nested in it, with the counters of the enclosing units in it."""
        shown = self.label_tokens(fmt, section, parent)
        while shown and isinstance(shown[-1], Lit):
            stripped = shown[-1].text.rstrip(".")
            if stripped:
                shown[-1] = Lit(stripped)
                break
            shown.pop()
        wanted, mine = atoms(self.label_tokens(fmt, section, parent, designation=True)), atoms(shown)
        return len(mine) <= len(wanted) and wanted[len(wanted) - len(mine):] == mine

    def heading_level(self, owner: object) -> int | None:
        """The heading list's level the counter of *owner* is drawn at, when it is a unit of the open section that is
        numbered on the heading list (an item or a paragraph that rides it, or a nested item on one of its reserved
        levels); None for any other owner."""
        found = self.placed.get(owner) if isinstance(owner, str) else None
        if found is None or found.abstract.kind != "headings":
            return None
        unit = self.units[owner]  # type: ignore[index]
        return unit.ilvl if unit.num_id not in (None, 0) else None

    def candidate(self, fmt: LevelFormat, section: Section, parent: str | None, ilvl: int) -> LevelParts | None:
        """The level text a unit of *fmt* in *parent* would have at level *ilvl* of the heading list: its label filled
        in as the resolver does, with the counter of each part of the section's number as ``%j`` of the heading level
        that holds it, the counter of an item on the way as ``%j`` of the level that item is on, and its own counter
        at *ilvl*. None when a counter is of an owner that is not on the heading list or the text cannot be
        written."""
        assert section.unit is not None
        level_of = {owner: j for j, (owner, _) in enumerate(self.owners)}
        parts: list[str | int] = []
        for token in self.label_tokens(fmt, section, parent):
            if isinstance(token, Title):
                return None
            if isinstance(token, Count):
                j = ilvl if token.owner is _SELF else level_of.get(token.owner, self.heading_level(token.owner))
                if j is None:
                    return None
                parts.append(j)
            elif parts and isinstance(parts[-1], str):
                parts[-1] += token.text
            else:
                parts.append(token.text)
        return None if level_text_problem(tuple(parts)) is not None else tuple(parts)

    def claim(self, ilvl: int, definition: LevelDef, *, section_level: bool) -> tuple[_Abstract, int] | None:
        """The heading abstractNum at *ilvl* if the level reads as *definition*: a heading level when it is the same,
        a free level (levels 6-9) when nothing claimed it or it is the same, which defines it. The level after the
        sections' fifth, which only a level-5 section's units can use, is kept for them when the document has one."""
        abstract = self.by_kind["headings"]
        current = abstract.levels[ilvl]
        if ilvl <= 4 or ilvl in self.claimed:
            same = (current.lvl_text, current.num_fmt) == (definition.lvl_text, definition.num_fmt)
            return (abstract, ilvl) if same else None
        if ilvl == 5 and self.reserve5 and not section_level:
            return None
        abstract.levels[ilvl] = definition
        self.claimed.add(ilvl)
        return abstract, ilvl

    def heading_slot(self, fmt: LevelFormat, section: Section, parent: str | None, ilvl: int, *, section_level: bool
                     ) -> tuple[_Abstract, int] | None:
        """The heading list's level *ilvl* for the units of *fmt* in *parent*, if the label can be written there with
        counters of the heading list alone and the level is free or reads the same. A level after a section's is as wide
        as the widest of its labels, as a heading's is; a nested one steps in a centimetre from the item it is in, as
        every nested list does (a label with the path in it is longer than that, and Word moves the text to its next tab
        stop)."""
        parts = self.candidate(fmt, section, parent, ilvl)
        if parts is None:
            return None
        if not section_level and any(isinstance(part, int) and part != ilvl for part in parts) \
                and not self.reads_as_designation(fmt, section, parent):
            return None  # the counters it shows are not the ones its designation ends with: no field could stand for it
        levels = self.by_kind["headings"].levels
        definition = _level(ilvl, NUM_FMT[fmt.counter], parts,
                            widths={j: levels[j].num_fmt for j in range(9)} if section_level else None)
        return self.claim(ilvl, definition, section_level=section_level)

    def qualified(self, fmt: LevelFormat, section: Section) -> tuple[_Abstract, int] | None:
        """Where the units of *fmt* directly in *section* go when their label needs the section's number: the heading
        list's level after the section's (``%1.%2`` after a level-1 section), where Word can show the section's
        counters, if that level reads as the label does. None: the label has a foreign-free form of its own
        (:meth:`fallback_label`). Under the ``none`` scheme the section's number is its heading text."""
        if self.textual or "headings" not in self.by_kind:
            return None
        return self.heading_slot(fmt, section, section.unit, section.level, section_level=True)

    def reserved(self, fmt: LevelFormat, depth: int, section: Section, parent: str) -> tuple[_Abstract, int] | None:
        """Where a list nested *depth* levels below a unit on the heading list goes: the heading abstractNum's level
        ``4 + depth``, defined on first use. Its text is the label as the resolver writes it, with every counter it
        shows at the level of the heading list that holds it (the section's number, the item it is nested in, the
        items between): a ``{path}`` label is live, and the list restarts by structure under every new item. None when
        a counter it shows is not on the heading list, or the level reads otherwise for another list."""
        return self.heading_slot(fmt, section, parent, 4 + depth, section_level=False)

    # -- numbering a unit ---------------------------------------------------------------

    def indent(self, abstract: _Abstract, ilvl: int, unit: str, parent: str | None, *, alternative: bool
               ) -> tuple[int, int] | None:
        """The indent a unit carries itself, in twips ``(left, hanging)``. An item or paragraph on the heading abstractNum
        (whose levels indent the headings) takes the width of the heading level it stands on; one that stands in an
        item with an indent of its own, on any list, is one step in from the text of that item; a later alternative,
        which has no numbering to take an indent from, takes its level's. Any other unit takes its level's, and the
        plan sets nothing."""
        level = abstract.levels[ilvl]
        if unit in self.item_units and parent in self.text_left:
            left, hanging = min(self.text_left[parent] + _STEP, _MAX_LEFT), _STEP
        elif unit in self.item_units and abstract.kind == "headings":
            left, hanging = level.left, level.hanging
        else:
            return (level.left, level.hanging) if alternative else None
        self.text_left[unit] = left
        return left, hanging

    def numbered(self, node: _Unit, kind: Literal["section", "item", "paragraph"], num: Num, abstract: _Abstract,
                 ilvl: int, *, counter: str, group: object, cause: tuple[str, ...] = (), explicit: bool = True) -> Num:
        """Number *node* on *num* at *ilvl* of *abstract* and check it against the resolver; returns the num it is on.

        * A later alternative whose number Word would not give (§15.8) takes ``numId`` 0 and the number of the unit
          it repeats, as fields.
        * A unit whose number Word would give differently (it goes on where the resolver restarts) is put on a num
          that starts there, a fixed setting (§2.7).
        * A label that shows a number of a unit outside the unit's own ancestors, which Word shows from the levels
          of its list, is marked ``label-fallback``."""
        assert node.unit is not None and node.number is not None
        unit = node.unit
        word = {"section": "heading", "item": "list item", "paragraph": "paragraph"}[kind]
        parent = node.parent_unit
        if kind != "section":
            self.item_units.add(unit)
        peek = self.sim.peek(num.id, ilvl)
        if node.shares_number_with is not None and peek != node.number:
            # Word would count on, and the number is the one the level holds now: the unit that last counted the level
            # holds it, and the alternative shows that unit's number (a unit that shares a number but finds another
            # in the level, because a list of the heading list counted in between, is restarted below).
            count, holder = self.sim.current(num.id, ilvl)
            if count == node.number and isinstance(holder, str) and holder in self.units:
                self.canon[unit] = holder
                self.alts[unit] = (holder, abstract.levels[ilvl].suff)
                self.add(node, kind, None, ilvl, explicit=True, cause=(*cause, "alternative"), holder=holder,
                         ind=self.indent(abstract, ilvl, unit, parent, alternative=True))
                return num
        used = num
        if peek != node.number:
            used = self.new_num(abstract, ilvl, node.number)
            cause, explicit = (*cause, "resync"), True
            if abstract.kind == "headings":
                self.collisions.append((node, word))
            else:
                self.shared_counter(
                    "Word's counter for a level of this list was also used by another list in between; the numbers "
                    "are kept as the renderer shows them by restarting at a fixed number, which will not follow "
                    "items added or removed before it.", node.line)
        step = self.sim.step(used.id, ilvl, unit)
        if kind != "section":
            # The counters of the unit's own chain: the units it stands under, and every part of the number of the
            # section (a skipped heading level counts, as the ghost of the heading that skipped it).
            chain: set[object] = {t.owner for t in self.desig.get(parent or "", ()) if isinstance(t, Count)}
            above = parent
            while above is not None:
                chain.add(self.canon.get(above, above))
                above = self.parent.get(above)
            if "label-fallback" not in cause and any(
                    isinstance(t, Count) and t.owner != unit and t.owner not in chain for t in step.tokens):
                # The label shows the counter of a level of its list that no unit of its own chain holds: Word shows
                # a level nothing numbers as 0, or the counter of whatever last counted it, and the resolver's label
                # is that of the enclosing unit. Not left to Word's reading.
                cause += ("label-fallback",)
                self.report("render-docx-section-label", "warning",
                            f"The label of {word}s '{printable(node.label or '')}' shows the number of an enclosing "
                            f"item that Word cannot show here (the list is not nested directly in an item of its own "
                            f"kind): in Word it reads '{printable(step.label)}'.", node.line)
        self.add(node, kind, used, ilvl, explicit=explicit, cause=cause, step=step,
                 ind=self.indent(abstract, ilvl, unit, parent, alternative=False))
        self.check(node, word, step, counter, group, cause, (abstract.id, ilvl))
        return used

    def restart_class(self, parent: str, placed: _Placed, abstract: _Abstract, *, deeper: bool = False) -> str:
        """Why a list nested in the item *parent* does not take its num: the item is a later alternative (numbered by
        no list), the list goes on past Word's nine levels (*deeper*), the item is on the heading list but the list
        cannot be, it is on another kind of list, or Word would go on counting from another list in the same item."""
        if self.units[parent].num_id == 0:
            return "restart-alternative"
        if deeper:
            return "restart-depth"
        if placed.abstract is not abstract:
            return "restart-section" if placed.abstract.kind == "headings" else "restart-family"
        return "resync"

    def report_collisions(self) -> None:
        """One finding for the units of the heading list that Word would number off the resolver's because they share
        a counter with others of their section (§2.7): the first, and how many more. What an edit in Word does to
        the fixed value depends on the unit: a sub-heading restarted after the clauses of its section keeps it, and
        the headings after it read higher if it is deleted; a clause or paragraph does not follow what is inserted or
        deleted before it."""
        if not self.collisions:
            return
        node, word = self.collisions[0]
        assert node.unit is not None
        designation = text_of(self.desig[node.unit])
        more = f" {len(self.collisions) - 1} more units do the same." if len(self.collisions) > 1 else ""
        if word == "heading":
            effect = ("The heading keeps that value when clauses or headings are inserted before it, and if it is "
                      "deleted in Word the headings after it read higher than the renderer's.")
        else:
            effect = ("The unit keeps that value: a clause or paragraph inserted or deleted before it in Word does not "
                      "move it.")
        self.shared_counter(
            f"The {word} '{printable(node.label or '')}' has the designation {printable(designation)}, which another "
            f"unit of its section has too (in every output format their designations collide), and in Word they "
            f"share one counter. The number is kept as the renderer shows it by restarting at a fixed value. "
            f"{effect}{more}", node.line)

    def report_alternatives(self) -> None:
        """One information for the document's alternatives that share a number: their numbers are fields of the first
        alternative, which a lawyer can delete in Word (§15.8)."""
        if not self.alts:
            return
        holder = next(iter(self.alts.values()))[0]
        node = self.nodes[holder]
        where = f", line {node.line}" if node.line is not None else ""
        self.report("render-docx-alternative", "info",
                    f"{len(self.alts)} alternative(s) that share a number show it as a field of the first alternative "
                    f"('{printable(self.units[holder].label)}'{where}); deleting that paragraph in Word "
                    f"breaks their numbers and every reference to their shared identifier. Render the document "
                    f"with the condition values to produce the final one.", node.line)

    def list_(self, lst: List, section: Section | None, quoted: bool = False) -> None:
        first = lst.items[0]
        if lst.family == "bullet":
            self.bullets(lst, section, quoted)
            return
        fmt = lst.format
        assert fmt is not None
        depth = lst.depth
        # Word has nine levels in a list: a list nested deeper goes on in the next abstractNum of its family (levels
        # 10-18 in ``enumerated+1``, and so on up to MAX_DEPTH), so that it keeps restarting by structure inside that
        # abstractNum; past MAX_DEPTH the last level is used again.
        reached = min(depth, MAX_DEPTH - 1)
        chunk, rem = divmod(reached, _LEVELS)
        levels = self.formats.enumerated if lst.family == "enumerated" else (fmt,)
        counter = levels[reached % len(levels)].counter
        parent = first.parent_unit
        placed = self.placed.get(parent) if parent is not None else None
        parent_live = placed is not None and self.units[parent].num_id not in (None, 0)
        refs = ([f.ref for f in (self.formats.enumerated[j % len(self.formats.enumerated)] for j in range(depth + 1))]
                if lst.family == "enumerated" else [fmt.ref] * (depth + 1))
        shows = section is not None and shows_section(fmt.label, refs, depth, textual=self.textual)
        # A label that needs the section's number goes on the heading list's level after the section's; a list nested
        # in a unit that is on the heading list goes on its reserved levels, so that Word keeps restarting it by
        # structure, with the label's counters of the section and of the items on the way as levels of that list.
        # Otherwise the family's own abstractNum, with a label Word can write; and where that abstractNum would have to
        # show a counter of a unit that is not on it, the detached one, which shows each level's own counter alone.
        slot = None
        if lst.family == "enumerated" and section is not None and not quoted:
            if depth == 0 and placed is None:
                slot = self.qualified(fmt, section) if shows else None
            elif placed is not None and placed.abstract.kind == "headings" and 1 <= depth <= 4:
                assert parent is not None
                slot = self.reserved(fmt, depth, section, parent)
        detached = slot is None and lst.family == "enumerated" and chunk == 0 and self.detaches(depth, parent, placed)
        if slot is not None:
            abstract, ilvl = slot
        elif detached:
            abstract, ilvl = self.abstract("detached"), depth
        else:
            abstract, ilvl = self.abstract(lst.family if chunk == 0 else f"{lst.family}+{chunk}"), rem
        cause: tuple[str, ...] = ()
        # Nested in an item: that item's num, if it is live, of the same kind and Word counts the list from where the
        # resolver does (a sibling list continues, a list in a new item restarts); otherwise a num of its own.
        if (placed is not None and parent_live and placed.abstract is abstract and depth < MAX_DEPTH
                and self.sim.peek(placed.num.id, ilvl) == first.number):
            num = placed.num
        else:
            num = self.new_num(abstract, ilvl, first.number or 1)
            if depth >= MAX_DEPTH:
                cause = ("clamped",)
                if not self.depth_reported:
                    self.depth_reported = True
                    self.report("render-docx-list-depth", "warning",
                                f"A list is nested {depth} levels deep: the plan has {MAX_DEPTH} levels of Word lists. "
                                f"Items from level {MAX_DEPTH + 1} on are numbered in the format of level {MAX_DEPTH} "
                                f"and restart by fixed values.", first.line)
            elif placed is not None and first.number > 1:
                cause = ("resync",)
                self.shared_counter(
                    "A list that goes on from an earlier list in the same item (the resolver counts them as one) "
                    "starts at a fixed number in Word: it will not follow items added or removed before it.",
                    first.line)
            elif placed is not None:
                cause, message = _RESTARTS[self.restart_class(parent, placed, abstract, deeper=chunk > 0 and rem == 0)]
                self.report("render-docx-static-restart", "info", message, first.line)
        if slot is not None and abstract.kind == "headings" and depth > 0:
            cause += ("reserved-level",)
        own_only = detached or chunk > 0  # the levels of these abstractNums show each level's own counter alone
        lost = self.lost_counters(fmt, section, parent) if own_only else []
        fallback = bool(lost) if own_only else shows and slot is None
        if own_only and lost:
            self.detached_label(fmt, abstract, ilvl, "list item", lost, placed, first)
        elif fallback:
            self.fallback_label(fmt, lst.family, min(depth, 8), "list item", first.line)
        parent_des = self.desig.get(parent, []) if parent is not None else []
        section_des = self.desig[section.unit] if section is not None and section.unit is not None else []
        for index, item in enumerate(lst.items):
            assert item.unit is not None and item.number is not None
            self.parent[item.unit] = item.parent_unit
            item_cause = cause if index == 0 else ()
            if depth >= MAX_DEPTH and "clamped" not in item_cause:
                item_cause += ("clamped",)
            if slot is not None and depth > 0 and "reserved-level" not in item_cause:
                item_cause += ("reserved-level",)
            if fallback:
                item_cause += ("label-fallback",)
            used = self.numbered(item, "item", num, abstract, ilvl, counter=counter, group=("list", first.unit),
                                 cause=item_cause)
            piece = fill_tokens(fmt.ref, n=[self.counted(item, fmt.counter)], section=section_des)
            self.desig[item.unit] = extend_tokens(parent_des, piece, textual=self.textual)
            self.placed[item.unit] = _Placed(used, abstract)
            for child in item.blocks:
                self.block(child, section, quoted)

    def bullets(self, lst: List, section: Section | None, quoted: bool) -> None:
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
                self.block(child, section, quoted)


def plan_docx(tree: RenderTree, *, file: str | None = None) -> tuple[DocxPlan, list[Diagnostic]]:
    """The numbering plan of *tree* for Word, and the diagnostics only planning can produce (all
    ``render-docx-*``, in the order of their lines, naming *file*). Pure: the same tree gives an equal plan, and
    ``tree.formats`` carries the style's numbering. A style Word's numbering cannot express (:func:`docx_style_errors`)
    is refused before rendering (ADR 0006), so it never reaches here."""
    return _Planner(tree, file).run()
