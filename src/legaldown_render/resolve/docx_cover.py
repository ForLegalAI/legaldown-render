"""The designation cover: how a cross-reference is built from Word's ``REF`` fields (docs/decisions/0008, §6.3, §13.3).

A reference shows the designation of its target: the number of the section, the item or the paragraph written
with the ``ref`` templates of every unit on the way (``1.1.1``, ``I.A.1.a(i)``, ``1(b)(ii)``). In Word every part of
it that Word can compute must be a field, so that it stays right when a clause is inserted: ``REF <bookmark> \\n``
returns the number Word draws for the bookmarked paragraph (its whole ``lvlText``, trailing periods dropped), and
nothing else Word could know may be written as literal text. This module finds, for one designation, the
combination of such fields and literal delimiters that reads exactly like it.

Everything here works on token strings (:mod:`.docx_numbering`): the designation the renderer wrote, and what each
available field returns, are lists of :class:`~.docx_numbering.Lit` (text that is not a counter) and
:class:`~.docx_numbering.Count` (the counter of one owner, as formatted); a :class:`Title` is the text of a heading
under the ``none`` scheme (§13.3), the one thing besides counters a designation can hold that Word keeps up to date
(``REF <bookmark> \\h`` returns the bookmarked text). The designation is cut into **atoms** (one per character of
literal text, one per counter or title), a field is a sequence of atoms too, and a field **fits** at a position when
every atom of it equals the designation's there: characters equal, counters and titles of the same owner (their
text may differ: the field is then structurally right and its cached result is what Word will compute, which the
planner reports). A literal may stand only for characters of literal text, never for a counter or a title. Among the
covers the one with the fewest fields wins, then the fewest fields using ``\\t``, then the fewest literal
characters; of equal ones the first in the order the fields were given.

``\\t`` (Word only; LibreOffice ignores it) suppresses the text around the counters of a number. It is modelled only
where its result is certain: when every literal of the field is text to drop (``Article I`` gives ``I``,
``§ 3`` gives ``3``), and not when one of them is a delimiter (``(a)``, ``a)``, ``1.2``), where what Word does
is not known (:func:`suppressed`).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from .docx_numbering import Count, Lit, Token

#: The characters Word keeps when ``\t`` drops the text of a number: a field whose literal text holds one of them
#: has no ``\t`` form (what Word would make of it is not known).
DELIMITERS = frozenset(".,;:()[]/-–")


class Title(NamedTuple):
    """The text of the heading of *owner* (a unit id), as the ``none`` scheme designates a section with it."""

    owner: object
    text: str


Shown = Token | Title


class Field(NamedTuple):
    """A ``REF`` field that can stand in a designation: the bookmark of unit *owner*, the switches that decide what it
    returns (*number*: ``\\n``, else the bookmarked text; *suppress_text*: ``\\t``), and what it returns as tokens
    (Word's value, which is its cached result)."""

    owner: str
    number: bool
    suppress_text: bool
    tokens: tuple[Shown, ...]


def text_of(tokens: Sequence[Shown]) -> str:
    return "".join(token.text for token in tokens)


def atoms(tokens: Sequence[Shown]) -> list[tuple[str, object]]:
    """*tokens* as atoms: ``("d", character)`` for each character of literal text, ``("c", owner)`` for a counter and
    ``("t", owner)`` for a title."""
    out: list[tuple[str, object]] = []
    for token in tokens:
        if isinstance(token, Lit):
            out.extend(("d", character) for character in token.text)
        elif isinstance(token, Count):
            out.append(("c", token.owner))
        else:
            out.append(("t", token.owner))
    return out


def suppressed(tokens: Sequence[Shown]) -> tuple[Shown, ...] | None:
    """What ``REF \\n \\t`` returns for a field that returns *tokens* with ``\\n``: the counters alone, when every
    literal text between them is text Word drops; None when one holds a delimiter or there is no counter, since what
    Word keeps is then not known."""
    kept: list[Shown] = []
    for token in tokens:
        if isinstance(token, Lit):
            if any(character in DELIMITERS for character in token.text):
                return None
        else:
            kept.append(token)
    return tuple(kept) if kept else None


def _fits(wanted: Sequence[tuple[str, object]], at: int, field: Sequence[tuple[str, object]]) -> bool:
    """True if the atoms of a field equal those of the designation from *at* on. Atoms are equal when they are the
    same kind and owner or character: the *text* of a counter is not compared (that is the cached result's business)."""
    return at + len(field) <= len(wanted) and all(wanted[at + k] == atom for k, atom in enumerate(field))


class _Cost(NamedTuple):
    fields: int
    suppressing: int
    literal: int


def cover(designation: Sequence[Shown], fields: Sequence[Field]) -> list[str | Field] | None:
    """The cheapest cover of *designation* by *fields* and literal text, as literal strings and fields in order,
    or None when there is none (a counter or a title no field stands for, or text around a counter that no field
    drops)."""
    wanted = atoms(designation)
    shapes = [atoms(field.tokens) for field in fields]
    size = len(wanted)
    best: list[tuple[_Cost, list[str | Field]] | None] = [None] * (size + 1)
    best[size] = (_Cost(0, 0, 0), [])
    for at in range(size - 1, -1, -1):
        options: list[tuple[_Cost, list[str | Field]]] = []
        later = best[at + 1]
        if wanted[at][0] == "d" and later is not None:
            cost, rest = later
            options.append((_Cost(cost.fields, cost.suppressing, cost.literal + 1), [str(wanted[at][1]), *rest]))
        for field, shape in zip(fields, shapes, strict=True):
            if shape and shape[0] == wanted[at] and _fits(wanted, at, shape):
                onward = best[at + len(shape)]
                if onward is not None:
                    cost, rest = onward
                    options.append((_Cost(cost.fields + 1, cost.suppressing + field.suppress_text, cost.literal),
                                    [field, *rest]))
        if options:
            best[at] = min(options, key=lambda option: option[0])
    if best[0] is None:
        return None
    merged: list[str | Field] = []
    for piece in best[0][1]:
        if merged and isinstance(piece, str) and isinstance(merged[-1], str):
            merged[-1] += piece
        else:
            merged.append(piece)
    return merged


def uncovered(designation: Sequence[Shown], fields: Sequence[Field]) -> int | None:
    """Why :func:`cover` finds none: the index (among the atoms of *designation*) of the last atom that a cover of what
    comes before it reaches and that neither a literal nor a field can stand for; None when there is a cover."""
    wanted = atoms(designation)
    shapes = [atoms(field.tokens) for field in fields]
    size = len(wanted)
    onward = [False] * (size + 1)
    onward[size] = True
    steps: list[list[int]] = [[] for _ in range(size)]
    for at in range(size - 1, -1, -1):
        if wanted[at][0] == "d":
            steps[at].append(at + 1)
        steps[at] += [at + len(shape) for shape in shapes if shape and shape[0] == wanted[at] and _fits(wanted, at, shape)]
        onward[at] = any(onward[after] for after in steps[at])
    if onward[0]:
        return None
    reached = {0}
    for at in range(size):
        if at in reached:
            reached.update(steps[at])
    return max(at for at in reached if at < size and not onward[at])
