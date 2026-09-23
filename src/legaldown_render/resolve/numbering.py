"""Counters, numbering schemes, and label templates (§13.1, §13.2)."""
from __future__ import annotations

import re

from ..style.model import LevelFormat, Numbering

_ROMAN = (
    (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
    (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
)


def format_counter(value: int, counter: str) -> str:
    """*value* (1-based) written in the *counter* style."""
    match counter:
        case "lower-alpha" | "upper-alpha":
            # Bijective base 26: a … z, aa, ab, …
            letters = ""
            while value > 0:
                value, remainder = divmod(value - 1, 26)
                letters = chr(ord("a") + remainder) + letters
            return letters.upper() if counter == "upper-alpha" else letters
        case "lower-roman" | "upper-roman" if 0 < value < 4000:
            numeral = ""
            for amount, symbol in _ROMAN:
                while value >= amount:
                    numeral += symbol
                    value -= amount
            return numeral.upper() if counter == "upper-roman" else numeral
        case _:
            return str(value)


def _level(counter: str, label: str, ref: str) -> LevelFormat:
    return LevelFormat(counter, label, ref)  # type: ignore[arg-type]


#: The built-in heading schemes of §13.1, six levels each.
SCHEMES: dict[str, tuple[LevelFormat, ...]] = {
    "decimal": (
        _level("decimal", "{path}.", "{n}"),
        *(_level("decimal", "{path}", ".{n}") for _ in range(5)),
    ),
    "legal-outline": (
        _level("upper-roman", "{n}.", "{n}"),
        _level("upper-alpha", "{n}.", ".{n}"),
        _level("decimal", "{n}.", ".{n}"),
        _level("lower-alpha", "{n}.", ".{n}"),
        _level("lower-roman", "({n})", "({n})"),
        _level("decimal", "({n})", "({n})"),
    ),
    "mixed": (
        _level("decimal", "{n}.", "{n}"),
        _level("lower-alpha", "({n})", "({n})"),
        _level("lower-roman", "({n})", "({n})"),
        _level("upper-alpha", "({n})", "({n})"),
        _level("decimal", "({n})", "({n})"),
        _level("lower-alpha", "({n})", "({n})"),
    ),
}

#: Ordered lists under ``enumeration.ordered: renumber``.
RENUMBERED = _level("decimal", "{n}.", "({n})")


def heading_levels(numbering: Numbering) -> tuple[LevelFormat, ...]:
    """The six level formats for *numbering*: the style's ``levels`` first,
    the scheme's for the rest."""
    preset = SCHEMES.get(numbering.scheme, SCHEMES["decimal"])
    return tuple(numbering.levels[:6]) + preset[len(numbering.levels[:6]):]


_FIELD_RE = re.compile(r"\{(n|path|section)\}")


def fill(template: str, **values: str) -> str:
    """*template* with ``{n}``, ``{path}``, and ``{section}`` replaced. Any
    other braces are kept as written."""
    return _FIELD_RE.sub(lambda match: values.get(match.group(1), ""), template)


def extend(designation: str, piece: str, *, textual: bool) -> str:
    """*designation* followed by *piece*. Under the ``none`` scheme the
    designation is heading text, so the piece is set off by a space
    ("Termination (a)", §13.3)."""
    if not designation:
        return piece.lstrip(".")
    if textual:
        return f"{designation} {piece.lstrip('.')}"
    return designation + piece
