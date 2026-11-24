"""Typography of single elements (#14): ``elements.quote``, ``list``,
``table``, ``parties``, ``signatures`` and ``defined_term``, read by the
HTML and DOCX writers, and ``signatures.page_break_before``. Unset, they
change nothing: the stylesheet and HTML goldens are as they were, and the
DOCX only names its list, table and party styles (test_docx_package.py)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from docx_support import W, parts, require_schemas, validation_errors

from legaldown_render import StyleError, load_style, render
from legaldown_render.writers import stylesheet

SOURCE = """---
title: Agreement
sides:
  - name: buyers
    label: Buyer
    parties:
      - name: acme
        type: legal_entity
        legal_name: Acme Corporation
        registered_address: 1 Main Street
  - name: sellers
    label: Seller
    parties:
      - name: beta
        type: legal_entity
        legal_name: Beta Ltd
effective_date: 2026-01-02
---

# Terms {#terms}

"Fee" {{def: fee}} means the amount.

> A quote.
>
> - quoted item
>
> > A quote in a quote.

- item one

  a second paragraph of item one

  - nested item

| Column | Other |
|---|---|
| cell | cell |

See {{term: fee}}.
"""

EVERYTHING = {
    "elements.quote.font_family": "Arial", "elements.quote.font_size": "10pt", "elements.quote.color": "#333333",
    "elements.quote.line_height": "1.2", "elements.quote.space_before": "6pt", "elements.quote.space_after": "1em",
    "elements.quote.indent": "2em",
    "elements.list.font_family": "Georgia", "elements.list.font_size": "0.9em", "elements.list.color": "navy",
    "elements.list.line_height": "14pt", "elements.list.item_spacing": "3pt",
    "elements.table.font_family": "Verdana", "elements.table.font_size": "9pt", "elements.table.color": "#444",
    "elements.parties.font_family": "Calibri", "elements.parties.font_size": "12pt", "elements.parties.color": "teal",
    "elements.signatures.font_family": "Times", "elements.signatures.font_size": "10pt",
    "elements.signatures.color": "maroon",
    "elements.defined_term.font_family": "Courier", "elements.defined_term.font_size": "11pt",
    "elements.defined_term.color": "#990000",
    "signatures.page_break_before": True,
}


def style(**overrides: object):
    return load_style("default", overrides=overrides)


# -- HTML ------------------------------------------------------------------------

def test_unset_elements_add_no_rule() -> None:
    assert stylesheet(style()) == stylesheet(load_style("default"))
    for name in ("quote", "list", "table", "parties", "signatures", "defined_term"):
        assert stylesheet(style(**{f"elements.{name}.color": None})) == stylesheet(load_style("default"))


def test_each_element_gets_its_rule() -> None:
    css = stylesheet(style(**EVERYTHING))
    assert (".ld-quote { font-family: Arial; font-size: 10pt; color: #333333; line-height: 1.2; margin-top: 6pt; "
            "margin-bottom: 1em; padding-left: 2em; }") in css
    assert ".ld-quote .ld-quote { font-size: inherit; }" in css
    assert ".ld-list { font-family: Georgia; font-size: 0.9em; color: navy; line-height: 14pt; }" in css
    assert ".ld-list .ld-list { font-size: inherit; }" in css
    assert ".ld-list > li { margin-bottom: 3pt; }" in css
    assert ".ld-table { font-family: Verdana; font-size: 9pt; color: #444; }" in css
    assert ".ld-parties { font-family: Calibri; font-size: 12pt; color: teal; }" in css
    assert ".ld-signatures { font-family: Times; font-size: 10pt; color: maroon; }" in css
    assert "dfn.ld-defined { font-family: Courier; font-size: 11pt; color: #990000; }" in css
    assert "@media print {\n  .ld-signatures { break-before: page; }\n}" in css


def test_the_element_rules_follow_the_base_rules() -> None:
    """They override the base rule of the same element (same specificity, later)."""
    css = stylesheet(style(**EVERYTHING))
    assert css.index(".ld-quote { margin: 0.75em 0;") < css.index(".ld-quote { font-family: Arial;")
    assert css.index(".ld-enumerated > li {") < css.index(".ld-list > li {")


def test_no_size_no_nested_reset() -> None:
    css = stylesheet(style(**{"elements.quote.color": "red", "elements.list.color": "red"}))
    assert ".ld-quote .ld-quote" not in css and ".ld-list .ld-list" not in css


def test_the_element_rules_are_scoped_in_the_embedded_sheet() -> None:
    css = stylesheet(style(**EVERYTHING), standalone=False, scope=".preview")
    assert ":where(.preview) .ld-document .ld-quote { font-family: Arial;" in css
    assert ":where(.preview) .ld-document dfn.ld-defined {" in css
    assert "  :where(.preview) .ld-document .ld-signatures { break-before: page; }" in css


def test_the_html_markup_carries_the_classes_the_rules_name() -> None:
    page = render(SOURCE, format="html", style=style(**EVERYTHING)).output
    for marker in ('class="ld-quote"', 'class="ld-list', 'class="ld-table"', 'class="ld-parties"',
                   'class="ld-signatures"', '<dfn class="ld-defined'):
        assert marker in page, marker


def test_element_values_cannot_leave_their_declaration() -> None:
    from legaldown_render.style.model import Elements, QuoteFormat, Style

    hostile = "red;} body { display: none } /* x */ </style>"
    css = stylesheet(Style(elements=Elements(quote=QuoteFormat(color=hostile, indent=hostile))))
    line = next(line for line in css.splitlines() if line.startswith(".ld-quote { color"))
    assert line.count("{") == 1 and line.count("}") == 1 and "/*" not in line and "<" not in line


# -- Validation ---------------------------------------------------------------------

def test_an_untrusted_style_is_held_to_the_grammars() -> None:
    load_style({"elements": {"quote": {"font_size": "10pt", "indent": "2em", "line_height": "1.2"},
                             "table": {"color": "#333"}}}, trusted=False)
    with pytest.raises(StyleError) as caught:
        load_style({"elements": {"quote": {"font_size": "calc(1em + 2px)"}, "list": {"color": "var(--x)"}}},
                   trusted=False)
    assert [problem.split(":")[0] for problem in caught.value.problems] == [
        "elements.quote.font_size", "elements.list.color"]


def test_docx_refuses_what_word_cannot_express() -> None:
    with pytest.raises(StyleError) as caught:
        render(SOURCE, format="docx", style=style(**{"elements.quote.indent": "5%", "elements.list.item_spacing": "-1pt",
                                                     "elements.table.color": "papayawhip",
                                                     "elements.parties.font_size": "2ch"}))
    keys = [problem.split(":")[0] for problem in caught.value.problems]
    assert keys == ["elements.quote.indent", "elements.list.item_spacing", "elements.table.color",
                    "elements.parties.font_size"]
    # HTML takes them.
    render(SOURCE, format="html", style=style(**{"elements.quote.indent": "5%", "elements.table.color": "papayawhip"}))


def test_an_unknown_element_setting_is_refused() -> None:
    with pytest.raises(StyleError, match="elements.quote.weight"):
        style(**{"elements.quote.weight": "bold"})


# -- DOCX -------------------------------------------------------------------------------

def docx_parts(**overrides: object) -> dict[str, ET.Element]:
    data = render(SOURCE, format="docx", style=style(**overrides)).binary
    return {name: ET.fromstring(content) for name, content in parts(data).items() if name.endswith(".xml")}


def styles_by_id(root: ET.Element) -> dict[str, ET.Element]:
    return {node.get(f"{W}styleId"): node for node in root.iter(f"{W}style")}


def run_props(node: ET.Element) -> dict[str, str | None]:
    rpr = node.find(f"{W}rPr")
    assert rpr is not None
    fonts, color, size = rpr.find(f"{W}rFonts"), rpr.find(f"{W}color"), rpr.find(f"{W}sz")
    return {"font": fonts.get(f"{W}ascii") if fonts is not None else None,
            "color": color.get(f"{W}val") if color is not None else None,
            "size": size.get(f"{W}val") if size is not None else None}


@pytest.mark.parametrize(("style_id", "font", "color", "size"), [
    ("Quote", "Arial", "333333", "20"),
    ("ListParagraph", "Georgia", "000080", "20"),  # 0.9em of 11pt is 9.9pt: 20 half-points
    ("TableText", "Verdana", "444444", "18"),
    ("PartySide", "Calibri", "008080", "24"), ("PartyName", "Calibri", "008080", "24"),
    ("PartyDetails", "Calibri", "008080", "24"),
    ("SignatureSide", "Times", "800000", "20"), ("SignatureText", "Times", "800000", "20"),
    ("SignatureLine", "Times", "800000", "20"),
    ("DefinedTerm", "Courier", "990000", "22"),
])
def test_each_element_sets_its_word_style(style_id: str, font: str, color: str, size: str) -> None:
    by_id = styles_by_id(docx_parts(**EVERYTHING)["word/styles.xml"])
    assert run_props(by_id[style_id]) == {"font": font, "color": color, "size": size}


def test_the_quote_and_list_paragraph_properties() -> None:
    by_id = styles_by_id(docx_parts(**EVERYTHING)["word/styles.xml"])
    quote = by_id["Quote"].find(f"{W}pPr")
    spacing, ind = quote.find(f"{W}spacing"), quote.find(f"{W}ind")
    # 6pt before; 1em of the quote's own 10pt after; line 1.2; indent 2em of 10pt.
    assert {k.removeprefix(W): v for k, v in spacing.attrib.items()} == {
        "before": "120", "after": "200", "line": "288", "lineRule": "auto"}
    assert ind.get(f"{W}left") == "400" and ind.get(f"{W}right") == "567"
    listed = by_id["ListParagraph"].find(f"{W}pPr/{W}spacing")
    assert {k.removeprefix(W): v for k, v in listed.attrib.items()} == {"after": "60", "line": "280", "lineRule": "atLeast"}
    assert by_id["ListParagraph"].find(f"{W}pPr/{W}ind") is None  # the numbering indents a list item
    assert by_id["DefinedTerm"].find(f"{W}rPr/{W}b") is not None  # definitions.style stays


def test_rem_is_the_body_size_and_em_the_element_size() -> None:
    by_id = styles_by_id(docx_parts(**{"elements.quote.font_size": "20pt", "elements.quote.indent": "1em",
                                       "elements.quote.space_before": "1rem"})["word/styles.xml"])
    pr = by_id["Quote"].find(f"{W}pPr")
    assert pr.find(f"{W}ind").get(f"{W}left") == "400"  # 1em of 20pt
    assert pr.find(f"{W}spacing").get(f"{W}before") == "220"  # 1rem of the body's 11pt


def test_unset_elements_leave_the_word_styles_as_they_were() -> None:
    by_id = styles_by_id(docx_parts()["word/styles.xml"])
    assert by_id["Quote"].find(f"{W}rPr") is None
    assert by_id["ListParagraph"].find(f"{W}rPr") is None and by_id["ListParagraph"].find(f"{W}pPr") is None
    assert by_id["TableText"].find(f"{W}rPr") is None
    assert by_id["Quote"].find(f"{W}pPr/{W}ind").get(f"{W}left") == "567"


def paragraph_styles(root: ET.Element) -> list[tuple[str | None, str]]:
    found = []
    for paragraph in root.iter(f"{W}p"):
        style_node = paragraph.find(f"{W}pPr/{W}pStyle")
        found.append((style_node.get(f"{W}val") if style_node is not None else None,
                      "".join(node.text or "" for node in paragraph.iter(f"{W}t"))))
    return found


def test_which_paragraphs_take_the_list_table_and_party_styles() -> None:
    used = paragraph_styles(docx_parts()["word/document.xml"])
    assert ("ListParagraph", "item one") in used
    assert ("ListParagraph", "a second paragraph of item one") in used  # the whole item, as in HTML
    assert ("ListParagraph", "nested item") in used
    assert ("TableText", "cell") in used and ("TableText", "Column") in used
    # In a quote the item keeps the quote's style: its bar and indent.
    assert ("Quote", "quoted item") in used and ("Quote", "A quote in a quote.") in used
    # The header rows keep their style; the parties' details take theirs.
    assert ("TitleBlockText", "Effective date") in used and ("TitleBlockText", "January 2, 2026") in used
    assert ("PartyDetails", "1 Main Street") in used
    assert not any(style_id == "TitleBlockText" and "Main Street" in text for style_id, text in used)


def test_a_quote_indent_reaches_what_the_quote_holds() -> None:
    """The list item in the quote is indented by the quote's own indent, as the quote's text is."""
    def item_left(**overrides: object) -> int:
        root = docx_parts(**overrides)["word/document.xml"]
        paragraph = next(p for p in root.iter(f"{W}p")
                         if "".join(t.text or "" for t in p.iter(f"{W}t")) == "quoted item")
        return int(paragraph.find(f"{W}pPr/{W}ind").get(f"{W}left"))
    assert item_left(**{"elements.quote.indent": "2in"}) - item_left() == 2880 - 567


def test_the_signatures_start_a_page_when_asked() -> None:
    def before_signatures(**overrides: object) -> ET.Element:
        body = docx_parts(**overrides)["word/document.xml"].find(f"{W}body")
        children = list(body)
        table = [child for child in children if child.tag == f"{W}tbl"][-1]  # the signature table is the last
        return children[children.index(table) - 1]
    assert before_signatures().find(f"{W}pPr/{W}pageBreakBefore") is None
    breaking = before_signatures(**{"signatures.page_break_before": True})
    assert breaking.find(f"{W}pPr/{W}pageBreakBefore") is not None
    assert breaking.find(f"{W}r") is None  # an empty paragraph
    spacing = breaking.find(f"{W}pPr/{W}spacing")
    assert spacing.get(f"{W}line") == "20" and spacing.get(f"{W}lineRule") == "exact"


def test_the_parts_validate_with_every_element_set() -> None:
    require_schemas()
    data = render(SOURCE, format="docx", style=style(**EVERYTHING)).binary
    assert validation_errors(data) == []


@pytest.mark.libreoffice
def test_libreoffice_starts_the_signatures_on_a_new_page() -> None:
    """One page more, and only one: also after an attachment that starts a page of its own."""
    from docx_support import libreoffice_export, require_libreoffice

    require_libreoffice()
    attached = SOURCE.replace("effective_date:", "attachments:\n  - id: annex\n    title: Annex\n    file: annex.pdf\n"
                              "effective_date:", 1)

    def made(source: str, **overrides: object) -> bytes:
        return render(source, format="docx", style=style(**overrides)).binary
    shown = libreoffice_export({
        "plain": made(SOURCE), "breaking": made(SOURCE, **{"signatures.page_break_before": True}),
        "attached": made(attached), "attached_breaking": made(attached, **{"signatures.page_break_before": True}),
        "everything": made(SOURCE, **EVERYTHING),
    })
    assert shown["breaking"].pages == shown["plain"].pages + 1
    assert shown["attached_breaking"].pages == shown["attached"].pages + 1
    # Every element set: the same words, one page more for the signatures.
    assert [line for line in shown["everything"].lines if line] == [line for line in shown["breaking"].lines if line]
