"""Word's list numbering, as the DOCX planner needs it (docs/decisions/0008, §13.1, §13.2).

Two things live here, both pure:

* **Token strings.** The renderer's labels and designations, and Word's expansion of an ``lvlText``, are written as
  lists of :class:`Lit` (text that is not a counter) and :class:`Count` (the counter of one owner, as formatted). The
  functions :func:`fill_tokens` and :func:`extend_tokens` are :func:`.numbering.fill` and :func:`.numbering.extend` on
  tokens, so that a template filled here reads exactly as the resolver filled it, with the owner of every counter kept.
  :func:`level_text` derives the ``lvlText`` of a level from a label template.
* **The simulator** (:class:`WordNumbering`): what Word's numbering gives each paragraph of a list, from the
  abstractNum and num definitions alone. The planner uses it to place every unit and to check each one against the
  number the resolver gave it, and its results are the cached results of the fields Word will recompute.

The simulator models Word's list semantics (docs/architecture.md, "The DOCX planner"): counters belong to the
abstractNum and are shared by all its ``w:num``; a ``startOverride`` restarts the named level at the first use of that
num; a paragraph restarts every deeper level; ``numId 0`` paragraphs count nothing (the planner never steps them).
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal, NamedTuple

from ..errors import InternalError
from .numbering import _FIELD_RE

if TYPE_CHECKING:
    from .docx_plan import LevelDef, Num

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------

#: The renderer's counters (``style.model.Counter``) and the ``numFmt`` that writes each in Word.
NUM_FMT: dict[str, str] = {
    "decimal": "decimal",
    "lower-alpha": "lowerLetter",
    "upper-alpha": "upperLetter",
    "lower-roman": "lowerRoman",
    "upper-roman": "upperRoman",
}

_ROMAN = (
    (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
    (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
)


def word_counter(value: int, num_fmt: str) -> str:
    """*value* as Word writes it in *num_fmt*.

    Letters repeat after 26 (``aa``, ``bb``, ... ``zz``, ``aaa``), as in ECMA-376 and Word's documentation; the
    renderer counts ``aa``, ``ab`` (§13.2), so the two differ from 28 on. Roman numerals are the usual form below
    1000 and ``m`` repeated above, so that 4000 reads ``mmmm`` where the renderer writes digits. A value below 1 is
    ``0`` for ``decimal`` and empty otherwise."""
    if value < 1:
        return "0" if num_fmt == "decimal" else ""
    match num_fmt:
        case "lowerLetter" | "upperLetter":
            letters = chr(ord("a") + (value - 1) % 26) * ((value - 1) // 26 + 1)
            return letters.upper() if num_fmt == "upperLetter" else letters
        case "lowerRoman" | "upperRoman":
            numeral, rest = "m" * (value // 1000), value % 1000
            for amount, symbol in _ROMAN:
                while rest >= amount:
                    numeral += symbol
                    rest -= amount
            return numeral.upper() if num_fmt == "upperRoman" else numeral
        case _:
            return str(value)


#: How wide a counter of each ``numFmt`` is taken to be, to size a hanging indent.
SAMPLE_COUNTER: dict[str, str] = {
    "decimal": "88", "lowerLetter": "mm", "upperLetter": "MM", "lowerRoman": "viii", "upperRoman": "VIII",
}

# ---------------------------------------------------------------------------
# Token strings
# ---------------------------------------------------------------------------


class Lit(NamedTuple):
    """Text that is not a counter: delimiters, or words of a label."""

    text: str


class Count(NamedTuple):
    """The counter of *owner* (a unit id, a :class:`Ghost`, or a level number in a template), as formatted."""

    owner: object
    text: str


class Ghost(NamedTuple):
    """The counter of a skipped heading level: *level* (from 0) was never used before *unit* needed it."""

    unit: str
    level: int


Token = Lit | Count


def fill_tokens(template: str, *, n: Sequence[Token], path: Sequence[Token] = (),
                section: Sequence[Token] | None = None) -> list[Token]:
    """:func:`.numbering.fill` on tokens: *template* with ``{n}``, ``{path}`` and ``{section}`` replaced by token
    lists, other text (and other braces) kept as :class:`Lit`. *section* given and empty (a preamble item) drops
    ``{section}`` and ONE ``.`` after it; None means "not given" (headings), and ``{section}`` is then empty."""
    if section is not None and not section:
        template = re.sub(r"\{section\}\.", "", template)
    values: dict[str, Sequence[Token]] = {"n": n, "path": path, "section": section or ()}
    out: list[Token] = []
    position = 0
    for match in _FIELD_RE.finditer(template):
        if match.start() > position:
            out.append(Lit(template[position:match.start()]))
        out.extend(values[match.group(1)])
        position = match.end()
    if position < len(template):
        out.append(Lit(template[position:]))
    return out


def extend_tokens(designation: Sequence[Token], piece: Sequence[Token], *, textual: bool) -> list[Token]:
    """:func:`.numbering.extend` on tokens: *designation* followed by *piece*; the piece loses leading dots when
    nothing precedes it or under the ``none`` scheme, where a space goes before it."""
    piece = list(piece)
    if (not designation or textual) and piece and isinstance(piece[0], Lit):
        piece[0] = Lit(piece[0].text.lstrip("."))
    if not designation:
        return [token for token in piece if token != Lit("")]
    if textual:
        return [*designation, Lit(" "), *(token for token in piece if token != Lit(""))]
    return [*designation, *piece]


def text_of(tokens: Sequence[Token]) -> str:
    return "".join(token.text for token in tokens)


# ---------------------------------------------------------------------------
# lvlText
# ---------------------------------------------------------------------------

#: A level text as parts: text, and the number of the level (from 0) a ``%k`` stands for.
LevelParts = tuple[str | int, ...]

_PLACEHOLDER = re.compile(r"%([1-9])")


def level_text(label: str, refs: Sequence[str], level: int, *, section: Sequence[Token] | None,
               textual: bool = False) -> LevelParts:
    """The ``lvlText`` of level *level* of a list, from its ``label`` template, as parts.

    Only counters of the list's own levels can be shown (Word's ``%k`` names a level of the same abstractNum), so
    ``{n}`` is this level's counter and ``{path}`` the designation over levels ``0..level``, each with its own ``ref``
    template (*refs*, one per level). *section* is ``None`` for headings (``{section}`` is empty) and ``()`` for the
    foreign-free form of a list item or paragraph, the one a preamble item has: ``{section}`` and the dot after it
    are dropped, which is the documented fallback where a label needs a section number Word cannot show there."""
    designation: list[Token] = []
    for j in range(level + 1):
        piece = fill_tokens(refs[j], n=[Count(j, "")], section=section)
        designation = extend_tokens(designation, piece, textual=textual)
    tokens = fill_tokens(label, n=[Count(level, "")], path=designation, section=section)
    parts: list[str | int] = []
    for token in tokens:
        if isinstance(token, Count):
            parts.append(token.owner)  # type: ignore[arg-type]
        elif parts and isinstance(parts[-1], str):
            parts[-1] += token.text
        else:
            parts.append(token.text)
    return tuple(parts)


def level_text_problem(parts: LevelParts) -> str | None:
    """Why *parts* cannot be written as an ``lvlText``, or None. A literal ``%`` before a digit, or before a
    counter, cannot be told from a placeholder; this happens where the pieces of a label are put together (a ref
    ending in ``%`` before another that starts with a counter), not only inside one template."""
    text = "".join(f"%{part + 1}" if isinstance(part, int) else part for part in parts)
    before_counter = any(isinstance(after, int) and isinstance(part, str) and part.endswith("%")
                         for part, after in zip(parts, parts[1:], strict=False))
    if before_counter or len(_PLACEHOLDER.findall(text)) != sum(isinstance(part, int) for part in parts):
        return "a '%' meets a digit or a counter"
    return None


def lvl_text(parts: LevelParts) -> str:
    """*parts* in OOXML form: ``%k`` for level ``k-1``. Style validation (:func:`.docx_plan.docx_style_errors`)
    refuses parts with a :func:`level_text_problem` before rendering, so one reaching here is a bug."""
    if level_text_problem(parts) is not None:
        raise InternalError("a label that Word's level text cannot write reached the DOCX planner. This is a bug; "
                            "style validation refuses it.")
    return "".join(f"%{part + 1}" if isinstance(part, int) else part for part in parts)


_SECTION = "section"


def shows_section(label: str, refs: Sequence[str], level: int, *, textual: bool = False) -> bool:
    """True if the label of level *level* of a list in a section shows a counter of the section: it uses
    ``{section}``, or ``{path}`` of a designation that starts from the section's. Judged on the filled-in label
    (a counter of the section is in it), not on how the template is spelled."""
    section: list[Token] = [Count(_SECTION, "")]
    designation: list[Token] = list(section)
    for j in range(level + 1):
        designation = extend_tokens(designation, fill_tokens(refs[j], n=[Count(j, "")], section=section),
                                    textual=textual)
    tokens = fill_tokens(label, n=[Count(level, "")], path=designation, section=section)
    return any(isinstance(token, Count) and token.owner == _SECTION for token in tokens)


def split_level_text(text: str) -> list[str | int]:
    """An ``lvlText`` as literal strings and level numbers (from 0), as :func:`lvl_text` writes it."""
    parts: list[str | int] = []
    position = 0
    for match in _PLACEHOLDER.finditer(text):
        if match.start() > position:
            parts.append(text[position:match.start()])
        parts.append(int(match.group(1)) - 1)
        position = match.end()
    if position < len(text):
        parts.append(text[position:])
    return parts


# ---------------------------------------------------------------------------
# The simulator
# ---------------------------------------------------------------------------


class Step(NamedTuple):
    """What Word gives one numbered paragraph."""

    #: The counter of the paragraph's own level.
    count: int
    #: The level's own counter as written (``a``, ``IV``).
    own: str
    #: The full ``lvlText`` expansion, trailing period kept (``1.``, ``(a)``).
    label: str
    #: What ``REF <paragraph> \n`` returns: the label minus its trailing periods.
    word_ref: str
    #: The label as tokens, each counter with the unit (or ghost) that holds it.
    tokens: tuple[Token, ...]


class WordNumbering:
    """Word's counters for a set of abstractNums and nums. Register each with :meth:`add_abstract` and
    :meth:`add_num` (an abstractNum's levels are read when a paragraph is stepped, so a level may be defined
    after the abstractNum is added), then :meth:`step` paragraphs in document order.

    *skipped* is the model of a level that was never used before a deeper one: ``"lo"`` shows its start value and
    counts on (what the renderer does for a skipped heading level, and LibreOffice shows); ``"word"`` shows
    ``start - 1`` and does not advance it, which is what Word is expected to do. It is the default of every
    abstractNum and can be set for each in :meth:`add_abstract`."""

    def __init__(self, *, skipped: Literal["lo", "word"] = "lo") -> None:
        self.skipped = skipped
        self._skipped: dict[int, Literal["lo", "word"]] = {}
        self._levels: dict[int, Sequence[LevelDef]] = {}
        self._nums: dict[int, Num] = {}
        self._count: dict[int, list[int]] = {}
        self._holder: dict[int, list[object | None]] = {}
        self._used: set[tuple[int, int]] = set()

    def add_abstract(self, abstract_id: int, levels: Sequence[LevelDef], *,
                     skipped: Literal["lo", "word"] | None = None) -> None:
        self._levels[abstract_id] = levels
        self._skipped[abstract_id] = skipped or self.skipped
        self._count[abstract_id] = [0] * 9
        self._holder[abstract_id] = [None] * 9

    def add_num(self, num: Num) -> None:
        self._nums[num.id] = num

    def _overrides(self, num_id: int) -> dict[int, int]:
        return dict(self._nums[num_id].start_overrides)

    def peek(self, num_id: int, ilvl: int) -> int:
        """The number the next paragraph at (*num_id*, *ilvl*) would get."""
        num = self._nums[num_id]
        overrides = self._overrides(num_id)
        if ilvl in overrides and (num_id, ilvl) not in self._used:
            return overrides[ilvl]
        return self._count[num.abstract_id][ilvl] + 1

    def current(self, num_id: int, ilvl: int) -> tuple[int, object | None]:
        """The counter of the level now, and the unit that last counted it (None if unused)."""
        abstract_id = self._nums[num_id].abstract_id
        return self._count[abstract_id][ilvl], self._holder[abstract_id][ilvl]

    def step(self, num_id: int, ilvl: int, owner: object) -> Step:
        """Number the next paragraph at (*num_id*, *ilvl*), which *owner* (a unit id) is. ``numId`` 0 (no
        numbering) counts nothing and restarts nothing; ``REF \\n`` of such a paragraph returns ``0``."""
        if num_id == 0:
            return Step(0, "", "", "0", ())
        num = self._nums[num_id]
        levels = self._levels[num.abstract_id]
        count, holder = self._count[num.abstract_id], self._holder[num.abstract_id]
        overrides = self._overrides(num_id)
        if ilvl in overrides and (num_id, ilvl) not in self._used:
            self._used.add((num_id, ilvl))
            count[ilvl] = overrides[ilvl] - 1
        count[ilvl] += 1
        holder[ilvl] = owner
        for deeper in range(ilvl + 1, 9):
            count[deeper], holder[deeper] = 0, None
        skipped = self._skipped[num.abstract_id]
        for above in range(ilvl):
            if count[above] == 0:
                holder[above] = Ghost(str(owner), above)
                count[above] = levels[above].start if skipped == "lo" else levels[above].start - 1
        tokens: list[Token] = []
        for part in split_level_text(levels[ilvl].lvl_text):
            if isinstance(part, int):
                tokens.append(Count(holder[part], word_counter(count[part], levels[part].num_fmt)))
            else:
                tokens.append(Lit(part))
        if skipped == "word":
            # Never-used levels show start - 1 and stay unused: the next heading of that level starts at `start`.
            for above in range(ilvl):
                if isinstance(holder[above], Ghost):
                    count[above], holder[above] = 0, None
        label = text_of(tokens)
        return Step(count[ilvl], word_counter(count[ilvl], levels[ilvl].num_fmt), label,
                    label.rstrip("."), tuple(tokens))
