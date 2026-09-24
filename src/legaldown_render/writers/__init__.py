"""Writers: resolved tree → file format. A writer contains no LegalDown rules
(docs/decisions/0003); it only lays out what resolution computed."""
from __future__ import annotations

from .html import HtmlWriter, stylesheet
from .text import TextWriter

#: Output formats by name, with the file extensions that select them.
FORMATS: dict[str, tuple[str, ...]] = {
    "html": ("html", "htm"),
    "text": ("txt", "text"),
}


def format_for_path(path: str) -> str | None:
    """The output format a file name implies, or None."""
    extension = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return next((name for name, extensions in FORMATS.items() if extension in extensions), None)


__all__ = ["FORMATS", "HtmlWriter", "TextWriter", "format_for_path", "stylesheet"]
