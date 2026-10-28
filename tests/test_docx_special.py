"""What the planner's hard cases become in the file (docs/architecture.md, "The DOCX planner"): the heading list's levels
that hold items, the reserved levels of the lists nested in them, and the later alternatives that share a number, as
numbering definitions, ``w:numPr``, ``w:ind`` and ``REF`` fields; and that every document that has them validates
against the ISO/IEC 29500-4 schemas (marker ``ooxml_schema``). The numbers as Word's simulator and LibreOffice draw them
are checked in test_docx_plan.py and test_docx_roundtrip.py."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest
from conftest import FRONT
from docx_support import W, parts, require_schemas, validation_errors
from test_docx_plan import ALTERNATIVES
from test_docx_roundtrip import STRESS

from legaldown_render import render


def document(source: str, **settings: object) -> dict[str, ET.Element]:
    result = render(source, format="docx", **settings)
    assert result.binary is not None
    return {name: ET.fromstring(content) for name, content in parts(result.binary).items()
            if name in ("word/document.xml", "word/numbering.xml")}


def body_paragraphs(root: ET.Element) -> list[ET.Element]:
    body = root.find(f"{W}body")
    assert body is not None
    return body.findall(f"{W}p")


def text_of(paragraph: ET.Element) -> str:
    return "".join(node.text or "" for node in paragraph.iter(f"{W}t"))


def paragraph_with(root: ET.Element, text: str) -> ET.Element:
    [found] = [p for p in body_paragraphs(root) if text_of(p).endswith(text)]
    return found


def numbering_of(paragraph: ET.Element) -> tuple[int | None, int | None]:
    ilvl = paragraph.find(f"{W}pPr/{W}numPr/{W}ilvl")
    num = paragraph.find(f"{W}pPr/{W}numPr/{W}numId")
    return (None if ilvl is None else int(ilvl.get(f"{W}val")),  # type: ignore[arg-type]
            None if num is None else int(num.get(f"{W}val")))  # type: ignore[arg-type]


def indent(paragraph: ET.Element) -> tuple[int, int] | None:
    found = paragraph.find(f"{W}pPr/{W}ind")
    return None if found is None else (int(found.get(f"{W}left")), int(found.get(f"{W}hanging", "0")))  # type: ignore[arg-type]


def levels(numbering: ET.Element, abstract_id: int) -> list[ET.Element]:
    [abstract] = [a for a in numbering.findall(f"{W}abstractNum") if a.get(f"{W}abstractNumId") == str(abstract_id)]
    return abstract.findall(f"{W}lvl")


def instructions(paragraph: ET.Element) -> list[str]:
    return [n.text.strip() for n in paragraph.iter(f"{W}instrText") if n.text]


CONTINENTAL = FRONT + "# Alpha\n\n- clause one {#c1}\n  - nested a\n  - nested b\n    - deeper\n- clause two\n"


def test_an_item_on_the_heading_list_is_numbered_at_the_level_after_its_section_with_an_indent_of_its_own() -> None:
    parts_ = document(CONTINENTAL, style="continental")
    one, two = paragraph_with(parts_["word/document.xml"], "clause one"), paragraph_with(
        parts_["word/document.xml"], "clause two")
    assert numbering_of(one) == numbering_of(two) == (1, 2)
    assert one.find(f"{W}pPr/{W}pStyle") is None  # no heading style: it is no heading
    assert indent(one) == indent(two) is not None
    level = levels(parts_["word/numbering.xml"], 0)[1]
    assert level.find(f"{W}lvlText").get(f"{W}val") == "%1.%2" and level.find(f"{W}pStyle").get(f"{W}val") == "Heading2"
    # the num of the list restarts the level at one, as every top-level list does
    [num] = [n for n in parts_["word/numbering.xml"].findall(f"{W}num") if n.get(f"{W}numId") == "2"]
    override = num.find(f"{W}lvlOverride")
    assert override.get(f"{W}ilvl") == "1" and override.find(f"{W}startOverride").get(f"{W}val") == "1"  # type: ignore[union-attr]


def test_a_nested_item_uses_a_reserved_level_of_the_same_num_with_the_indent_one_step_in() -> None:
    parts_ = document(CONTINENTAL, style="continental")
    root = parts_["word/document.xml"]
    one, a, b, deeper = (paragraph_with(root, t) for t in ("clause one", "nested a", "nested b", "deeper"))
    assert [numbering_of(p) for p in (a, b, deeper)] == [(5, 2), (5, 2), (6, 2)]  # the parent's num, no override of its own
    assert indent(a) == (indent(one)[0] + 567, 567) and indent(deeper) == (indent(one)[0] + 1134, 567)  # type: ignore[index]
    levels_ = levels(parts_["word/numbering.xml"], 0)
    assert [(lvl.find(f"{W}numFmt").get(f"{W}val"), lvl.find(f"{W}lvlText").get(f"{W}val")) for lvl in levels_[5:7]] == [
        ("lowerLetter", "%6)"), ("lowerRoman", "%7)")]
    assert [n.get(f"{W}numId") for n in parts_["word/numbering.xml"].findall(f"{W}num")] == ["1", "2"]


SHARED = ALTERNATIVES + """
# Disputes {#d when=forum:courts}

## Sub one {#s1}

# Disputes {#d when=forum:arb}

## Sub alt {#sa}

- courts item {#ci when=forum:courts}
- arbitration item {#ci when=forum:arb}
  - nested under it {#an}
- after
"""


def test_a_later_alternative_has_numid_zero_an_indent_of_its_own_and_the_number_of_the_holder_as_a_field() -> None:
    parts_ = document(SHARED)
    root = parts_["word/document.xml"]
    heading = [p for p in body_paragraphs(root) if text_of(p).endswith("Disputes Only if: forum:arb")][0]
    assert numbering_of(heading) == (0, 0) and indent(heading) == (567, 567)
    assert heading.find(f"{W}pPr/{W}pStyle").get(f"{W}val") == "Heading1"  # type: ignore[union-attr]
    # the number: a REF field for the holder (with \n and \h), the period as text, then a tab, then the title
    fields = instructions(heading)
    assert len(fields) == 1 and re.fullmatch(r"REF \S+ \\n \\h", fields[0])
    assert text_of(heading).startswith("1.")  # the cached result "1" and the period
    runs = [r for r in heading.findall(f"{W}r")]
    assert any(r.find(f"{W}tab") is not None for r in runs)
    # the holder carries the bookmark the field points at
    name = fields[0].split()[1]
    assert name in {b.get(f"{W}name") for b in root.iter(f"{W}bookmarkStart")}
    item = paragraph_with(root, "arbitration item")
    [field] = instructions(item)
    assert numbering_of(item) == (0, 0) and re.fullmatch(r"REF \S+ \\n \\h", field)
    assert field.split()[1] in {b.get(f"{W}name") for b in root.iter(f"{W}bookmarkStart")}
    # the first alternative and the section that follows are numbered by the list as usual
    assert numbering_of(paragraph_with(root, "courts item")) == (0, 3)
    assert numbering_of(paragraph_with(root, "after")) == (0, 3)


def test_the_children_of_a_later_alternative_are_numbered_by_a_num_of_their_own() -> None:
    parts_ = document(SHARED)
    root, numbering = parts_["word/document.xml"], parts_["word/numbering.xml"]
    sub_alt, nested = paragraph_with(root, "Sub alt"), paragraph_with(root, "nested under it")
    (_, sub_num), (_, nested_num) = numbering_of(sub_alt), numbering_of(nested)
    assert sub_num == 2 and nested_num == 4  # the sections after the alternative; the list nested under the alternative item
    for num_id, level in ((sub_num, "1"), (nested_num, "1")):
        [num] = [n for n in numbering.findall(f"{W}num") if n.get(f"{W}numId") == str(num_id)]
        assert num.find(f"{W}lvlOverride").get(f"{W}ilvl") == level  # type: ignore[union-attr]


@pytest.mark.ooxml_schema
@pytest.mark.parametrize("name", ["alternatives", "alternatives in threes", "qualified", "level five", "nested titles"])
@pytest.mark.parametrize("settings", [{"style": "continental"}, {}, {"overrides": {"numbering.scheme": "none"}},
                                      {"style": "continental", "overrides": {"paragraphs.numbered": True}}])
def test_documents_with_alternatives_and_items_on_the_heading_list_validate(name: str, settings: dict) -> None:
    require_schemas()
    result = render(STRESS[name], format="docx", **settings)
    assert result.binary is not None
    assert validation_errors(result.binary) == []
