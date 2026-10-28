"""The DOCX plan: the data the planner (`resolve/docx_plan.py`, `plan_docx`) hands to the writer.

Frozen data only, no logic. The planner decides every Word-specific matter (abstractNums, nums, which paragraph
carries which number, bookmark names, reference fields and their cached results, the cached table of contents);
the writer serialises these decisions and decides nothing (docs/decisions/0003). Bookmark *ids* are not here: the
writer allocates them (`writers/docx/ooxml.Bookmarks`), the plan only names bookmarks. Names are valid for
`ooxml._BOOKMARK_RE` (at most 40 characters) and unique ignoring case.

All sequences are tuples, all mappings are plain dicts built once in document order and never changed afterwards.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from ..tree import Inline  # in the implementation: from ...tree import Inline


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
