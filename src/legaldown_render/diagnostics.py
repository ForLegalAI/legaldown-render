"""Where the renderer's own diagnostics point, and when one repeats another
(§16.9: a diagnostic names its file and the line of what it reports).

The validator's diagnostics carry a line, and the renderer's own do too, as
far as the validator makes lines public: the first line of the block (or the
list item, or the heading) the finding is in, ``Document.line_of``; and, for a
finding about a frontmatter value, the line of its key (:func:`key_line`).
Directives inside a block have no public line of their own (a ``{{ref:}}`` or
``{{term:}}`` paragraph is lifted by the validator and written back, so even
its text has moved), so a block's several findings share its line.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from legaldown import Diagnostic, Document


class KeyLines:
    """The lines of a document's frontmatter keys: ``KeyLines(document)(*path)``
    is the line in the document's file of the value at *path*
    (``("attachments", 0, "file")``), or of the nearest key above it that is
    written, as ``SourceMap.key`` says; None when the document has no source to
    name a line of (it was built from a dict, or changed since it was parsed)
    or has no frontmatter.

    Whether the document still fits its source map is worked out once, when
    this is made (``SourceMap.fits`` reads the whole document), so a renderer
    keeps one for the document it renders and asks it for every reference."""

    def __init__(self, document: Document) -> None:
        source_map = document.source_map
        self._map = source_map if source_map is not None and source_map.fits(document) else None

    def __call__(self, *path: Any) -> int | None:
        line = self._map.key(*path) if self._map is not None else None
        return line if isinstance(line, int) else None

    def has(self, *path: Any) -> bool:
        """True if the document's source writes the frontmatter node at *path*,
        as a key or a value (a value merged in with ``<<`` is written where it
        is defined). A node that is not written has the line of the key above
        it when asked for (``__call__``)."""
        return self._map is not None and path in self._map.keys


def key_line(document: Document, *path: Any) -> int | None:
    """``KeyLines(document)(*path)``, for a single lookup."""
    return KeyLines(document)(*path)


def repeats(found: Diagnostic, known: Iterable[Diagnostic]) -> bool:
    """True if *known* holds *found*'s finding already: the same rule, level
    and message, wherever it was."""
    return any(_same(d, found) for d in known)


def record(found: Diagnostic, known: list[Diagnostic]) -> None:
    """Add *found* to *known*, unless it holds the same finding already (the
    same rule, level and message): the finding is reported once, at the lowest
    line it was seen at (a link to the same refused URL on two lines is one
    ``render-link-refused``, at the earlier line, whichever was found first)."""
    for index, d in enumerate(known):
        if _same(d, found):
            if found.line is not None and (d.line is None or found.line < d.line):
                known[index] = replace(d, line=found.line)
            return
    known.append(found)


def by_line(found: Iterable[Diagnostic]) -> list[Diagnostic]:
    """*found* in the order of their lines, those with none last, and those at
    one line as they were."""
    return sorted(found, key=lambda d: (d.line is None, d.line or 0))


def _same(a: Diagnostic, b: Diagnostic) -> bool:
    return a.rule == b.rule and a.level == b.level and a.message == b.message


def covered_by(found: Diagnostic, path: str, validator: Iterable[Diagnostic]) -> bool:
    """True if the validator already reported *found*, a ``path-not-relative``
    about *path*: its diagnostic of the rule quotes the path (``'<path>'``) and
    is at the same line, or has none. A path the validator did not name is
    still the renderer's to report, even where the validator reported another
    at the same line, and so is the same path where it stands elsewhere."""
    quoted = f"'{path}'"
    return any(
        d.rule == found.rule and quoted in d.message
        and (d.line is None or found.line is None or d.line == found.line)
        for d in validator
    )
