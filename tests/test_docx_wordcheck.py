"""The generator of the Word check document (tests/word_check.py, .github/PUBLISHING.md item 8 and 9): that it makes a
valid package whose rows carry what the planner assumes, from the planner's own code, and that LibreOffice (which does
not read ``\\t``) computes the rows' plain ``REF \\n`` fields as the planner caches them."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from docx_support import libreoffice_export, parts, require_libreoffice, require_schemas, validation_errors
from word_check import ROWS, build, main

from legaldown_render import render
from legaldown_render.resolve.docx_cover import suppressed
from legaldown_render.resolve.docx_numbering import Count, Lit
from legaldown_render.resolve.docx_plan import plan_docx

REPOSITORY = Path(__file__).resolve().parents[1]
CHECKLIST = REPOSITORY / ".github" / "PUBLISHING.md"


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
    assert "Part %1 of %2" in text and "documented: the switch is not used; guess: '11'" in text  # words between counters
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


# -- The checklist's own snippet -----------------------------------------------------------------------------------


def checklist_snippet() -> str:
    """The Python the checklist gives for making the documents to open in Word."""
    if not CHECKLIST.is_file():
        pytest.skip("the checklist is not part of this distribution")
    section = CHECKLIST.read_text(encoding="utf-8").split("## Checking the DOCX in Word", 1)[1]
    found = re.search(r"```python\n(.*?)```", section, re.S)
    assert found is not None
    return found.group(1)


def test_the_snippet_of_the_checklist_makes_the_documents_it_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """What the checklist tells a maintainer to run still runs, and makes every file its items name: the three styles and the
    settings variants of both golden documents (the template has no attachments, so none of the variants about them), and
    the document of the level nothing numbered."""
    monkeypatch.chdir(REPOSITORY)
    exec(compile(checklist_snippet().replace('Path("word-check")', f"Path({str(tmp_path)!r})"), "PUBLISHING.md", "exec"), {})
    made = sorted(path.name for path in tmp_path.iterdir())
    styles = ("default", "continental", "outline")
    variants = ("attachments-not-listed", "depth-1", "no-attachments-label", "separator-none", "separator-rule",
                "scheme-none", "paragraphs-numbered")
    about_attachments = {"attachments-not-listed", "no-attachments-label", "separator-none", "separator-rule"}
    expected = [f"features.{name}.docx" for name in (*styles, *variants)]
    expected += [f"template.{name}.docx" for name in (*styles, *(v for v in variants if v not in about_attachments))]
    expected.append("uncounted.default.docx")
    assert made == sorted(expected)
    checklist = CHECKLIST.read_text(encoding="utf-8")
    assert all(name in checklist for name in (*variants, "uncounted.default.docx"))
    assert all(parts((tmp_path / name).read_bytes())["word/document.xml"] for name in made)


def test_the_document_of_the_level_nothing_numbered_is_the_one_the_checklist_describes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Items ``e`` and ``f`` are numbered ``(A)`` and ``(B)`` as the checklist says Word is expected to number them, with the
    information that LibreOffice (``(B)``, ``(C)``) does not (tests/test_docx_roundtrip.py shows what it does)."""
    monkeypatch.chdir(REPOSITORY)
    code = checklist_snippet().replace('Path("word-check")', f"Path({str(tmp_path)!r})")
    namespace: dict = {}
    exec(compile(code, "PUBLISHING.md", "exec"), namespace)
    result = render(namespace["uncounted"], format="docx")
    plan, diagnostics = plan_docx(result.tree)
    items = [unit for unit in plan.units.values() if unit.label in ("(A)", "(B)")]
    assert [unit.label for unit in items] == ["(A)", "(B)"]
    assert any(d.rule == "render-docx-libreoffice-count" and d.level == "info" for d in diagnostics)
