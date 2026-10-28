"""LibreOffice as the oracle: each document is opened headless through UNO (tests/lo/uno_export.py,
run by the system Python that has ``uno``), its fields and indexes are updated, and what it shows is
compared with what was meant: the words in order, the running header and footer, the page count.
Skipped when ``soffice`` or ``python3-uno`` is missing (it is not on Windows, and not in the test
jobs that do not install it); a failure instead when ``LEGALDOWN_REQUIRE_DOCX_TOOLS`` is set, as in
the CI job that installs them (``pytest -m libreoffice``)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest
from conftest import DOCUMENTS
from docx_support import Exported, libreoffice_export, parts, require_libreoffice, texts

from legaldown_render import render
from legaldown_render.resolve.docx_plan import plan_docx

pytestmark = pytest.mark.libreoffice

ROUND_TRIP = """---
legaldown: '0.2'
title: Round Trip
subtitle: Sub
language: cs
version: '1.2'
effective_date: 2026-03-04
---

Preamble ž€.

# First

Body with *emphasis*, **strong** and `code`.\\
Second line.

## Second

- item one
- item two

> A quote

```
code line 1
  code line 2
```

| H1 | H2 |
|----|----|
| a | b |
"""

LONG = "---\ntitle: Long\n---\n\n" + "".join(f"# Heading {n}\n\nParagraph {n} " + "word " * 120 + "\n\n" for n in range(40))

#: A few lines and three attachments, each of which starts a page (``attachments.separator: page-break``, the default):
#: how many pages it has is the layout's business, but never fewer than the breaks make, and in the order of the document.
PAGED = """---
legaldown: '0.2'
title: Paged
attachments:
  - {id: alpha, title: Alpha Schedule}
  - {id: beta, title: Beta Schedule}
  - {id: gamma, title: Gamma Schedule}
---

# One

See {{attach: gamma}}.
"""

HOSTILE = "---\nlegaldown: '0.2'\ntitle: \"a\\x01b\"\n---\n\n# H\x01\x08\x0b\x0c\x1f\x7f\x85x\n\nbody\x01 <&> ]]>\n"


def docx(source: str, **settings: object) -> bytes:
    result = render(source, format="docx", **settings)
    assert result.binary is not None
    return result.binary


def without_number(line: str, labels: list[str]) -> str:
    """*line* without the number LibreOffice drew before the text of a numbered paragraph (one of *labels*,
    the numbers Word draws, with any indent of a nested item)."""
    text = line.lstrip()
    for label in labels:
        if text.startswith(f"{label} "):
            return text.removeprefix(f"{label} ")
    return line


def expected_lines(data: bytes) -> list[str]:
    """What the paragraphs of the document part say, one line per line, without blank ones."""
    lines = []
    for paragraph in texts(ET.fromstring(parts(data)["word/document.xml"])):
        lines += paragraph.split("\n")
    return [line for line in lines if line]


@pytest.fixture(scope="module")
def shown() -> dict[str, Exported]:
    require_libreoffice()
    documents = {
        "round_trip": docx(ROUND_TRIP, overrides={"page.header.left": "{title}", "page.header.center": "{date}",
                                                   "page.header.right": "v{version}, p{page}"}),
        "features": docx((DOCUMENTS / "features.lgd").read_text(encoding="utf-8")),
        "template": docx((DOCUMENTS / "template.lgd").read_text(encoding="utf-8"), style="continental"),
        "long": docx(LONG),
        "paged": docx(PAGED, overrides={"contents.enabled": True, "page.footer": {"left": "{page}", "center": "", "right": "{pages}"}}),
        "hostile": docx(HOSTILE),
        "no_footer": docx(ROUND_TRIP, overrides={"page.footer": {"center": ""}}),
        "landscape": docx(ROUND_TRIP, overrides={"page.size": "A4 landscape", "page.footer.right": "{page}"}),
    }
    return libreoffice_export(documents)


def test_the_words_come_back_in_order(shown: dict[str, Exported]) -> None:
    # the numbers are drawn by LibreOffice from the numbering definitions (test_docx_roundtrip.py compares them)
    assert shown["round_trip"].lines == [
        "Round Trip", "Sub", "Datum účinnosti", "4. března 2026", "Preamble ž€.", "1. First", "Body with emphasis, strong and code.", "Second line.",
        "1.1 Second", "(a) item one", "(b) item two", "A quote", "code line 1", "  code line 2", "H1", "H2",
        "a", "b", ""]


@pytest.mark.parametrize("name", ["features", "template"])
def test_the_test_documents_read_as_their_paragraphs(shown: dict[str, Exported], name: str) -> None:
    settings = {"style": "continental"} if name == "template" else {}
    source = (DOCUMENTS / f"{name}.lgd").read_text(encoding="utf-8")
    labels = sorted({unit.label for unit in plan_docx(render(source, format="text", **settings).tree)[0].units.values()
                     if unit.label}, key=len, reverse=True)
    words = [without_number(line, labels) for line in shown[name].lines if line]
    assert words == expected_lines(docx(source, **settings))


def test_the_header_shows_the_templates_and_the_page_field(shown: dict[str, Exported]) -> None:
    assert shown["round_trip"].header == "Round Trip\t4. března 2026\tv1.2, p1"


def test_the_default_footer_counts_the_pages(shown: dict[str, Exported]) -> None:
    footer = shown["round_trip"].footer
    match = re.fullmatch(r"1 / (\d+)", footer)  # a few lines: one page in any font
    assert match is not None, footer
    assert int(match.group(1)) == shown["round_trip"].pages == 1
    assert shown["round_trip"].header.count("\t") == 2


def test_the_page_count_field_follows_the_layout(shown: dict[str, Exported]) -> None:
    """How many pages a text takes depends on the fonts of the machine, so the counts are compared with each other: the
    long document has more pages than the short one, and its footer reads the first page and the number of pages the
    layout has."""
    long, short = shown["long"], shown["round_trip"]
    assert long.pages > short.pages
    match = re.fullmatch(r"(\d+) / (\d+)", long.footer)
    assert match is not None, long.footer
    assert int(match.group(2)) == long.pages
    assert 1 <= int(match.group(1)) <= long.pages


def test_each_attachment_starts_a_page_and_the_contents_give_their_pages_in_order(shown: dict[str, Exported]) -> None:
    """The page breaks hold in any font: three attachments are on three different pages, in the order of the document, the
    first not on the first page, and the footer's page count is the layout's."""
    paged = shown["paged"]
    pages = [int(m[1]) for title in ("Alpha", "Beta", "Gamma")
             for m in [re.search(rf"^\s*{title} Schedule\t(\d+)$", paged.text, re.M)] if m]
    assert len(pages) == 3, paged.text
    assert 2 <= pages[0] < pages[1] < pages[2] <= paged.pages
    left, right = paged.footer.split("\t")[0], paged.footer.split("\t")[-1]
    assert 1 <= int(left) <= paged.pages and right == str(paged.pages), paged.footer  # {page} is that of the view's cursor


def test_no_footer_means_none(shown: dict[str, Exported]) -> None:
    assert shown["no_footer"].footer == ""


def test_a_landscape_page_with_a_right_aligned_page_number_opens(shown: dict[str, Exported]) -> None:
    assert shown["landscape"].footer == "\t1 / 1\t1"
    assert shown["landscape"].lines == shown["round_trip"].lines


def test_hostile_text_opens_with_the_unwritable_characters_dropped(shown: dict[str, Exported]) -> None:
    lines = shown["hostile"].lines
    assert lines[0] == "ab"
    assert lines[1] == "1. Hx"
    assert lines[2] == "body <&> ]]>"
