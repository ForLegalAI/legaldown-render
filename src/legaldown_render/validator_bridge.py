"""Everything the renderer takes from ``legaldown-validator`` beyond its
top-level public API, in one place.

The renderer never re-derives a LegalDown rule (docs/decisions/0002): where
the validator has decided something — where a marker is placed, whether a
document is a template — the renderer asks it. Some of those answers are not
part of the validator's public API yet, so they are imported here from its
modules. This module is the complete list of such imports, the surface that
roadmap item U3 asks the validator to make public; the dependency is pinned
to one minor version until it does.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache

from legaldown import Document
from legaldown.cli import _read_answers as read_answers
from legaldown.definitions import block_fragments
from legaldown.directives import Lexed, lex
from legaldown.markdown import FENCE_OPEN_RE, closes_fence, dedent, indent_width
from legaldown.models import listed_items
from legaldown.serializer import list_runs
from legaldown.validator.core import is_template
from legaldown.validator.templates import block_quotes
from legaldown.validator.units import FoundMarker, find_markers

__all__ = [
    "FENCE_OPEN_RE",
    "PlacedMarkers",
    "block_fragments",
    "block_quotes",
    "closes_fence",
    "dedent",
    "indent_width",
    "lex",
    "list_runs",
    "listed_items",
    "placed_markers",
    "read_answers",
]


@dataclass(frozen=True, slots=True)
class PlacedMarkers:
    """The validator's reading of a document's markers (§5.7, §15.3)."""

    #: Whether the document is a template (§15.1): the validator's own
    #: decision (``is_template``), over the same markers.
    template: bool
    #: The placed markers, by (section index or None for the preamble,
    #: block index, fragment index in block_fragments(block)).
    placed: dict[tuple[int | None, int, int], FoundMarker]
    #: The lexer, cached for this one render: each text is lexed once.
    lex: Callable[[str], Lexed]


def placed_markers(document: Document) -> PlacedMarkers:
    """Which markers the validator places, and whether *document* is a
    template — the two decisions the validator makes together in
    ``validate_document`` (legaldown.validator.core, §15.1)."""
    lexer = cache(lex)
    markers = find_markers(document, lexer)
    template = is_template(document, markers, lexer)
    placed = {
        (found.section, found.block, found.fragment): found
        for found in markers
        if found.marker is not None and found.placed(template)
    }
    return PlacedMarkers(template, placed, lexer)
