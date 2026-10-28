"""What a LibreOffice document says, read from its model instead of from a text export filter.

LibreOffice's plain-text export lays a table row out as one line (cells separated by tabs) in some versions
and as one line per cell paragraph in others, so a test that compared that text would depend on the version of
the LibreOffice that happens to be installed. The text here is made by walking the body the way the document
holds it, so it is the same under every version: one line per paragraph, a table's rows and cells in order (each
cell's paragraphs, and a table inside a cell, in place), a soft line break starting a new line, and, before the
text of a numbered paragraph, the label LibreOffice draws for it (``ListLabelString``, which is what its numbering
computes once the fields are updated) and one space.

This module needs nothing from LibreOffice itself (it reads what the UNO objects answer), so the tests can
run it over stand-ins (tests/test_docx_lo_text.py). It is imported by tests/lo/uno_export.py and
tests/lo/uno_edit.py, which run under the system Python that has ``uno``.
"""
from __future__ import annotations

import re

PARAGRAPH = "com.sun.star.text.Paragraph"
TABLE = "com.sun.star.text.TextTable"


def _cell_order(name: str) -> tuple[int, int, str]:
    """The reading order of a cell name ("A1", "B1", "A2", ..., "AA1"): by row, then by column."""
    match = re.fullmatch(r"([A-Za-z]+)(\d+)", name)
    assert match is not None, name
    return int(match[2]), len(match[1]), match[1]


def paragraph_lines(paragraph: object) -> list[str]:
    """The lines of one paragraph: its text, split at the soft line breaks, the first one led by the list label
    (and a space) when the paragraph shows one."""
    label = paragraph.ListLabelString  # type: ignore[attr-defined]
    text = paragraph.getString()  # type: ignore[attr-defined]
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if label:
        lines[0] = f"{label} {lines[0]}"
    return lines


def collect(enumeration: object, lines: list[str]) -> None:
    """Append the lines of every paragraph and table that *enumeration* (of a text or of a table cell) yields."""
    while enumeration.hasMoreElements():  # type: ignore[attr-defined]
        element = enumeration.nextElement()  # type: ignore[attr-defined]
        if element.supportsService(TABLE):
            for name in sorted(element.getCellNames(), key=_cell_order):
                collect(element.getCellByName(name).createEnumeration(), lines)
        elif element.supportsService(PARAGRAPH):
            lines += paragraph_lines(element)


def body_text(document: object) -> str:
    """The body of *document* as text: every line ends with a line feed, so that an empty last paragraph shows as
    an empty last line, as in a plain-text file."""
    lines: list[str] = []
    collect(document.getText().createEnumeration(), lines)  # type: ignore[attr-defined]
    return "".join(f"{line}\n" for line in lines)
