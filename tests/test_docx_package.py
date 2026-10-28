"""The OOXML package the DOCX writer produces: its parts, relationships and content types,
the settings, styles, headers and footers, properties and page setup generated from the
style template, determinism, and the builders later work reuses. Schema validity is in
test_docx_validation.py, LibreOffice in test_docx_libreoffice.py; this module needs neither."""
from __future__ import annotations

import hashlib
import io
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from conftest import DOCUMENTS, FEATURES, FRONT, run_cli
from docx_support import W_NS, parts, texts

from legaldown_render import RenderOptions, render, render_file
from legaldown_render.cli import main
from legaldown_render.errors import InternalError
from legaldown_render.tree import plain_text
from legaldown_render.writers import FORMATS, DocxWriter, format_for_path
from legaldown_render.writers.docx import ooxml, package, styles
from legaldown_render.writers.docx.headers import template_runs

W = f"{{{W_NS}}}"
SOURCE = ("---\nlegaldown: '0.2'\ntitle: Services Agreement\nsubtitle: A subtitle\nlanguage: cs\nversion: '2.1'\n"
          "effective_date: 2026-03-04\n---\n\nPreamble text.\n\n# First {#first}\n\nBody *one*.\n\n## Nested\n\n"
          "- item a\n- item b\n\n> quoted\n\n```\ncode 1\ncode 2\n```\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n---\n")


def docx(source: str = SOURCE, **settings: object) -> dict[str, bytes]:
    result = render(source, format="docx", **settings)
    assert result.binary is not None
    return parts(result.binary)


def xml(part: bytes) -> ET.Element:
    return ET.fromstring(part)


# -- The package ---------------------------------------------------------------------------


def test_a_package_has_the_parts_a_word_document_needs_in_a_fixed_order() -> None:
    assert list(docx()) == [
        "[Content_Types].xml", "_rels/.rels", "docProps/core.xml", "docProps/app.xml", "word/document.xml",
        "word/_rels/document.xml.rels", "word/styles.xml", "word/numbering.xml", "word/settings.xml",
        "word/footer1.xml"]


def test_content_types_name_every_part() -> None:
    contents = docx()
    root = xml(contents["[Content_Types].xml"])
    ns = "{http://schemas.openxmlformats.org/package/2006/content-types}"
    defaults = {entry.get("Extension"): entry.get("ContentType") for entry in root.iter(f"{ns}Default")}
    overrides = {entry.get("PartName"): entry.get("ContentType") for entry in root.iter(f"{ns}Override")}
    assert defaults == {"rels": "application/vnd.openxmlformats-package.relationships+xml",
                        "xml": "application/xml"}
    assert overrides == {
        "/docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml",
        "/docProps/app.xml": "application/vnd.openxmlformats-officedocument.extended-properties+xml",
        "/word/document.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        "/word/styles.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml",
        "/word/numbering.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml",
        "/word/settings.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml",
        "/word/footer1.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml",
    }


def test_the_relationships_point_at_the_parts() -> None:
    contents = docx()
    ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    package_rels = [(r.get("Id"), r.get("Type").rsplit("/", 1)[1], r.get("Target"))
                    for r in xml(contents["_rels/.rels"]).iter(f"{ns}Relationship")]
    assert package_rels == [("rId1", "officeDocument", "word/document.xml"),
                            ("rId2", "core-properties", "docProps/core.xml"),
                            ("rId3", "extended-properties", "docProps/app.xml")]
    document_rels = [(r.get("Id"), r.get("Type").rsplit("/", 1)[1], r.get("Target"))
                     for r in xml(contents["word/_rels/document.xml.rels"]).iter(f"{ns}Relationship")]
    assert document_rels == [("rId1", "styles", "styles.xml"), ("rId2", "numbering", "numbering.xml"),
                             ("rId3", "settings", "settings.xml"), ("rId4", "footer", "footer1.xml")]


def test_every_part_is_utf8_xml_with_a_declaration() -> None:
    for name, content in docx().items():
        assert content.startswith(b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'), name
        xml(content)  # well-formed


def test_the_zip_is_deterministic_and_has_no_timestamps_or_platform_bits() -> None:
    first = render(SOURCE, format="docx").binary
    second = render(SOURCE, format="docx").binary
    assert first == second
    assert first is not None
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.testzip() is None
        for info in archive.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.compress_type == zipfile.ZIP_DEFLATED
            assert (info.create_system, info.external_attr >> 16, info.extra, info.comment) == (3, 0o100644, b"", b"")


def test_a_different_document_is_a_different_file() -> None:
    assert render(SOURCE, format="docx").binary != render(SOURCE.replace("First", "Second"), format="docx").binary


# -- settings.xml ------------------------------------------------------------------------


def test_settings_have_compatibility_mode_15_and_no_revision_ids() -> None:
    settings = xml(docx()["word/settings.xml"])
    modes = [(c.get(f"{W}name"), c.get(f"{W}val")) for c in settings.iter(f"{W}compatSetting")]
    assert modes == [("compatibilityMode", "15")]
    assert b"rsid" not in docx()["word/settings.xml"]


def test_update_fields_is_in_schema_order_after_the_character_spacing_and_before_compat() -> None:
    settings = xml(docx()["word/settings.xml"])
    assert [child.tag.removeprefix(W) for child in settings] == [
        "defaultTabStop", "characterSpacingControl", "updateFields", "compat"]
    assert settings.find(f"{W}updateFields").get(f"{W}val") == "true"


def test_update_fields_on_open_can_be_turned_off() -> None:
    settings = xml(docx(overrides={"docx.update_fields_on_open": False})["word/settings.xml"])
    assert settings.find(f"{W}updateFields") is None
    assert settings.find(f"{W}compat") is not None


def test_the_numbering_part_is_an_empty_stub_that_later_work_fills() -> None:
    assert list(xml(docx()["word/numbering.xml"])) == []
    from legaldown_render.writers.docx.settings import build_numbering

    filled = xml(build_numbering([ooxml.el("abstractNum", {"abstractNumId": 0})], [ooxml.el("num", {"numId": 1})]))
    assert [child.tag.removeprefix(W) for child in filled] == ["abstractNum", "num"]


# -- styles.xml ----------------------------------------------------------------------------


def style_map(contents: dict[str, bytes]) -> dict[str, ET.Element]:
    return {s.get(f"{W}styleId"): s for s in xml(contents["word/styles.xml"]).iter(f"{W}style")}


def test_the_styles_the_body_and_word_need_exist_under_the_names_word_knows() -> None:
    by_id = style_map(docx())
    names = {style_id: s.find(f"{W}name").get(f"{W}val") for style_id, s in by_id.items()}
    assert names == {
        "Normal": "Normal", "DefaultParagraphFont": "Default Paragraph Font", "TableNormal": "Normal Table",
        "NoList": "No List", "Heading1": "heading 1", "Heading2": "heading 2", "Heading3": "heading 3",
        "Heading4": "heading 4", "Heading5": "heading 5", "Title": "Title", "Subtitle": "Subtitle",
        "Quote": "Quote", "ContentsHeading": "Contents Heading", "DraftingNote": "Drafting Note", "Code": "Code",
        "TableGrid": "Table Grid", "Header": "header", "Footer": "footer", "TOC1": "toc 1", "TOC2": "toc 2",
        "TOC3": "toc 3", "TOC4": "toc 4", "TOC5": "toc 5", "Hyperlink": "Hyperlink", "InlineCode": "Inline Code",
        "DefinedTerm": "Defined Term", "TermReference": "Term Reference", "Blank": "Blank", "Choice": "Choice",
        "FailureMarker": "Failure Marker", "ConditionLabel": "Condition Label",
        "DraftingNoteLabel": "Drafting Note Label"}
    assert by_id["Normal"].get(f"{W}default") == "1"
    assert by_id["TableGrid"].get(f"{W}type") == "table"
    assert by_id["Hyperlink"].get(f"{W}type") == "character"
    assert by_id["ContentsHeading"].get(f"{W}customStyle") == "1"
    assert by_id["Heading1"].get(f"{W}customStyle") is None


def test_headings_are_outline_levels_zero_to_four_and_not_numbered_yet() -> None:
    by_id = style_map(docx())
    for level in range(1, 6):
        heading = by_id[f"Heading{level}"]
        assert heading.find(f"{W}pPr/{W}outlineLvl").get(f"{W}val") == str(level - 1)
        assert heading.find(f"{W}basedOn").get(f"{W}val") == "Normal"
        assert heading.find(f"{W}pPr/{W}numPr") is None
    assert by_id["ContentsHeading"].find(f"{W}pPr/{W}outlineLvl") is None  # in no table of contents


def test_headings_link_to_a_numbering_definition_when_given_one() -> None:
    from legaldown_render.style import load_style
    from legaldown_render.style.docx import docx_settings

    style = load_style("default")
    root = xml(styles.build_styles(style, docx_settings(style), "en", heading_num_id=7))
    for style_element in root.iter(f"{W}style"):
        style_id = style_element.get(f"{W}styleId")
        num = style_element.find(f"{W}pPr/{W}numPr")
        if style_id.startswith("Heading"):
            level = int(style_id.removeprefix("Heading")) - 1
            assert (num.find(f"{W}ilvl").get(f"{W}val"), num.find(f"{W}numId").get(f"{W}val")) == (str(level), "7")
        else:
            assert num is None


def test_headings_follow_the_style_template() -> None:
    by_id = style_map(docx(style="continental"))
    heading = by_id["Heading1"]
    assert heading.find(f"{W}pPr/{W}jc").get(f"{W}val") == "center"
    assert heading.find(f"{W}rPr/{W}caps") is None  # continental: transform none
    assert heading.find(f"{W}rPr/{W}sz").get(f"{W}val") == "24"  # 1.1em of 11pt, rounded to half-points
    default = style_map(docx())["Heading1"]
    assert default.find(f"{W}rPr/{W}caps") is not None and default.find(f"{W}rPr/{W}b") is not None


def test_normal_carries_size_colour_justification_and_line_height() -> None:
    normal = style_map(docx(overrides={"typography.justify": True, "typography.font_size": "12pt",
                                       "typography.line_height": "1.25", "typography.color": "#112233"}))["Normal"]
    assert normal.find(f"{W}rPr/{W}sz").get(f"{W}val") == "24"
    assert normal.find(f"{W}rPr/{W}color").get(f"{W}val") == "112233"
    assert normal.find(f"{W}pPr/{W}jc").get(f"{W}val") == "both"
    spacing = normal.find(f"{W}pPr/{W}spacing")
    assert (spacing.get(f"{W}line"), spacing.get(f"{W}lineRule")) == ("300", "auto")


def test_doc_defaults_have_the_language_and_the_first_font_family() -> None:
    root = xml(docx()["word/styles.xml"])
    run = root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr")
    fonts = run.find(f"{W}rFonts")
    # Only the Latin font: complex-script and East Asian fonts are not the Latin font's to give.
    assert {key.removeprefix(W): value for key, value in fonts.attrib.items()} == {"ascii": "Georgia", "hAnsi": "Georgia"}
    lang = run.find(f"{W}lang")
    assert {key.removeprefix(W): value for key, value in lang.attrib.items()} == {"val": "cs"}  # the document's language
    english = xml(docx(FRONT + "# A\n")["word/styles.xml"]).find(f"{W}docDefaults/{W}rPrDefault/{W}rPr/{W}lang")
    assert english.get(f"{W}val") == "en"


def test_a_language_that_is_not_a_tag_falls_back_to_the_locale_then_en_us() -> None:
    assert styles.language_tag("cs-CZ", "en") == "cs-CZ"
    assert styles.language_tag("not a tag", "de-DE") == "de-DE"
    assert styles.language_tag("<x>", None) == "en-US"
    assert styles.language_tag(None, "") == "en-US"


def test_term_styles_follow_the_definitions_settings() -> None:
    by_id = style_map(docx(overrides={"definitions.style": "italic", "definitions.term_style": "underline"}))
    assert by_id["DefinedTerm"].find(f"{W}rPr/{W}i") is not None
    assert by_id["TermReference"].find(f"{W}rPr/{W}u").get(f"{W}val") == "single"
    plain = style_map(docx(overrides={"definitions.style": "plain"}))["DefinedTerm"]
    assert plain.find(f"{W}rPr") is None


def test_the_hyperlink_and_toc_styles_use_the_link_colour_and_the_text_width() -> None:
    by_id = style_map(docx(overrides={"typography.link_color": "#ff0000", "page.margin": "20mm"}))
    assert by_id["Hyperlink"].find(f"{W}rPr/{W}color").get(f"{W}val") == "FF0000"
    tab = by_id["TOC2"].find(f"{W}pPr/{W}tabs/{W}tab")
    assert (tab.get(f"{W}val"), tab.get(f"{W}leader"), tab.get(f"{W}pos")) == ("right", "dot", str(11906 - 2 * 1134))
    assert by_id["TOC3"].find(f"{W}pPr/{W}ind").get(f"{W}left") == "480"


# -- Page setup, header and footer ---------------------------------------------------------


def section(contents: dict[str, bytes]) -> ET.Element:
    return xml(contents["word/document.xml"]).find(f"{W}body/{W}sectPr")


def test_the_page_setup_is_in_twips() -> None:
    sect = section(docx(overrides={"page.size": "letter", "page.margin": "1in 2in"}))
    assert {k.removeprefix(W): v for k, v in sect.find(f"{W}pgSz").attrib.items()} == {"w": "12240", "h": "15840"}
    margins = {k.removeprefix(W): v for k, v in sect.find(f"{W}pgMar").attrib.items()}
    assert margins == {"top": "1440", "right": "2880", "bottom": "1440", "left": "2880", "header": "720",
                       "footer": "720", "gutter": "0"}


def test_landscape_has_the_long_edge_as_width_and_says_so() -> None:
    size = section(docx(overrides={"page.size": "A4 landscape"})).find(f"{W}pgSz")
    assert {k.removeprefix(W): v for k, v in size.attrib.items()} == {"w": "16838", "h": "11906", "orient": "landscape"}


def test_the_section_references_the_footer_and_there_is_no_header_by_default() -> None:
    contents = docx()
    sect = section(contents)
    assert sect.find(f"{W}headerReference") is None
    reference = sect.find(f"{W}footerReference")
    assert reference.get(f"{W}type") == "default"
    assert reference.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") == "rId4"
    assert [child.tag.removeprefix(W) for child in sect] == ["footerReference", "pgSz", "pgMar"]


def test_a_document_without_header_and_footer_has_no_such_parts() -> None:
    contents = docx(overrides={"page.footer": {"center": ""}})
    assert not [name for name in contents if "header" in name or "footer" in name]
    assert section(contents).find(f"{W}footerReference") is None


def test_the_default_footer_is_a_page_and_pages_field_centred() -> None:
    footer = xml(docx()["word/footer1.xml"])
    (paragraph,) = footer.iter(f"{W}p")
    assert paragraph.find(f"{W}pPr/{W}pStyle").get(f"{W}val") == "Footer"
    assert paragraph.find(f"{W}pPr/{W}jc").get(f"{W}val") == "center"
    instructions = [t.text for t in paragraph.iter(f"{W}instrText")]
    assert instructions == [" PAGE ", " NUMPAGES "]
    kinds = [c.get(f"{W}fldCharType") for c in paragraph.iter(f"{W}fldChar")]
    assert kinds == ["begin", "separate", "end"] * 2
    assert texts(footer) == ["1 / 1"]  # the cached results and the literal between them


def header_footer(**templates: str) -> dict[str, bytes]:
    return docx(overrides={f"page.{key.replace('_', '.')}": value for key, value in templates.items()})


def test_three_templates_are_set_with_tab_stops_at_the_middle_and_right_margin() -> None:
    contents = header_footer(header_left="{title}", header_center="{date}", header_right="v{version}, {page}")
    header = xml(contents["word/header1.xml"])
    (paragraph,) = header.iter(f"{W}p")
    assert texts(header) == ["Services Agreement\t4. března 2026\tv2.1, 1"]
    width = 11906 - 2 * 1417
    tabs = [(t.get(f"{W}val"), t.get(f"{W}pos")) for t in paragraph.iter(f"{W}tab") if t.get(f"{W}pos")]
    assert tabs == [("center", str(width // 2)), ("right", str(width))]
    assert paragraph.find(f"{W}pPr/{W}pStyle").get(f"{W}val") == "Header"
    assert section(contents).find(f"{W}headerReference").get(
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") == "rId4"


@pytest.mark.parametrize(("templates", "line", "alignment", "stops"), [
    ({"header_left": "L"}, "L", None, []),
    ({"header_center": "C"}, "C", "center", []),
    ({"header_right": "R"}, "R", "right", []),
    ({"header_left": "L", "header_center": "C"}, "L\tC", None, ["center"]),
    ({"header_left": "L", "header_right": "R"}, "L\tR", None, ["right"]),
    ({"header_center": "C", "header_right": "R"}, "\tC\tR", None, ["center", "right"]),
    ({"header_left": "L", "header_center": "C", "header_right": "R"}, "L\tC\tR", None, ["center", "right"]),
])
def test_how_the_templates_are_laid_out(templates: dict[str, str], line: str, alignment: str | None,
                                        stops: list[str]) -> None:
    header = xml(header_footer(**templates)["word/header1.xml"])
    assert texts(header) == [line]
    jc = header.find(f".//{W}pPr/{W}jc")
    assert (jc.get(f"{W}val") if jc is not None else None) == alignment
    assert [t.get(f"{W}val") for t in header.iter(f"{W}tab") if t.get(f"{W}pos")] == stops


def test_title_version_and_date_are_text_and_a_missing_one_is_empty() -> None:
    footer = xml(header_footer(footer_center="{title}|{version}|{date}")["word/footer1.xml"])
    assert texts(footer) == ["Services Agreement|2.1|4. března 2026"]
    bare = xml(docx(FRONT + "# A\n", overrides={"page.footer.center": "[{version}][{date}]"})["word/footer1.xml"])
    assert texts(bare) == ["[][]"]


def test_the_date_follows_the_style_and_the_locale() -> None:
    footer = xml(docx(overrides={"page.footer.center": "{date}", "values.date": "short", "locale": "cs-CZ"})
                 ["word/footer1.xml"])
    assert texts(footer) == ["04.03.26"]


def test_a_version_or_date_that_holds_directives_reads_as_the_title_block_shows_it() -> None:
    source = "---\ntitle: T\nversion: 'v {{date: 2026-03-04}} {{placeholder: v}}'\neffective_date: '{{date: 2026-01-02}}'\n---\n# A\n"
    overrides = {"page.footer.center": "[{version}|{date}]", "title_block.version": True}
    result = render(source, format="docx", overrides=overrides)
    footer = xml(parts(result.binary)["word/footer1.xml"])
    assert texts(footer) == ["[v March 4, 2026 [_____]|January 2, 2026]"]
    shown = {label: plain_text(value) for label, value in result.tree.header}
    assert shown["Version"] == "v March 4, 2026 [_____]"  # the same text the title block has


def test_the_tree_carries_version_and_date_whether_or_not_the_title_block_shows_them() -> None:
    tree = render(SOURCE, format="text", overrides={"title_block.version": False,
                                                    "title_block.effective_date": False}).tree
    assert (tree.version, tree.effective_date, tree.header) == ("2.1", "4. března 2026", ())
    bare = render(FRONT + "# A\n", format="text").tree
    assert (bare.version, bare.effective_date) == ("", "")


def test_the_title_is_the_same_text_in_the_body_the_properties_and_the_footer() -> None:
    source = "---\ntitle: '  A {{date: 2026-03-04}} B  '\n---\n# A\n"
    contents = docx(source, overrides={"page.footer.center": "{title}"})
    expected = "A March 4, 2026 B"
    assert texts(xml(contents["word/document.xml"]))[0] == expected
    assert texts(xml(contents["word/footer1.xml"])) == [expected]
    assert xml(contents["docProps/core.xml"])[0].text == expected


def test_a_field_name_the_writer_does_not_know_stays_as_written() -> None:
    runs = template_runs("{page} of {pages}: {nope}", {"title": "x"})
    root = ET.Element("p")
    root.extend(runs)
    assert "".join(t.text for t in root.iter(f"{W}t")) == "1 of 1: {nope}"


# -- Properties --------------------------------------------------------------------------


def test_core_properties_have_the_title_and_language_and_no_timestamps() -> None:
    core = xml(docx()["docProps/core.xml"])
    assert [(child.tag.rsplit("}", 1)[1], child.text) for child in core] == [
        ("title", "Services Agreement"), ("language", "cs")]
    raw = docx()["docProps/core.xml"].decode("utf-8")
    assert "created" not in raw and "modified" not in raw and "creator" not in raw


def test_core_properties_leave_out_an_empty_title() -> None:
    core = xml(docx("---\ntitle: ''\n---\n# A\n")["docProps/core.xml"])
    assert [child.tag.rsplit("}", 1)[1] for child in core] == ["language"]


def test_app_properties_name_the_program() -> None:
    app = xml(docx()["docProps/app.xml"])
    assert app.tag == "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}Properties"
    assert app[0].text.startswith("legaldown-render ")


# -- The body without a plan ---------------------------------------------------------------
# (the body writer itself is in test_docx_body.py)


def test_the_body_without_a_plan_has_the_title_headings_and_text_and_no_numbers() -> None:
    contents = docx()
    body = xml(contents["word/document.xml"]).find(f"{W}body")
    styles_used = [(p.find(f"{W}pPr/{W}pStyle").get(f"{W}val") if p.find(f"{W}pPr/{W}pStyle") is not None else None,
                    texts(p)[0]) for p in body.iter(f"{W}p")]
    assert styles_used[:5] == [("Title", "Services Agreement"), ("Subtitle", "A subtitle"), (None, "Preamble text."),
                               ("Heading1", "First"), (None, "Body one.")]
    assert ("Heading2", "Nested") in styles_used
    assert (None, "item a") in styles_used and ("Quote", "quoted") in styles_used
    assert [text for style, text in styles_used if style == "Code"] == ["code 1", "code 2"]
    # The cells of the table, then the paragraph of the rule that ends the document.
    assert [text for _, text in styles_used][-5:] == ["A", "B", "1", "2", ""]
    assert all(not text.startswith(("1.", "(a)")) for _, text in styles_used)


def test_a_heading_level_is_clamped_to_the_five_word_styles() -> None:
    source = FRONT + "# 1\n\n## 2\n\n### 3\n\n#### 4\n\n##### 5\n"
    used = [p.find(f"{W}pPr/{W}pStyle").get(f"{W}val")
            for p in xml(docx(source)["word/document.xml"]).iter(f"{W}p") if p.find(f"{W}pPr/{W}pStyle") is not None]
    assert used == ["Title", "Heading1", "Heading2", "Heading3", "Heading4", "Heading5"]


def test_a_code_block_is_one_paragraph_per_line_and_a_hard_break_is_a_break() -> None:
    document = xml(docx(FRONT + "# A\n\nline one\\\nline two\n")["word/document.xml"])
    assert "line one\nline two" in texts(document)


# -- Sanitising text -----------------------------------------------------------------------


def test_the_sanitiser_drops_what_xml_cannot_carry() -> None:
    assert ooxml._t("a\x00b\x01c\x08d\x0be\x0cf\x1fg\x7fh\x85i\x9fj\ufffek\uffffl\ud800m\udfffn") == "abcdefghijklmn"
    assert ooxml._t("keep ž € \U0001f600   ﻿") == "keep ž € \U0001f600   ﻿"
    assert ooxml._t("a\tb\nc\rd") == "a b c d"
    assert ooxml._t("a\tb\nc\rd", breaks=True) == "a\tb\nc\rd"


def test_text_runs_turn_tab_and_line_breaks_into_elements() -> None:
    run = ooxml.text_run("a\tb\nc\r\nd\re  f")
    assert [(child.tag.removeprefix(W), child.text) for child in run] == [
        ("t", "a"), ("tab", None), ("t", "b"), ("br", None), ("t", "c"), ("br", None), ("t", "d"), ("br", None),
        ("t", "e  f")]
    assert all(t.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve" for t in run.iter(f"{W}t"))
    assert list(ooxml.text_run("")) == []
    assert [child.tag.removeprefix(W) for child in ooxml.text_run("\x00\x01")] == []


def test_every_attribute_and_text_goes_through_the_sanitiser() -> None:
    element = ooxml.el("bookmarkStart", {"name": "a\x01b", "id": 3, "flag": True, "off": False, "none": None},
                       text="x\x02y")
    assert element.attrib == {f"{W}name": "ab", f"{W}id": "3", f"{W}flag": "1", f"{W}off": "0"}
    assert element.text == "xy"
    assert ET.tostring(ooxml.el("t", text="a\ud800b"), encoding="utf-8")  # encodable: no lone surrogate survives


def test_serialisation_is_valid_utf8_for_any_text() -> None:
    hostile = "".join(chr(code) for code in (*range(0x20), 0x7F, 0x85, 0xD800, 0xDFFF, 0xFFFE, 0xFFFF)) + "<&>\"'"
    data = ooxml.serialize(ooxml.el("p", {"x": hostile}, ooxml.text_run(hostile), ooxml.el("instrText", text=hostile)))
    ET.fromstring(data.decode("utf-8"))


def test_properties_are_sorted_into_schema_order() -> None:
    properties = ooxml.ppr(ooxml.val("jc", "left"), ooxml.val("outlineLvl", 0), ooxml.val("pStyle", "X"),
                           ooxml.el("spacing"), ooxml.el("keepNext"), ooxml.el("numPr"), ooxml.el("ind"))
    assert [child.tag.removeprefix(W) for child in properties] == [
        "pStyle", "keepNext", "numPr", "spacing", "ind", "jc", "outlineLvl"]
    run = ooxml.rpr(ooxml.val("sz", 2), ooxml.el("b"), ooxml.val("rStyle", "S"), ooxml.val("color", "000000"),
                    ooxml.el("i"))
    assert [child.tag.removeprefix(W) for child in run] == ["rStyle", "b", "i", "color", "sz"]
    assert ooxml.ppr(None, [None, False]) is None
    with pytest.raises(InternalError):
        ooxml.ppr(ooxml.el("notAChild"))


def test_a_paragraph_puts_its_properties_before_its_content() -> None:
    paragraph = ooxml.p(ooxml.text_run("x"), style="Quote", props=[ooxml.el("keepNext")])
    assert [child.tag.removeprefix(W) for child in paragraph] == ["pPr", "r"]
    assert [child.tag.removeprefix(W) for child in paragraph[0]] == ["pStyle", "keepNext"]


# -- Fields, bookmarks, hyperlinks ---------------------------------------------------------


def test_a_complex_field_is_begin_instruction_separate_result_end() -> None:
    runs = ooxml.complex_field("REF  ld_x \\n \\h", [ooxml.text_run("2.1")], dirty=True)
    root = ET.Element("p")
    root.extend(runs)
    chars = [(c.get(f"{W}fldCharType"), c.get(f"{W}dirty")) for c in root.iter(f"{W}fldChar")]
    assert chars == [("begin", "1"), ("separate", None), ("end", None)]
    assert [t.text for t in root.iter(f"{W}instrText")] == [" REF  ld_x \\n \\h "]
    assert [child[0].tag.removeprefix(W) if len(child) else None for child in runs] == [
        "fldChar", "instrText", "fldChar", "t", "fldChar"]
    empty = ooxml.complex_field("PAGEREF x")
    assert len(empty) == 4  # no cached result


def test_a_field_instruction_is_sanitised_and_kept_on_one_line() -> None:
    runs = ooxml.complex_field("REF a\x01b\nc")
    assert runs[1].find(f"{W}instrText").text == " REF ab c "


def test_bookmark_ids_are_unique_across_the_package_and_names_are_checked() -> None:
    bookmarks = ooxml.Bookmarks()
    first_start, first_end = bookmarks.pair("ld_first")
    second = bookmarks.around("_Ld2", ooxml.text_run("x"))
    assert first_start.get(f"{W}id") == first_end.get(f"{W}id") == "1"
    assert [c.tag.removeprefix(W) for c in second] == ["bookmarkStart", "r", "bookmarkEnd"]
    assert second[0].get(f"{W}id") == second[2].get(f"{W}id") == "2"
    for bad in ("", "1abc", "a-b", "a b", "x" * 41, "ž"):
        with pytest.raises(InternalError):
            bookmarks.pair(bad)
    with pytest.raises(InternalError):
        bookmarks.pair("LD_FIRST")  # Word compares names without regard to case
    assert bookmarks.pair("x" * 40)


def test_hyperlinks_are_internal_by_anchor_or_external_by_relationship() -> None:
    relationships = package.Relationships()
    first = relationships.add_external("https://example.com/a b?q=ž")
    again = relationships.add_external("https://example.com/a b?q=ž")
    other = relationships.add_external("mailto:x@example.com")
    assert (first, again, other) == ("rId1", "rId1", "rId2")
    external = ooxml.hyperlink_external(first, ooxml.text_run("here"))
    assert external.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") == "rId1"
    internal = ooxml.hyperlink_internal("ld_x", ooxml.text_run("there"), tooltip="t")
    assert (internal.get(f"{W}anchor"), internal.get(f"{W}tooltip")) == ("ld_x", "t")
    listed = ET.fromstring(relationships.to_xml())
    assert [(r.get("Id"), r.get("Target"), r.get("TargetMode")) for r in listed] == [
        ("rId1", "https://example.com/a%20b?q=%C5%BE", "External"), ("rId2", "mailto:x@example.com", "External")]


@pytest.mark.parametrize(("url", "expected"), [
    ("https://example.com/a?b=c&d=e#f", "https://example.com/a?b=c&d=e#f"),
    ("https://example.com/a b", "https://example.com/a%20b"),
    ("https://example.com/%20already", "https://example.com/%20already"),
    ("https://example.com/ž", "https://example.com/%C5%BE"),
    ('https://example.com/"<>^`{|}', "https://example.com/%22%3C%3E%5E%60%7B%7C%7D"),
    ("https://e/a\\b", "https://e/a%5Cb"),
    ("  https://example.com  ", "https://example.com"),
    ("", None), ("   ", None), ("https://e/\x01", None), ("https://e/\x7f", None), ("https://e/a\nb", None),
    ("https://e/\ud800", None),
])
def test_a_relationship_target_is_a_valid_uri_or_none(url: str, expected: str | None) -> None:
    assert package.external_target(url) == expected
    if "\\" not in url:
        assert package.Relationships().add_external(url) == (None if expected is None else "rId1")


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", "java\tscript:alert(1)", "file:///etc/passwd", "ms-msdt:/id PCWDiagnostic",
    "ms-word:ofe|u|https://x/a.docx", "data:text/html,x", "vbscript:x", "//evil.example/x", "///x", "\\\\host\\share",
    "\\host", "https://e/a\\b", "relative/path", "../up", "#fragment", "?q=1", "C:\\x", "localhost:8080", "",
])
def test_a_hyperlink_relationship_is_refused_for_what_is_not_a_link(url: str) -> None:
    """The HTML writer's rule (is_link_target), and more: no relative target and no //host,
    since Word follows a relationship without a base to read it against."""
    assert package.Relationships().add_external(url) is None


@pytest.mark.parametrize("url", ["https://example.com", "http://e/x", "mailto:a@example.com", "tel:+420123456789",
                                 "ftp://e/f", "urn:isbn:0451450523", "sms:+1555"])
def test_a_hyperlink_relationship_is_made_for_a_link_scheme(url: str) -> None:
    assert package.Relationships().add_external(url) == "rId1"


def test_relationships_are_found_in_constant_time_and_keep_their_order() -> None:
    relationships = package.Relationships()
    ids = [relationships.add_external(f"https://example.com/{n}") for n in range(2000)]
    assert ids == [f"rId{n + 1}" for n in range(2000)]
    assert relationships.add_external("https://example.com/1999") == "rId2000"
    assert relationships.add(package.REL_STYLES, "styles.xml") == "rId2001"


def test_a_relationship_value_is_escaped_in_the_part() -> None:
    relationships = package.Relationships()
    relationships.add("http://x/\"&<", "a&b\"c", external=True)
    (entry,) = ET.fromstring(relationships.to_xml())
    assert (entry.get("Type"), entry.get("Target")) == ("http://x/\"&<", "a&b\"c")


def test_a_part_added_twice_is_a_bug() -> None:
    bundle = package.Package()
    bundle.add("word/x.xml", package.CT_XML, b"<x/>")
    with pytest.raises(InternalError):
        bundle.add("word/x.xml", package.CT_XML, b"<x/>")


# -- The API and the command line ------------------------------------------------------------


def test_docx_is_a_format_selected_by_its_extension() -> None:
    assert FORMATS["docx"] == ("docx",)
    assert format_for_path("contract.docx") == "docx"
    assert format_for_path("CONTRACT.DOCX") == "docx"


def test_the_result_carries_the_bytes_and_an_empty_output() -> None:
    result = render(SOURCE, RenderOptions(format="docx"))
    assert result.format == "docx"
    assert result.output == ""
    assert isinstance(result.binary, bytes) and result.binary.startswith(b"PK")
    assert result.ok
    assert render(SOURCE, format="html").binary is None and render(SOURCE, format="text").binary is None


def test_render_file_renders_docx(tmp_path: Path) -> None:
    source = tmp_path / "contract.lgd"
    source.write_text(SOURCE, encoding="utf-8")
    assert render_file(source, format="docx").binary == render(SOURCE, format="docx").binary


def test_diagnostics_are_the_other_formats_plus_the_interim_warning() -> None:
    document = (DOCUMENTS / "features.lgd").read_text(encoding="utf-8")
    docx_result = render(document, format="docx").diagnostics
    assert docx_result[:-1] == render(document, format="text").diagnostics
    warning = docx_result[-1]
    assert (warning.rule, warning.level, warning.file) == ("render-docx-interim", "warning", "")
    assert warning.message.startswith("DOCX output is incomplete in this version:")


def test_the_interim_warning_is_one_warning_that_does_not_refuse_under_strict() -> None:
    result = render(FRONT + "# A\n", format="docx", strict=True, filename="d.lgd")
    assert [(d.rule, d.file) for d in result.diagnostics if d.rule.startswith("render-")] == [
        ("render-docx-interim", "d.lgd")]
    assert result.ok
    assert not [d for d in render(FRONT + "# A\n", format="html").diagnostics if d.rule == "render-docx-interim"]


def test_the_docx_writer_can_be_used_directly() -> None:
    from legaldown_render.style import load_style

    result = render(SOURCE, format="text")
    data = DocxWriter(load_style("default"), generator="test 1").write(result.tree)
    assert parts(data)["docProps/app.xml"].count(b"test 1") == 1


def test_the_cli_writes_a_docx_file(tmp_path: Path) -> None:
    target = tmp_path / "out.docx"
    assert main([FEATURES, "-q", "-o", str(target)]) == 0
    assert target.read_bytes() == render((DOCUMENTS / "features.lgd").read_text(encoding="utf-8"),
                                         format="docx").binary
    other = tmp_path / "other.bin"
    assert main([FEATURES, "-q", "-f", "docx", "-o", str(other)]) == 0
    assert other.read_bytes() == target.read_bytes()


def test_an_uppercase_extension_selects_docx(tmp_path: Path) -> None:
    target = tmp_path / "OUT.DOCX"
    assert main([FEATURES, "-q", "-o", str(target)]) == 0
    assert target.read_bytes().startswith(b"PK")


def test_docx_to_standard_output_with_dash_writes_bytes() -> None:
    expected = render((DOCUMENTS / "features.lgd").read_text(encoding="utf-8"), format="docx").binary
    done = run_cli(FEATURES, "-q", "-f", "docx", "-o", "-")
    assert done.returncode == 0 and done.stderr == b""
    assert done.stdout.startswith(b"PK") and done.stdout == expected


def test_docx_without_output_goes_to_standard_output_when_that_is_not_a_terminal() -> None:
    done = run_cli(FEATURES, "-q", "-f", "docx")  # the test's standard output is a pipe
    assert done.returncode == 0 and done.stderr == b""
    assert done.stdout.startswith(b"PK")
    assert done.stdout == run_cli(FEATURES, "-q", "-f", "docx", "-o", "-").stdout


def test_fragment_is_a_usage_error_for_docx(tmp_path: Path) -> None:
    done = run_cli(FEATURES, "-q", "-f", "docx", "--fragment", "-o", str(tmp_path / "x.docx"))
    assert done.returncode == 2 and b"--fragment applies to HTML only" in done.stderr
    assert list(tmp_path.iterdir()) == []


def test_docx_without_output_is_refused_on_a_terminal(monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    with pytest.raises(SystemExit) as exit_info:
        main([FEATURES, "-q", "-f", "docx"])
    assert exit_info.value.code == 2
    assert "refusing to write a DOCX file to a terminal" in capsys.readouterr().err


def test_a_standard_output_without_isatty_is_not_a_terminal(monkeypatch: pytest.MonkeyPatch,
                                                            capsysbinary: pytest.CaptureFixture[bytes]) -> None:
    class Plain:  # no isatty, no reconfigure: some embedders replace the stream so
        buffer = io.BytesIO()

        def write(self, text: str) -> int:
            return len(text)

        def flush(self) -> None:
            pass

    monkeypatch.setattr(sys, "stdout", Plain())
    assert main([FEATURES, "-q", "-f", "docx"]) == 0
    assert Plain.buffer.getvalue().startswith(b"PK")


def test_docx_is_not_written_to_a_terminal(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    with pytest.raises(SystemExit) as exit_info:
        main([FEATURES, "-q", "-f", "docx", "-o", "-"])
    assert exit_info.value.code == 2
    assert "refusing to write a DOCX file to a terminal" in capsys.readouterr().err


def test_a_style_word_cannot_express_exits_with_status_2_and_writes_no_file(tmp_path: Path) -> None:
    target = tmp_path / "out.docx"
    done = run_cli(FEATURES, "-q", "-o", str(target), "--set", "page.margin=5%")
    assert done.returncode == 2
    assert b"page.margin: uses '%'" in done.stderr
    assert not target.exists()
    assert run_cli(FEATURES, "-q", "-o", str(tmp_path / "out.html"), "--set", "page.margin=5%").returncode == 0


def test_the_cli_cannot_write_a_docx_into_a_missing_folder(tmp_path: Path) -> None:
    done = run_cli(FEATURES, "-q", "-o", str(tmp_path / "no" / "out.docx"))
    assert done.returncode == 1 and b"cannot write" in done.stderr


def test_text_and_html_to_standard_output_with_dash_still_work_and_name_no_file(tmp_path: Path) -> None:
    done = run_cli(FEATURES, "-q", "-f", "text", "-o", "-", cwd=tmp_path)
    assert done.returncode == 0 and done.stdout.startswith(b"Services Agreement")
    assert list(tmp_path.iterdir()) == []


def test_an_untrusted_render_makes_a_docx_too(tmp_path: Path) -> None:
    result = render(SOURCE, format="docx", untrusted=True, document_root=tmp_path)
    assert result.binary is not None and result.ok


# -- Output that does not depend on anything outside the writer -------------------------------------


def test_the_output_does_not_depend_on_the_global_namespace_registry() -> None:
    expected = render(SOURCE, format="docx").binary
    saved = dict(ET._namespace_map)  # type: ignore[attr-defined]
    try:
        for uri in (W_NS, "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
                    "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
                    "http://purl.org/dc/elements/1.1/", "http://www.w3.org/XML/1998/namespace"):
            ET.register_namespace("host", uri)  # another library's registration
        assert render(SOURCE, format="docx").binary == expected
    finally:
        ET._namespace_map.clear()  # type: ignore[attr-defined]
        ET._namespace_map.update(saved)  # type: ignore[attr-defined]


def test_importing_the_writer_registers_no_namespace() -> None:
    code = ("import xml.etree.ElementTree as ET\nbefore = dict(ET._namespace_map)\n"
            "import legaldown_render.writers.docx\nassert ET._namespace_map == before\n")
    assert run_python(code).returncode == 0


def run_python(code: str):  # noqa: ANN201 - a test helper
    import os
    import subprocess

    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    return subprocess.run([sys.executable, "-c", code], capture_output=True, env=env, check=False)


def test_the_serialiser_sanitises_whoever_built_the_element() -> None:
    root = ET.Element(f"{W}t", {f"{W}val": "a\x01b\n\"<&>", "plain": "x"})
    root.text = "a\x00b\ud800c&<>\t"
    data = ooxml.serialize(root).decode("utf-8")
    assert data.endswith('<w:t xmlns:w="' + W_NS + '" w:val="ab &quot;&lt;&amp;&gt;" plain="x">abc&amp;&lt;&gt;\t</w:t>')
    with pytest.raises(InternalError):
        ooxml.serialize(ET.Element("{urn:other}x"))


def test_a_carriage_return_in_text_is_written_as_a_character_reference() -> None:
    root = ET.Element(f"{W}t")
    root.text = "a\rb"
    data = ooxml.serialize(root).decode("utf-8")
    assert "&#13;" in data and "\r" not in data
    assert ET.fromstring(data).text == "a\rb"


def test_an_element_with_a_tail_is_refused_rather_than_dropped() -> None:
    root = ET.Element(f"{W}p")
    ET.SubElement(root, f"{W}r").tail = "text"
    with pytest.raises(InternalError, match="mixed content is not written"):
        ooxml.serialize(root)


def test_a_text_passed_as_a_child_is_a_bug_not_a_recursion_error() -> None:
    for bad in ("text", b"bytes", ["ok", ooxml.el("x")]):
        with pytest.raises(InternalError):
            ooxml.el("p", None, bad)
    assert [c.tag for c in ooxml.el("p", None, [None, False, [ooxml.el("x")]])] == [f"{W}x"]
    assert ooxml.p(style="").find(f"{W}pPr") is None


def test_names_and_languages_must_match_in_full() -> None:
    bookmarks = ooxml.Bookmarks()
    with pytest.raises(InternalError):
        bookmarks.pair("ld_x\n")
    assert styles.language_tag("cs\n", "en") == "en"
    assert styles.language_tag("cs-CZ\n", None) == "en-US"


def test_code_is_left_aligned_even_when_the_body_is_justified() -> None:
    by_id = style_map(docx(overrides={"typography.justify": True}))
    assert by_id["Normal"].find(f"{W}pPr/{W}jc").get(f"{W}val") == "both"
    assert by_id["Code"].find(f"{W}pPr/{W}jc").get(f"{W}val") == "left"


#: SHA-256 of each part of the fixed document below, uncompressed. The ZIP's own bytes depend on the
#: zlib that compressed them (a different zlib may write different, equivalent, bytes); these do not.
#: When a part changes on purpose, run this test, review the change, and replace the digest.
GOLDEN = {
    '[Content_Types].xml': '0edc3b54ddf02325a53d17a23256a46b50f782562ff0319d5c48b73ceb50d29b',
    '_rels/.rels': 'd6f21829c0081670bdb417d3e3f644b81134a121e074fb153c7686faaa8b167b',
    'docProps/core.xml': '159ed0e537106ab29739fd357a5f6d89a2c0b0154daefd4c13d878bd16dff47e',
    'docProps/app.xml': 'b285204d159fd58da80b6a0242cef523448f9c3a598b357b15faf4627160dcc3',
    'word/document.xml': '973a83b30f439e08a3238504ff0d6c8df6ea9259244e7ee82e1f81a3d274bb19',
    'word/_rels/document.xml.rels': 'ada136c2e7d05a317579c9503c1f0dd8dfaa8f0cce687df86fe59e36d5fa5d57',
    'word/styles.xml': '83adf799de91eac3e9bf1e658b8c2f53586c700be35df179e1b2f1765f189dfc',
    'word/numbering.xml': '8d1f7769c35af16db98942ba09882646b45ba404e83686d4ce7a376c6a911f0f',
    'word/settings.xml': 'b3c72bc5720862c1a9c1449d89903673777f30df6ff38ceb23da73e21cad22e1',
    'word/footer1.xml': 'e693781f92e5a7ccf747fd2ed3facb627e448f389b021882cac589d746aa709b',
}


def part_digests() -> dict[str, str]:
    from legaldown_render.style import load_style

    tree = render(SOURCE, format="text").tree
    data = DocxWriter(load_style("default"), generator="golden").write(tree)
    return {name: hashlib.sha256(content).hexdigest() for name, content in parts(data).items()}


def test_the_parts_of_a_fixed_document_are_what_they_were() -> None:
    digests = part_digests()
    assert digests == GOLDEN, "\n".join(f"    {name!r}: {digest!r}," for name, digest in digests.items())
