"""The generator of the Word check document (tests/word_check.py, .github/PUBLISHING.md item 8 and 9): that it makes a
valid package whose rows carry what the planner assumes, from the planner's own code, and that LibreOffice (which does
not read ``\\t``) computes the rows' plain ``REF \\n`` fields as the planner caches them."""
from __future__ import annotations

import re

import pytest
from docx_support import libreoffice_export, parts, require_libreoffice, require_schemas, validation_errors
from word_check import ROWS, build, main

from legaldown_render.resolve.docx_cover import suppressed
from legaldown_render.resolve.docx_numbering import Count, Lit


def test_the_document_is_a_package_that_holds_every_row_and_the_assumption_beside_it() -> None:
    contents = parts(build())
    assert {"[Content_Types].xml", "word/document.xml", "word/numbering.xml", "word/styles.xml"} <= set(contents)
    text = re.sub(r"<[^>]+>", "", contents["word/document.xml"].decode("utf-8"))
    for number, row in enumerate(ROWS, 1):
        assert f"Row {number}, {row.title}" in text
    # the planner's assumption is what its own code says, per setting of docx.reference_text_switch
    assert "Row 6, an abbreviation with a period: No. %1: drawn 'No. 1'" in text
    assert "documented: the switch is not used; guess: '1'" in text  # No. 1: only the guess drops the period
    assert "documented: 'I.1'; guess: 'I.1'" in text  # Microsoft's example with a roman counter
    assert contents["word/document.xml"].count(b"REF t3 \\n \\t \\h") == 1
    assert suppressed([Lit("Section "), Count("a", "1"), Lit("."), Count("b", "1")]) is not None


def test_the_document_is_deterministic_and_the_command_writes_it(tmp_path) -> None:  # noqa: ANN001
    assert build() == build()
    target = tmp_path / "folder" / "check.docx"
    assert main(["word_check.py", str(target)]) == 0 and target.read_bytes() == build()
    assert main(["word_check.py"]) == 2


@pytest.mark.ooxml_schema
def test_the_document_validates_against_the_schemas() -> None:
    require_schemas()
    assert validation_errors(build()) == []


@pytest.mark.libreoffice
def test_libreoffice_computes_the_plain_fields_as_the_planner_caches_them() -> None:
    require_libreoffice()
    said = libreoffice_export({"check": build()})["check"].text
    lines = [line for line in said.splitlines() if "REF \\n gives" in line]
    assert len(lines) == len(ROWS) + 6 + 2  # the rows, the nested items of part B, the two headings of part C
    for line in lines:
        computed = re.search(r"REF \\n gives (.*?)(?:\. REF \\n \\t|$)", line)
        assumed = re.search(r"\[planner: (.*?)\]", line) or re.search(r"The planner assumes REF \\n gives '(.*?)';", line)
        assert computed and assumed, line
        assert computed.group(1).rstrip(".") == assumed.group(1).rstrip("."), line
