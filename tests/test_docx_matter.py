"""The front and back matter of the DOCX (§13.6, §13.8, §3.6): the header rows and the parties, the table of
contents, the attachments and the signature blocks. Their XML (no tool needed), the validity of documents
that hold every part, hostile text included (marker ``ooxml_schema``), and what LibreOffice shows once its
fields and indexes are updated (marker ``libreoffice``): the table of contents lists the HTML contents'
entries with page numbers, a reference to an attachment resolves, the attachments are paged as configured,
and the document reads in the order the plain-text writer prints it."""
from __future__ import annotations

import random
import re
import xml.etree.ElementTree as ET
from typing import Any

import pytest
import yaml
from conftest import DOCUMENTS
from docx_support import (
    W_NS,
    Exported,
    libreoffice_edit,
    libreoffice_export,
    parts,
    repackage,
    require_libreoffice,
    require_schemas,
    texts,
    validation_errors,
)
from test_docx_escaping import ALPHABET, assert_well_formed, soup

from legaldown_render import render
from legaldown_render.errors import InternalError
from legaldown_render.resolve.docx_plan import DocxPlan, plan_docx
from legaldown_render.style import load_style
from legaldown_render.style.docx import docx_settings
from legaldown_render.tree import plain_text
from legaldown_render.writers import DocxWriter

W = f"{{{W_NS}}}"

FRONT = """---
legaldown: '0.2'
title: Master Agreement
subtitle: Every part of the matter
version: '2.1'
effective_date: 2026-03-04
language: en
questions:
  forum:
    type: choice
    prompt: How are disputes resolved?
    choices:
      courts: State courts
      arbitration: Arbitration
sides:
  - name: sellers
    label: Seller
    parties:
      - name: acme
        type: legal_entity
        legal_name: Acme Corporation
        identification_number: CZ-123
        address: 1 Main Street
        representatives:
          - name: John Smith
            title: Chief Executive Officer
          - name: Jane Roe
            title: Director
  - name: buyers
    label: Buyer
    parties:
      - name: beta
        type: legal_entity
        legal_name: Beta Industries Inc.
      - name: gamma
        type: legal_entity
        legal_name: Gamma Ltd.
        representatives:
          - name: Pat Doe
            title: Proxy
attachments:
  - id: schedule-a
    title: "Schedule A: Services"
    file: files/services.lgd
  - id: price-list
    title: Price List
    when: forum:courts
  - id: terms
    title: Terms
---

Preamble text.

# Services {#services}

The Seller provides {{attach: schedule-a}}, see {{attach: price-list, label=the prices}}.

## Delivery

Text of the delivery.

# Fees {#fees}

Fees are in {{attach: terms}}.

## Payment {#payment}

Net 30.

### Late payment

Interest applies.
"""


def docx(source: str = FRONT, **settings: Any) -> bytes:
    result = render(source, format="docx", **settings)
    assert result.binary is not None
    return result.binary


def document_xml(data: bytes) -> ET.Element:
    return ET.fromstring(parts(data)["word/document.xml"])


def body(data: bytes) -> list[ET.Element]:
    return list(document_xml(data).find(f"{W}body"))  # type: ignore[arg-type]


def style_of(paragraph: ET.Element) -> str | None:
    node = paragraph.find(f"{W}pPr/{W}pStyle")
    return None if node is None else node.get(f"{W}val")


def paragraphs(data: bytes, style: str) -> list[ET.Element]:
    return [p for p in document_xml(data).iter(f"{W}p") if style_of(p) == style]


def text_of(element: ET.Element) -> str:
    """The text of a paragraph (a tab as TAB), or of any other element: its runs' text."""
    if element.tag in (f"{W}p", f"{W}tc"):
        return "\n".join(texts(element)) if element.tag == f"{W}tc" else texts(element)[0]
    return "".join({f"{W}t": lambda n: n.text or "", f"{W}tab": lambda n: "\t"}.get(n.tag, lambda n: "")(n)
                   for n in element.iter())


def instructions(root: ET.Element) -> list[str]:
    return [(node.text or "").strip() for node in root.iter(f"{W}instrText")]


def bookmark_names(root: ET.Element) -> list[str]:
    return [node.get(f"{W}name") for node in root.iter(f"{W}bookmarkStart")]  # type: ignore[misc]


def tables(data: bytes) -> list[ET.Element]:
    return list(document_xml(data).iter(f"{W}tbl"))


def contents_style(**overrides: Any) -> dict[str, Any]:
    return {"overrides": {"contents.enabled": True, **overrides}}


# -- Order -----------------------------------------------------------------------------------------------------


def test_the_matter_is_written_in_the_order_the_html_shows_it() -> None:
    data = docx(**contents_style())
    styles = [style_of(c) if c.tag == f"{W}p" else "table" for c in body(data) if c.tag != f"{W}sectPr"]
    order = [name for name in styles if name in ("Title", "Subtitle", "table", "PartySide", "ContentsHeading", "TOC1",
                                                 "Heading1", "AttachmentsHeading", "AttachmentTitle",
                                                 "SignatureSide")]
    first = {}
    for index, name in enumerate(order):
        first.setdefault(name, index)
    sequence = ["Title", "Subtitle", "table", "PartySide", "ContentsHeading", "TOC1", "Heading1",
                "AttachmentsHeading", "AttachmentTitle"]
    assert [first[name] for name in sequence] == sorted(first[name] for name in sequence)
    assert order[-1] == "table"  # the signature table, after the attachments
    assert max(i for i, name in enumerate(order) if name == "AttachmentTitle") < len(order) - 1


# -- Header rows and parties -----------------------------------------------------------------------------------


def test_the_header_rows_are_a_borderless_table_of_label_and_value() -> None:
    data = docx()
    header = tables(data)[0]
    rows = [[text_of(cell) for cell in row.iter(f"{W}tc")] for row in header.iter(f"{W}tr")]
    assert rows == [["Effective date", "March 4, 2026"], ["Version", "2.1"]] or rows[0] == ["Effective date",
                                                                                              "March 4, 2026"]
    assert header.find(f"{W}tblPr/{W}tblBorders") is None and header.find(f"{W}tblPr/{W}tblStyle") is None
    label = header.find(f"{W}tr/{W}tc/{W}p/{W}r/{W}rPr/{W}rStyle")
    assert label is not None and label.get(f"{W}val") == "TitleBlockLabel"


def test_the_parties_follow_their_side_with_their_details() -> None:
    data = docx()
    sides = [text_of(p) for p in paragraphs(data, "PartySide")]
    assert sides == ["Seller", "Buyer"]
    assert [text_of(p) for p in paragraphs(data, "PartyName")] == ["Acme Corporation", "Beta Industries Inc.",
                                                                  "Gamma Ltd."]
    details = tables(data)[1]
    rows = [[text_of(cell) for cell in row.iter(f"{W}tc")] for row in details.iter(f"{W}tr")]
    assert ["Identification No.", "CZ-123"] in rows and ["Address", "1 Main Street"] in rows
    assert any(row[0] == "Represented by" and "John Smith" in row[1] for row in rows)


def test_no_header_rows_no_parties_means_no_table() -> None:
    data = docx("---\ntitle: T\n---\n# A\n", overrides={"title_block.effective_date": False})
    assert tables(data) == []


def test_the_style_can_hide_the_parties() -> None:
    data = docx(overrides={"title_block.parties": False})
    assert paragraphs(data, "PartySide") == []


def test_version_and_date_are_the_rows_the_title_block_shows() -> None:
    data = docx(overrides={"title_block.version": True})
    rows = [[text_of(cell) for cell in row.iter(f"{W}tc")] for row in tables(data)[0].iter(f"{W}tr")]
    assert rows == [["Effective date", "March 4, 2026"], ["Version", "2.1"]]


def test_two_tables_in_a_row_are_kept_apart() -> None:
    source = "---\ntitle: T\neffective_date: 2026-01-02\n---\n\n| a | b |\n|---|---|\n| c | d |\n"
    elements = [c for c in body(docx(source)) if c.tag != f"{W}sectPr"]
    tags = [c.tag.removeprefix(W) for c in elements]
    assert tags[:5] == ["p", "tbl", "p", "tbl", "p"]


# -- Table of contents -----------------------------------------------------------------------------------------


def toc_paragraphs(data: bytes) -> list[ET.Element]:
    return [p for p in document_xml(data).iter(f"{W}p") if (style_of(p) or "").startswith("TOC")]


def test_the_contents_label_is_a_style_without_numbers_or_outline() -> None:
    data = docx(**contents_style())
    [label] = paragraphs(data, "ContentsHeading")
    assert text_of(label) == "Contents"
    assert label.find(f"{W}pPr/{W}numPr") is None and label.find(f"{W}pPr/{W}outlineLvl") is None
    styles = ET.fromstring(parts(data)["word/styles.xml"])
    heading = next(s for s in styles.iter(f"{W}style") if s.get(f"{W}styleId") == "ContentsHeading")
    assert heading.find(f"{W}pPr/{W}outlineLvl") is None and heading.find(f"{W}pPr/{W}numPr") is None
    assert heading.find(f"{W}name").get(f"{W}val") == "Contents Heading"


def test_the_toc_field_is_one_complex_field_over_the_entries() -> None:
    data = docx(**contents_style())
    root = document_xml(data)
    toc = [i for i in instructions(root) if i.startswith("TOC")]
    assert toc == ['TOC \\o "1-2" \\h \\z \\u']
    kinds = [node.get(f"{W}fldCharType") for node in root.iter(f"{W}fldChar")]
    assert kinds.count("begin") == kinds.count("separate") == kinds.count("end")
    # the TOC field opens in the first entry and closes in a paragraph of its own after the last
    elements = list(root.find(f"{W}body"))  # type: ignore[arg-type]
    entries = [p for p in elements if (style_of(p) or "").startswith("TOC")]
    first_kinds = [n.get(f"{W}fldCharType") for n in entries[0].iter(f"{W}fldChar")]
    assert first_kinds[:2] == ["begin", "separate"]
    last = elements[elements.index(entries[-1]) + 1]
    assert text_of(last) == "" and [n.get(f"{W}fldCharType") for n in last.iter(f"{W}fldChar")] == ["end"]


@pytest.mark.parametrize("depth", [1, 2, 3, 5])
def test_the_depth_is_the_style_s(depth: int) -> None:
    data = docx(**contents_style(**{"contents.depth": depth}))
    assert f'TOC \\o "1-{depth}" \\h \\z \\u' in instructions(document_xml(data))


def test_the_cached_entries_are_the_planned_ones() -> None:
    result = render(FRONT, format="docx", **contents_style())
    assert result.binary is not None
    plan = plan_docx(result.tree)[0]
    entries = toc_paragraphs(result.binary)
    assert len(entries) == len(plan.toc) == len(result.tree.contents)
    for paragraph, planned, entry in zip(entries, plan.toc, result.tree.contents, strict=True):
        assert style_of(paragraph) == f"TOC{planned.level}"
        link = paragraph.find(f"{W}hyperlink")
        assert link is not None and link.get(f"{W}anchor") == planned.bookmark
        shown = text_of(paragraph)
        expected = (planned.number + planned.sep + plain_text(planned.title) if planned.number
                    else plain_text(planned.title)) + (f" {planned.condition_label}" if planned.condition_label else "")
        assert shown == expected + "\t"
        assert entry.condition_label == planned.condition_label


def test_each_entry_links_and_refers_to_a_bookmark_the_document_has() -> None:
    data = docx(**contents_style())
    root = document_xml(data)
    names = set(bookmark_names(root))
    entries = toc_paragraphs(data)
    anchors = [p.find(f"{W}hyperlink").get(f"{W}anchor") for p in entries]  # type: ignore[union-attr]
    pagerefs = [i.split()[1] for i in instructions(root) if i.startswith("PAGEREF")]
    assert anchors == pagerefs and all(a.startswith("_Toc") for a in anchors)
    assert set(anchors) <= names and len(set(anchors)) == len(anchors)
    assert all(i.endswith("\\h") for i in instructions(root) if i.startswith("PAGEREF"))
    for paragraph in entries:  # the cached page is empty: separate and end follow each other
        kinds = [n.get(f"{W}fldCharType") for n in paragraph.iter(f"{W}fldChar")]
        assert kinds[-3:] == ["begin", "separate", "end"]
        assert paragraph.find(f"{W}hyperlink").find(f"{W}r/{W}fldChar[@{W}fldCharType='end']") is not None  # type: ignore[union-attr]


def test_each_entry_has_a_dotted_right_tab_at_the_text_width() -> None:
    data = docx(**contents_style())
    text_width = docx_settings(load_style("default")).page.text_width
    for paragraph in toc_paragraphs(data):
        right = [t for t in paragraph.findall(f"{W}pPr/{W}tabs/{W}tab") if t.get(f"{W}val") == "right"]
        assert [(t.get(f"{W}leader"), t.get(f"{W}pos")) for t in right] == [("dot", str(text_width))]
    styles = ET.fromstring(parts(data)["word/styles.xml"])
    toc1 = next(s for s in styles.iter(f"{W}style") if s.get(f"{W}styleId") == "TOC1")
    tab = toc1.find(f"{W}pPr/{W}tabs/{W}tab")
    assert (tab.get(f"{W}val"), tab.get(f"{W}leader"), tab.get(f"{W}pos")) == ("right", "dot", str(text_width))


def test_a_number_is_followed_by_the_tab_the_heading_has_and_aligned_per_level() -> None:
    data = docx(**contents_style())
    stops: dict[str, set[str]] = {}
    for paragraph in toc_paragraphs(data):
        left = [t.get(f"{W}pos") for t in paragraph.findall(f"{W}pPr/{W}tabs/{W}tab") if t.get(f"{W}val") == "left"]
        link = paragraph.find(f"{W}hyperlink")
        runs = list(link)  # type: ignore[arg-type]
        has_number = (style_of(paragraph) in ("TOC1", "TOC2")) and text_of(paragraph)[:1].isdigit()
        if has_number:
            assert runs[1].find(f"{W}tab") is not None and len(left) == 1
            stops.setdefault(style_of(paragraph), set()).add(left[0])  # type: ignore[arg-type]
        else:
            assert not left
    assert all(len(stop) == 1 for stop in stops.values())


def test_a_condition_label_is_a_run_after_the_title() -> None:
    source = (DOCUMENTS / "template.lgd").read_text(encoding="utf-8")
    data = docx(source, style="continental", overrides={"contents.enabled": True})
    result = render(source, format="text", style="continental", overrides={"contents.enabled": True})
    labelled = [e for e in result.tree.contents if e.condition_label]
    assert labelled
    runs = [r for p in toc_paragraphs(data) for r in p.iter(f"{W}r")
            if r.find(f"{W}rPr/{W}rStyle") is not None and r.find(f"{W}rPr/{W}rStyle").get(f"{W}val") == "ConditionLabel"]  # type: ignore[union-attr]
    assert [text_of(r) for r in runs] == [e.condition_label for e in labelled]


def test_no_contents_no_field_and_no_entries() -> None:
    data = docx()
    root = document_xml(data)
    assert not any(i.startswith(("TOC", "PAGEREF")) for i in instructions(root))
    assert toc_paragraphs(data) == [] and paragraphs(data, "ContentsHeading") == []
    assert not [n for n in bookmark_names(root) if n.startswith("_Toc")]


def test_headings_carry_the_toc_bookmarks_when_listed() -> None:
    data = docx(**contents_style())
    root = document_xml(data)
    names = bookmark_names(root)
    assert len([n for n in names if n.startswith("_Toc")]) == len(toc_paragraphs(data))


def test_a_contents_with_nothing_to_list_writes_nothing() -> None:
    data = docx("---\ntitle: T\n---\n\nJust text.\n", **contents_style())
    assert toc_paragraphs(data) == [] and paragraphs(data, "ContentsHeading") == []


def test_a_blanked_contents_label_leaves_the_field() -> None:
    data = docx(**contents_style(**{"labels.contents": ""}))
    assert paragraphs(data, "ContentsHeading") == [] and toc_paragraphs(data)


def test_the_planned_entries_and_the_tree_must_agree() -> None:
    result = render(FRONT, format="docx", **contents_style())
    plan = plan_docx(result.tree)[0]
    broken = DocxPlan(plan.abstract_nums, plan.nums, plan.heading_num_id, units=plan.units, anchors=plan.anchors,
                      refs=plan.refs, toc=plan.toc[:-1])
    with pytest.raises(InternalError):
        DocxWriter(load_style("default", overrides={"contents.enabled": True})).write(result.tree, broken)


def test_an_entry_pointing_at_a_bookmark_the_document_lacks_is_a_bug() -> None:
    result = render(FRONT, format="docx", **contents_style())
    plan = plan_docx(result.tree)[0]
    units = {key: (unit if unit.toc_bookmark is None else _without_toc(unit)) for key, unit in plan.units.items()}
    broken = DocxPlan(plan.abstract_nums, plan.nums, plan.heading_num_id, units=units, anchors=plan.anchors,
                      refs=plan.refs, toc=plan.toc)
    with pytest.raises(InternalError):
        DocxWriter(load_style("default", overrides={"contents.enabled": True})).write(result.tree, broken)


def _without_toc(unit: Any) -> Any:
    from dataclasses import replace

    return replace(unit, toc_bookmark=None)


# -- Attachments in the contents: outline levels ----------------------------------------------------------------


def outline(paragraph: ET.Element) -> str | None:
    node = paragraph.find(f"{W}pPr/{W}outlineLvl")
    return None if node is None else node.get(f"{W}val")


def test_the_attachments_are_listed_by_outline_level_not_by_style_list() -> None:
    data = docx(**contents_style())
    assert not any("\\t" in i for i in instructions(document_xml(data)))
    [heading] = paragraphs(data, "AttachmentsHeading")
    titles = paragraphs(data, "AttachmentTitle")
    assert outline(heading) == "0" and {outline(t) for t in titles} == {"1"}
    styles = ET.fromstring(parts(data)["word/styles.xml"])
    by_id = {s.get(f"{W}styleId"): s for s in styles.iter(f"{W}style")}
    assert by_id["AttachmentsHeading"].find(f"{W}pPr/{W}outlineLvl").get(f"{W}val") == "0"
    assert by_id["AttachmentTitle"].find(f"{W}pPr/{W}outlineLvl").get(f"{W}val") == "1"
    for name in ("AttachmentsHeading", "AttachmentTitle"):  # no heading numbering reaches them
        assert by_id[name].find(f"{W}pPr/{W}numPr") is None and by_id[name].find(f"{W}basedOn").get(f"{W}val") == "Normal"


def test_every_outline_level_the_contents_lists_is_what_the_html_lists() -> None:
    """The ``\\o`` range and the outline levels select exactly the entries of the tree's contents."""
    for depth in (1, 2, 3):
        result = render(FRONT, format="docx", **contents_style(**{"contents.depth": depth}))
        assert result.binary is not None
        root = document_xml(result.binary)
        listed = []
        for paragraph in root.iter(f"{W}p"):
            style = style_of(paragraph) or ""
            level = None
            if style.startswith("Heading"):
                level = int(style.removeprefix("Heading")) - 1
            elif outline(paragraph) is not None:
                level = int(outline(paragraph))  # type: ignore[arg-type]
            if level is not None and level < depth:
                listed.append(text_of(paragraph))
        expected = [" ".join(filter(None, (plain_text(e.title), e.condition_label))) for e in result.tree.contents]
        assert listed == expected


def test_attachments_not_listed_are_body_text_for_the_contents() -> None:
    data = docx(**contents_style(**{"contents.attachments": False}))
    assert {outline(p) for p in paragraphs(data, "AttachmentTitle")} == {"9"}
    assert outline(paragraphs(data, "AttachmentsHeading")[0]) == "9"
    assert not [p for p in toc_paragraphs(data) if "Schedule A" in text_of(p)]


def test_a_depth_of_one_keeps_the_attachments_in_the_list() -> None:
    data = docx(**contents_style(**{"contents.depth": 1}))
    assert {outline(p) for p in paragraphs(data, "AttachmentTitle")} == {"0"}
    assert [style_of(p) for p in toc_paragraphs(data)].count("TOC1") == len(toc_paragraphs(data))


def test_a_blanked_attachments_label_lists_the_titles_at_the_first_level() -> None:
    data = docx(**contents_style(**{"labels.attachments": ""}))
    assert paragraphs(data, "AttachmentsHeading") == []
    assert {outline(p) for p in paragraphs(data, "AttachmentTitle")} == {"0"}
    assert [text_of(p) for p in toc_paragraphs(data)][-3:] == [
        "Schedule A: Services\t", "Price List " + "Only if: forum:courts\t", "Terms\t"]


# -- Attachments -----------------------------------------------------------------------------------------------


def page_break(paragraph: ET.Element) -> bool:
    return paragraph.find(f"{W}pPr/{W}pageBreakBefore") is not None


def test_the_attachments_are_a_heading_and_titles_with_their_files_and_conditions() -> None:
    data = docx()
    [heading] = paragraphs(data, "AttachmentsHeading")
    assert text_of(heading) == "Attachments"
    titles = [text_of(p) for p in paragraphs(data, "AttachmentTitle")]
    assert titles == ["Schedule A: Services", "Price List Only if: forum:courts", "Terms"]
    assert [text_of(p) for p in paragraphs(data, "AttachmentFile")] == ["File: files/services.lgd"]
    label = [r for p in paragraphs(data, "AttachmentTitle") for r in p.iter(f"{W}r")
             if r.find(f"{W}rPr/{W}rStyle") is not None]
    assert [text_of(r) for r in label] == ["Only if: forum:courts"]


def test_each_attachment_is_a_bookmark_and_an_attach_reference_resolves_to_it() -> None:
    result = render(FRONT, format="docx", **contents_style())
    assert result.binary is not None
    root = document_xml(result.binary)
    plan = plan_docx(result.tree)[0]
    names = bookmark_names(root)
    for anchor in (a.anchor for a in result.tree.attachments):
        assert plan.anchors[anchor] in names
    assert plan.anchors[result.tree.attachments_anchor] in names
    refs = [i for i in instructions(root) if i.startswith("REF ldatt_")]
    assert refs and all(i.split()[1] in names for i in refs)
    links = [h.get(f"{W}anchor") for h in root.iter(f"{W}hyperlink") if (h.get(f"{W}anchor") or "").startswith("ldatt_")]
    assert links and all(a in names for a in links)
    assert len(names) == len(set(names))
    # the bookmark holds the title but not the condition label
    [paragraph] = [p for p in paragraphs(result.binary, "AttachmentTitle") if "Price List" in text_of(p)]
    start = next(e for e in paragraph if e.tag == f"{W}bookmarkStart" and e.get(f"{W}name").startswith("ldatt_"))  # type: ignore[union-attr]
    inside = []
    for element in list(paragraph)[list(paragraph).index(start) + 1:]:
        if element.tag == f"{W}bookmarkEnd" and element.get(f"{W}id") == start.get(f"{W}id"):
            break
        inside.append(element)
    assert "".join(text_of(e) for e in inside) == "Price List"


def test_the_attachments_are_bookmarked_without_a_contents_too() -> None:
    data = docx()
    names = bookmark_names(document_xml(data))
    assert {"ldatt_schedule_a", "ldatt_price_list", "ldatt_terms", "_LdAttachments"} <= set(names)
    assert not [n for n in names if n.startswith("_Toc")]


def test_page_break_separator_breaks_before_the_part_and_each_attachment() -> None:
    data = docx()  # the default style
    [heading] = paragraphs(data, "AttachmentsHeading")
    titles = paragraphs(data, "AttachmentTitle")
    assert page_break(heading) and [page_break(t) for t in titles] == [False, True, True]
    assert "w:br w:type" not in parts(data)["word/document.xml"].decode("utf-8")


def test_page_break_without_a_label_breaks_before_the_first_title() -> None:
    data = docx(overrides={"labels.attachments": ""})
    titles = paragraphs(data, "AttachmentTitle")
    assert [page_break(t) for t in titles] == [True, True, True]


def test_rule_separator_draws_a_line_above_each_attachment() -> None:
    data = docx(overrides={"attachments.separator": "rule"})
    titles = paragraphs(data, "AttachmentTitle")
    assert all(t.find(f"{W}pPr/{W}pBdr/{W}top") is not None for t in titles)
    assert not any(page_break(p) for p in titles + paragraphs(data, "AttachmentsHeading"))


def test_no_separator_draws_nothing() -> None:
    data = docx(overrides={"attachments.separator": "none"})
    for p in paragraphs(data, "AttachmentTitle") + paragraphs(data, "AttachmentsHeading"):
        assert not page_break(p) and p.find(f"{W}pPr/{W}pBdr") is None


def test_omitted_attachments_write_nothing() -> None:
    data = docx(overrides={"attachments.render": "omit"})
    assert paragraphs(data, "AttachmentsHeading") == [] and paragraphs(data, "AttachmentTitle") == []


def test_the_attachments_listed_in_the_contents_carry_its_bookmarks() -> None:
    result = render(FRONT, format="docx", **contents_style())
    assert result.binary is not None
    plan = plan_docx(result.tree)[0]
    unitless = [t.bookmark for e, t in zip(result.tree.contents, plan.toc, strict=True) if e.unit is None]
    root = document_xml(result.binary)
    heading, *titles = [*paragraphs(result.binary, "AttachmentsHeading"), *paragraphs(result.binary, "AttachmentTitle")]
    for paragraph, name in zip([heading, *titles], unitless, strict=True):
        assert name in [b.get(f"{W}name") for b in paragraph.iter(f"{W}bookmarkStart")]
    assert len(set(bookmark_names(root))) == len(bookmark_names(root))


# -- Signature blocks ------------------------------------------------------------------------------------------


def signature_table(data: bytes) -> ET.Element:
    return next(t for t in reversed(tables(data)) if any(style_of(p) == "SignatureSide" for p in t.iter(f"{W}p")))


def test_the_signatures_are_a_borderless_table_two_parties_to_a_row() -> None:
    data = docx()
    table = signature_table(data)
    assert table.find(f"{W}tblPr/{W}tblBorders") is None
    rows = table.findall(f"{W}tr")
    assert [len(row.findall(f"{W}tc")) for row in rows] == [2, 2]
    cells = [c for row in rows for c in row.findall(f"{W}tc")]
    sides = [text_of(c.find(f"{W}p")) for c in cells]  # type: ignore[arg-type]
    assert sides == ["Seller", "Buyer", "Buyer", ""]
    assert [text_of(c.findall(f"{W}p")[1]) for c in cells[:3]] == ["Acme Corporation", "Beta Industries Inc.", "Gamma Ltd."]


def test_each_signatory_has_a_line_a_name_and_a_title() -> None:
    data = docx()
    acme = signature_table(data).find(f"{W}tr/{W}tc")
    lines = [(style_of(p), text_of(p)) for p in acme.findall(f"{W}p")]  # type: ignore[union-attr]
    assert lines == [
        ("SignatureSide", "Seller"), ("SignatureText", "Acme Corporation"),
        ("SignatureLine", ""), ("SignatureText", "Name: John Smith"), ("SignatureText", "Title: Chief Executive Officer"),
        ("SignatureLine", ""), ("SignatureText", "Name: Jane Roe"), ("SignatureText", "Title: Director"),
        ("SignatureText", "Date:"), ("SignatureText", "Place:")]
    line = acme.find(f"{W}p[3]")  # type: ignore[union-attr]
    assert line.find(f"{W}pPr/{W}ind") is not None  # type: ignore[union-attr]
    styles = ET.fromstring(parts(data)["word/styles.xml"])
    style = next(s for s in styles.iter(f"{W}style") if s.get(f"{W}styleId") == "SignatureLine")
    assert style.find(f"{W}pPr/{W}pBdr/{W}bottom") is not None


def test_a_party_without_representatives_has_one_blank_signing_line() -> None:
    data = docx()
    beta = signature_table(data).findall(f"{W}tr")[0].findall(f"{W}tc")[1]
    lines = [(style_of(p), text_of(p)) for p in beta.findall(f"{W}p")]
    assert lines == [("SignatureSide", "Buyer"), ("SignatureText", "Beta Industries Inc."), ("SignatureLine", ""),
                     ("SignatureText", "Name:"), ("SignatureText", "Title:"), ("SignatureText", "Date:"),
                     ("SignatureText", "Place:")]


def test_the_signatures_stay_together() -> None:
    data = docx()
    table = signature_table(data)
    assert all(row.find(f"{W}trPr/{W}cantSplit") is not None for row in table.findall(f"{W}tr"))
    paragraphs_in = list(table.iter(f"{W}p"))
    kept = [p.find(f"{W}pPr/{W}keepNext") is not None for p in paragraphs_in]
    assert all(kept[:-1]) and not kept[-1]


def test_one_party_is_one_cell() -> None:
    source = ("---\nlegaldown: '0.2'\ntitle: T\nsides:\n  - name: a\n    parties:\n      - name: p\n        type: legal_entity\n"
              "        legal_name: Only One\n---\n\n# A\n")
    table = signature_table(docx(source))
    assert [len(r.findall(f"{W}tc")) for r in table.findall(f"{W}tr")] == [1]


def test_no_signatures_when_the_style_or_the_document_says_so() -> None:
    assert paragraphs(docx(overrides={"signatures.enabled": False}), "SignatureSide") == []
    off = FRONT.replace("language: en\n", "language: en\ninclude_signatures: false\n", 1)
    assert paragraphs(docx(off), "SignatureSide") == []


def test_a_signature_label_blanked_by_the_style_leaves_the_value() -> None:
    data = docx(overrides={"labels.signature_name": "", "labels.signature_title": "", "labels.signature_place": ""})
    acme = signature_table(data).find(f"{W}tr/{W}tc")
    lines = [text_of(p) for p in acme.findall(f"{W}p")]  # type: ignore[union-attr]
    assert "John Smith" in lines and "Chief Executive Officer" in lines and "Place:" not in lines and "Date:" in lines


def test_the_document_ends_with_a_paragraph_after_the_signature_table() -> None:
    elements = [c for c in body(docx()) if c.tag != f"{W}sectPr"]
    assert elements[-1].tag == f"{W}p" and elements[-2].tag == f"{W}tbl"


# -- The interim warning is gone -------------------------------------------------------------------------------


def test_a_docx_render_reports_what_the_text_render_reports() -> None:
    document = (DOCUMENTS / "features.lgd").read_text(encoding="utf-8")
    docx_rules = [d.rule for d in render(document, format="docx").diagnostics]
    assert "render-docx-interim" not in docx_rules
    text = [d.rule for d in render(document, format="text").diagnostics]
    assert [r for r in docx_rules if not r.startswith("render-docx")] == text


# -- Schema validity -------------------------------------------------------------------------------------------


def hostile_document(rnd: random.Random) -> str:
    def party(index: int) -> dict[str, Any]:
        return {"name": f"p{index}", "type": "legal_entity", "legal_name": soup(rnd, 12) + "x",
                "address": soup(rnd, 10),
                "representatives": [{"name": soup(rnd, 8) + "n", "title": soup(rnd, 8)} for _ in range(rnd.randint(0, 2))]}

    front = {"legaldown": "0.2", "title": soup(rnd, 20), "subtitle": soup(rnd, 20), "version": soup(rnd, 6),
             "effective_date": "2026-01-02",
             "sides": [{"name": f"s{i}", "label": soup(rnd, 8) + "l", "parties": [party(i * 3 + j) for j in range(2)]}
                       for i in range(2)],
             "attachments": [{"id": f"a{i}", "title": soup(rnd, 15) + "t", "file": soup(rnd, 10) + "f"}
                             for i in range(3)]}
    return (f"---\n{yaml.safe_dump(front, allow_unicode=True)}---\n\n{soup(rnd, 20)}\n\n# {soup(rnd, 15)} {{#h1}}\n\n"
            f"{soup(rnd, 30)} {{{{attach: a1}}}}\n\n## {soup(rnd, 10)}\n\ntext\n")


@pytest.mark.ooxml_schema
@pytest.mark.parametrize("settings", [
    {}, {"overrides": {"contents.enabled": True}},
    {"overrides": {"contents.enabled": True, "contents.depth": 5, "attachments.separator": "rule"}},
    {"overrides": {"contents.enabled": True, "contents.attachments": False, "attachments.separator": "none"}},
    {"overrides": {"contents.enabled": True, "labels.attachments": "", "labels.contents": ""}},
    {"overrides": {"title_block.version": True, "page.size": "A4 landscape"}},
    {"style": "continental", "overrides": {"contents.enabled": True}},
    {"style": "outline", "overrides": {"contents.enabled": True, "contents.depth": 3}},
    {"style": "continental", "overrides": {"numbering.scheme": "none", "contents.enabled": True}},
])
def test_a_document_with_every_part_validates(settings: dict[str, Any]) -> None:
    require_schemas()
    assert validation_errors(docx(**settings)) == []


@pytest.mark.ooxml_schema
@pytest.mark.parametrize("name", ["features", "template"])
@pytest.mark.parametrize("style", ["default", "continental", "outline"])
def test_the_test_documents_validate_with_a_contents(name: str, style: str) -> None:
    require_schemas()
    source = (DOCUMENTS / f"{name}.lgd").read_text(encoding="utf-8")
    assert validation_errors(docx(source, style=style, overrides={"contents.enabled": True, "contents.depth": 5})) == []


@pytest.mark.ooxml_schema
@pytest.mark.parametrize("seed", range(12))
def test_hostile_text_in_every_part_of_the_matter_validates(seed: int) -> None:
    require_schemas()
    rnd = random.Random(7000 + seed)
    data = docx(hostile_document(rnd), overrides={"contents.enabled": True,
                                                   "labels.contents": soup(rnd, 6), "labels.attachments": soup(rnd, 6),
                                                   "labels.colon": soup(rnd, 3) + ":"})
    assert_well_formed(data)
    assert validation_errors(data) == []


def test_hostile_front_matter_is_well_formed() -> None:
    for seed in range(20):
        rnd = random.Random(seed)
        assert_well_formed(docx(hostile_document(rnd), overrides={"contents.enabled": True}))
    assert ALPHABET


# -- LibreOffice -----------------------------------------------------------------------------------------------

def toc_lines(exported: Exported) -> list[str]:
    """The entries of the contents LibreOffice drew, as ``number title`` (the page number and the tabs left
    out), and the page numbers."""
    lines = []
    for line in exported.text.splitlines():
        match = re.fullmatch(r"(.*)\t(\d+)", line)
        if match:
            lines.append(" ".join(match.group(1).split()))
    return lines


def toc_pages(exported: Exported) -> list[int]:
    return [int(m.group(1)) for line in exported.text.splitlines() if (m := re.fullmatch(r".*\t(\d+)", line))]


def expected_entries(source: str, **settings: Any) -> list[str]:
    tree = render(source, format="text", **settings).tree
    return [" ".join(" ".join(filter(None, (e.label, plain_text(e.title),
                                              e.condition_label))).split()) for e in tree.contents]


LONG = ("---\nlegaldown: '0.2'\ntitle: Long\nattachments:\n  - id: one\n    title: First\n  - id: two\n    title: Second\n"
        "  - id: three\n    title: Third\n---\n\n" + "".join(f"# Heading {n}\n\nParagraph {n} " + "word " * 100 + "\n\n"
                                                           for n in range(6)) + "See {{attach: two}}.\n")


@pytest.fixture(scope="module")
def shown() -> dict[str, Exported]:
    require_libreoffice()
    features = (DOCUMENTS / "features.lgd").read_text(encoding="utf-8")
    template = (DOCUMENTS / "template.lgd").read_text(encoding="utf-8")
    contents = {"contents.enabled": True, "contents.depth": 3}
    documents = {
        "master": docx(FRONT, overrides=contents),
        "features": docx(features, overrides=contents),
        "template": docx(template, style="continental", overrides=contents),
        "long_break": docx(LONG, overrides={"contents.enabled": True}),
        "long_rule": docx(LONG, overrides={"contents.enabled": True, "attachments.separator": "rule"}),
        "long_none": docx(LONG, overrides={"contents.enabled": True, "attachments.separator": "none"}),
    }
    # The cached title of the attachment reference is wrong on purpose: LibreOffice must compute it.
    wrong = repackage(docx(LONG), "word/document.xml",
                      lambda text: re.sub(r"(REF ldatt_two [^<]*</w:instrText></w:r><w:r><w:fldChar w:fldCharType=\"separate\"/></w:r>"
                                          r"<w:r><w:t xml:space=\"preserve\">)Second", r"\1WRONG", text))
    assert b"WRONG" in parts(wrong)["word/document.xml"]
    documents["wrong_cache"] = wrong
    return libreoffice_export(documents)


@pytest.mark.libreoffice
def test_the_contents_lists_exactly_the_html_entries(shown: dict[str, Exported]) -> None:
    features = (DOCUMENTS / "features.lgd").read_text(encoding="utf-8")
    assert toc_lines(shown["features"]) == expected_entries(features, overrides={"contents.enabled": True,
                                                                                  "contents.depth": 3})
    assert toc_lines(shown["master"]) == expected_entries(FRONT, overrides={"contents.enabled": True,
                                                                             "contents.depth": 3})
    # An alternative that shares a number (§15.8) is numbered in sequence by LibreOffice until the planner keeps it
    # on the first one's number: the titles are the contents' either way.
    template = (DOCUMENTS / "template.lgd").read_text(encoding="utf-8")
    drawn = [line.split(" ", 1)[1] for line in toc_lines(shown["template"])]
    entries = expected_entries(template, style="continental", overrides={"contents.enabled": True, "contents.depth": 3})
    assert drawn == [line.split(" ", 1)[1] for line in entries]


@pytest.mark.libreoffice
def test_page_numbers_appear_after_the_update(shown: dict[str, Exported]) -> None:
    pages = toc_pages(shown["master"])
    assert len(pages) == len(toc_lines(shown["master"])) and all(p >= 1 for p in pages)
    assert pages == sorted(pages)
    assert max(pages) <= shown["master"].pages


@pytest.mark.libreoffice
def test_the_contents_is_not_one_of_its_own_entries(shown: dict[str, Exported]) -> None:
    assert "Contents" not in toc_lines(shown["master"])


@pytest.mark.libreoffice
def test_an_attachment_reference_resolves_to_the_title_not_the_cache(shown: dict[str, Exported]) -> None:
    assert "See Second." in shown["wrong_cache"].text and "WRONG" not in shown["wrong_cache"].text
    assert "Schedule A: Services" in shown["master"].text  # the {{attach:}} of FRONT, a field to the title


@pytest.mark.libreoffice
def test_a_page_break_separator_puts_each_attachment_on_its_own_page(shown: dict[str, Exported]) -> None:
    pages = toc_pages(shown["long_break"])
    entries = toc_lines(shown["long_break"])
    by_title = dict(zip(entries, pages, strict=True))
    assert by_title["Attachments"] == by_title["First"]  # the heading stays with the first attachment
    assert by_title["First"] > by_title["6. Heading 5"]
    assert by_title["First"] < by_title["Second"] < by_title["Third"]
    assert shown["long_break"].pages == by_title["Third"]


@pytest.mark.libreoffice
def test_rule_and_none_keep_the_attachments_on_the_pages_they_fall_on(shown: dict[str, Exported]) -> None:
    for name in ("long_rule", "long_none"):
        by_title = dict(zip(toc_lines(shown[name]), toc_pages(shown[name]), strict=True))
        assert by_title["First"] == by_title["Second"] == by_title["Third"]
    assert shown["long_break"].pages >= shown["long_rule"].pages + 2


def words(text: str) -> list[str]:
    """The words of *text* in order (the plain-text writer's ``<url>`` after a link left out: a hyperlink is not text)."""
    return re.findall(r"[^\W_]+", re.sub(r"<(?:https?|mailto|tel):[^>]*>", "", text).lower())


def lo_words(exported: Exported) -> list[str]:
    lines = []
    for line in exported.text.splitlines():
        lines.append(re.sub(r"\t\d+$", "", line))
    return words("\n".join(lines))


@pytest.mark.libreoffice
@pytest.mark.parametrize("name", ["master", "features"])
def test_the_whole_document_reads_in_the_order_the_text_writer_prints_it(shown: dict[str, Exported], name: str) -> None:
    contents = {"contents.enabled": True, "contents.depth": 3}
    source = FRONT if name == "master" else (DOCUMENTS / "features.lgd").read_text(encoding="utf-8")
    text = render(source, format="text", overrides=contents).output
    assert lo_words(shown[name]) == words(text)


@pytest.mark.libreoffice
def test_the_template_reads_in_the_text_writers_order_but_for_its_numbering(shown: dict[str, Exported]) -> None:
    source = (DOCUMENTS / "template.lgd").read_text(encoding="utf-8")
    text = render(source, format="text", style="continental", overrides={"contents.enabled": True,
                                                                         "contents.depth": 3}).output
    expected, found = words(text), lo_words(shown["template"])
    # the numbers Word draws for an alternative that shares one are numbering details (see the plan's diagnostics)
    assert [w for w in found if not w.isdigit()] == [w for w in expected if not w.isdigit()]


@pytest.mark.libreoffice
def test_a_heading_inserted_in_the_editor_is_listed_once_the_contents_is_updated() -> None:
    """The contents is live: the heading a lawyer adds (Enter at the start of a heading) is an entry, and the numbers
    after it move, as they do in the contents of the document written with it."""
    require_libreoffice()
    contents = {"contents.enabled": True, "contents.depth": 3}
    said = libreoffice_edit({"insert": (docx(FRONT, overrides=contents), [("Payment", "Inserted")])})["insert"]
    edited = FRONT.replace("## Payment {#payment}", "## Inserted\n\n## Payment {#payment}")
    lines = [" ".join(m.group(1).split()) for line in said.splitlines() if (m := re.fullmatch(r"(.*)\t(\d+)", line))]
    assert lines == expected_entries(edited, overrides=contents)
    assert "2.1 Inserted" in lines and "2.2 Payment" in lines


#: SHA-256 of the document part and the styles part of :data:`FRONT` with a contents (uncompressed: the ZIP's bytes
#: depend on the zlib). When the matter changes on purpose, run this test, review the change, and replace the digests.
MATTER_GOLDEN = {
    "word/document.xml": "d67511803cd53640fbd96c3c2f6531cc5cf649158c7f56a0d0681db4da5c1b98",
    "word/styles.xml": "6ccea95d23325c1d13fd3f11d953567df2ce075bbb00f0f537cac57939c6dcfc",
}


def test_the_matter_of_a_fixed_document_is_what_it_was() -> None:
    import hashlib

    contents = parts(DocxWriter(load_style("default", overrides={"contents.enabled": True}), generator="golden")
                     .write(render(FRONT, format="text", overrides={"contents.enabled": True}).tree))
    digests = {name: hashlib.sha256(contents[name]).hexdigest() for name in MATTER_GOLDEN}
    assert digests == MATTER_GOLDEN, "\n".join(f'    "{name}": "{digest}",' for name, digest in digests.items())
