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
from legaldown.definitions import block_fragments, text_fragments
from legaldown.directives import Lexed, lex
from legaldown.validator.core import _frontmatter_fields
from legaldown.validator.templates import block_quotes
from legaldown.validator.units import FoundMarker, find_markers

__all__ = ["PlacedMarkers", "block_fragments", "block_quotes", "lex", "placed_markers"]


@dataclass(frozen=True, slots=True)
class PlacedMarkers:
    """The validator's reading of a document's markers (§5.7, §15.3)."""

    #: Whether the document is a template (§15.1), decided by the
    #: validator's own formula.
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
    template = _is_template(document, markers, lexer)
    placed = {
        (found.section, found.block, found.fragment): found
        for found in markers
        if found.marker is not None and found.placed(template)
    }
    return PlacedMarkers(template, placed, lexer)


def _is_template(document: Document, markers: list[FoundMarker], lexer: Callable[[str], Lexed]) -> bool:
    """The validator's template test (legaldown.validator.core,
    validate_document), over the validator's own findings. A copy, until the
    validator exposes the decision (roadmap U3); the tests compare it with
    the value validate_document itself computes, on every document."""
    metadata = document.metadata

    def names(texts: list[str]) -> set[str]:
        return {directive.name for text in texts for directive in lexer(text or "").directives}

    return (
        metadata.questions is not None
        or any(attachment.when for attachment in metadata.attachments)
        or any(section.condition for section in document.sections)
        or any(found.marker and found.marker.condition and not found.misplaced for found in markers)
        or "choose" in names(_body_texts(document))
        or "choose" in names(_frontmatter_texts(document) + [section.title for section in document.sections])
    )


def _body_texts(document: Document) -> list[str]:
    return [text for _section, _index, block in document.iter_indexed_blocks() for text in text_fragments(block)]


def _frontmatter_texts(document: Document) -> list[str]:
    structural, values = _frontmatter_fields(document.metadata)
    return [text for _label, text in structural] + list(values)

