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
from typing import Any

from legaldown import Diagnostic, Document


def key_line(document: Document, *path: Any) -> int | None:
    """The line in the document's file of the frontmatter value at *path*
    (``key_line(document, "attachments", 0, "file")``), or of the nearest key
    above it that is written, as ``SourceMap.key`` says; None when the document
    has no source to name a line of (it was built from a dict, or changed since
    it was parsed) or has no frontmatter."""
    source_map = document.source_map
    if source_map is None or not source_map.fits(document):
        return None
    line = source_map.key(*path)
    return line if isinstance(line, int) else None


def repeats(found: Diagnostic, known: Iterable[Diagnostic]) -> bool:
    """True if *known* holds *found*'s finding already: the same rule, level
    and message, wherever it was. The same finding in two places is reported
    once, at the first (a link to the same refused URL on two lines is one
    ``render-link-refused``, at the line of the first)."""
    return any(d.rule == found.rule and d.level == found.level and d.message == found.message for d in known)


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
