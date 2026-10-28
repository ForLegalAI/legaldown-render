"""The text tests/lo/uno_text.py reads from a LibreOffice document model: one line per paragraph, whatever the
version of LibreOffice. It is run here over stand-ins for the UNO objects, so no LibreOffice is needed: the
text export filter, which lays a table row out differently in different versions, is not involved at all."""
from __future__ import annotations

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("uno_text", Path(__file__).parent / "lo" / "uno_text.py")
assert spec is not None and spec.loader is not None
uno_text = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uno_text)


class Enumeration:
    def __init__(self, items: list[object]) -> None:
        self.items = list(items)

    def hasMoreElements(self) -> bool:  # noqa: N802 - the UNO name
        return bool(self.items)

    def nextElement(self) -> object:  # noqa: N802
        return self.items.pop(0)


class Paragraph:
    def __init__(self, text: str, label: str = "") -> None:
        self.text, self.ListLabelString = text, label

    def supportsService(self, name: str) -> bool:  # noqa: N802
        return name == uno_text.PARAGRAPH

    def getString(self) -> str:  # noqa: N802
        return self.text


class Cell:
    def __init__(self, *items: object) -> None:
        self.items = items

    def createEnumeration(self) -> Enumeration:  # noqa: N802
        return Enumeration(list(self.items))


class Table:
    def __init__(self, cells: dict[str, Cell]) -> None:
        self.cells = cells

    def supportsService(self, name: str) -> bool:  # noqa: N802
        return name == uno_text.TABLE

    def getCellNames(self) -> list[str]:  # noqa: N802
        return list(self.cells)

    def getCellByName(self, name: str) -> Cell:  # noqa: N802
        return self.cells[name]


class Document:
    def __init__(self, *items: object) -> None:
        self.items = items

    def getText(self) -> Document:  # noqa: N802
        return self

    def createEnumeration(self) -> Enumeration:  # noqa: N802
        return Enumeration(list(self.items))


def test_a_table_row_is_one_line_per_cell_paragraph_in_reading_order() -> None:
    # the cell names in the order a version of LibreOffice may list them: the text is by row, then by column
    table = Table({"B2": Cell(Paragraph("d")), "A1": Cell(Paragraph("a")), "B1": Cell(Paragraph("b"), Paragraph("b2")),
                   "A2": Cell(Paragraph("c"))})
    document = Document(Paragraph("before"), table, Paragraph(""))
    assert uno_text.body_text(document) == "before\na\nb\nb2\nc\nd\n\n"


def test_a_table_inside_a_cell_is_read_in_place_and_wide_tables_by_column_length() -> None:
    inner = Table({"A1": Cell(Paragraph("x")), "B1": Cell(Paragraph("y"))})
    names = ["A1", "B1", "Z1", "AA1", "A2"]
    outer = Table({name: Cell(inner if name == "B1" else Paragraph(name)) for name in reversed(names)})
    assert uno_text.body_text(Document(outer)) == "A1\nx\ny\nZ1\nAA1\nA2\n"


def test_a_numbered_paragraph_leads_with_its_label_and_a_space_and_a_line_break_starts_a_line() -> None:
    document = Document(Paragraph("First", "1."), Paragraph("one\ntwo\tthree", "(a)"), Paragraph("plain\r\nnext"))
    assert uno_text.body_text(document) == "1. First\n(a) one\ntwo\tthree\nplain\nnext\n"


def test_an_empty_document_has_no_text() -> None:
    assert uno_text.body_text(Document()) == ""
