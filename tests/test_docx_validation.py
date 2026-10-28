"""Every part of every generated document validates against the ISO/IEC 29500-4 transitional
schemas (WordprocessingML, content types, relationships, extended properties), and the package
is sound: each relationship targets a part, each part has a content type. The schemas are
fetched, not stored (tests/schemas/README.md); the tests skip without them, and fail instead when
``LEGALDOWN_REQUIRE_DOCX_TOOLS`` is set, as in CI."""
from __future__ import annotations

import random

import pytest
from conftest import DOCUMENTS
from docx_support import parts, require_schemas, schema_for, validation_errors
from test_docx_escaping import FREE_TEXT_SETTINGS, document

from legaldown_render import render

pytestmark = pytest.mark.ooxml_schema

DOCUMENT_NAMES = ("features", "template")
STYLES = ("default", "continental", "outline")


def make(source: str, **settings: object) -> bytes:
    result = render(source, format="docx", **settings)
    assert result.binary is not None
    return result.binary


@pytest.fixture(autouse=True)
def _schemas() -> None:
    require_schemas()


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("name", DOCUMENT_NAMES)
def test_the_test_documents_validate_under_every_built_in_style(name: str, style: str) -> None:
    source = (DOCUMENTS / f"{name}.lgd").read_text(encoding="utf-8")
    assert validation_errors(make(source, style=style)) == []


def test_every_xml_part_has_a_schema_except_the_core_properties() -> None:
    names = parts(make("---\ntitle: T\n---\n# A\n"))
    assert {name: schema_for(name) for name in names} == {
        "[Content_Types].xml": "types", "_rels/.rels": "rels", "docProps/core.xml": None, "docProps/app.xml": "app",
        "word/document.xml": "wml", "word/_rels/document.xml.rels": "rels", "word/styles.xml": "wml",
        "word/numbering.xml": "wml", "word/settings.xml": "wml", "word/footer1.xml": "wml"}


@pytest.mark.parametrize("overrides", [
    {},
    {"page.footer": {"center": ""}},
    {"page.header.left": "{title}", "page.header.center": "{date}", "page.header.right": "{page}/{pages} {version}"},
    {"page.header.center": "C", "page.footer": {"left": "L", "center": "", "right": "R"}},
    {"page.header.right": "R", "page.footer.center": ""},
    {"docx.update_fields_on_open": False},
    {"page.size": "A4 landscape", "page.margin": "1in 2in 3in 4in"},
    {"page.size": "letter", "page.margin": "0"},
    {"page.size": "10cm 20cm", "typography.font_size": "9pt", "typography.line_height": "14pt"},
    {"typography.justify": True, "typography.color": "rgb(10, 20, 30)", "typography.link_color": "hsl(200, 50%, 40%)"},
    {"definitions.style": "small-caps", "definitions.term_style": "underline"},
    {"headings": {1: {"transform": "small-caps", "align": "center", "italic": True, "weight": "400"}}},
    {"typography.font_family": "'Open Sans', sans-serif"},
])
def test_the_settings_that_shape_the_parts_keep_them_valid(overrides: dict) -> None:
    source = ("---\nlegaldown: '0.2'\ntitle: Agreement\nversion: '1'\neffective_date: 2026-01-02\n---\n\n"
              "# One\n\ntext\n\n## Two\n\ntext\n")
    assert validation_errors(make(source, overrides=overrides)) == []


def test_an_empty_document_validates() -> None:
    assert validation_errors(make("---\ntitle: T\n---\n")) == []


def test_the_schema_check_catches_a_wrong_order_in_the_settings() -> None:
    """The validation is not vacuous: ``updateFields`` after ``compat`` (the order both prototypes
    had) is rejected."""
    import io
    import zipfile

    original = parts(make("---\ntitle: T\n---\n# A\n"))
    settings = original["word/settings.xml"].decode("utf-8")
    start = settings.index("<w:updateFields")
    end = settings.index("/>", start) + 2
    element = settings[start:end]
    broken = settings.replace(element, "").replace("</w:settings>", element + "</w:settings>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in original.items():
            archive.writestr(name, broken.encode("utf-8") if name == "word/settings.xml" else content)
    assert any("updateFields" in problem for problem in validation_errors(buffer.getvalue()))


@pytest.mark.parametrize("seed", range(15))
def test_documents_of_any_text_validate(seed: int) -> None:
    rnd = random.Random(1000 + seed)
    overrides = {key: "".join(rnd.choice("ab \x01\x00\ud800<&ž") for _ in range(10)) + "{page}" for key in FREE_TEXT_SETTINGS}
    assert validation_errors(make(document(rnd), overrides=overrides)) == []
