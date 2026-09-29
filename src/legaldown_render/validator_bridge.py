"""Everything the renderer takes from ``legaldown-validator`` beyond its
public API, in one place.

The renderer never re-derives a LegalDown rule (docs/decisions/0002): where
the validator has decided something, the renderer asks it. Since
legaldown-validator 0.3.0 nearly all of it is public — where markers are
placed and whether a document is a template come with the
``ValidationResult`` (``placed_markers``, ``is_template``), and the model
helpers are exported from ``legaldown``. What is left here is how the
validator reads a block quote's content and a fenced code block, and how
its CLI reads an answers file. The dependency stays pinned to one minor
version while this list is not empty.
"""
from __future__ import annotations

from legaldown.cli import _read_answers as read_answers
from legaldown.markdown import FENCE_OPEN_RE, closes_fence, dedent, indent_width
from legaldown.parser import MAX_QUOTE_DEPTH, quote_content

__all__ = [
    "FENCE_OPEN_RE",
    "MAX_QUOTE_DEPTH",
    "closes_fence",
    "dedent",
    "indent_width",
    "quote_content",
    "read_answers",
]
