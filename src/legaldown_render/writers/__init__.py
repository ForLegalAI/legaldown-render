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


def file_extension(name: str) -> str:
    """The lower-cased extension of a file name, without the dot, or ``""``.

    It is the text after the last dot, also in a name that starts with one
    (``.pdf`` is a PDF name here, not a name without extension), and trailing
    dots and spaces are not part of the name (Windows drops them, so
    ``x.pdf.`` and ``x.docx `` name the same files as ``x.pdf`` and ``x.docx``).
    """
    name = name.rstrip(". ")
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def format_for_path(path: str) -> str | None:
    """The output format a file name implies, or None."""
    extension = file_extension(path)
    return next((name for name, extensions in FORMATS.items() if extension in extensions), None)


__all__ = ["FORMATS", "HtmlWriter", "TextWriter", "file_extension", "format_for_path", "stylesheet"]
