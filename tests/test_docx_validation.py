"""Every part of every generated document validates against the ISO/IEC 29500-4 transitional
schemas (WordprocessingML, content types, relationships, extended properties), and the package
is sound: each relationship targets a part, each part has a content type. The schemas are
fetched, not stored (tests/schemas/README.md); the tests skip without them, and fail instead when
``LEGALDOWN_REQUIRE_DOCX_TOOLS`` is set, as in CI."""
from __future__ import annotations

import random

import pytest
from conftest import DOCUMENTS, spec_root
from docx_support import parts, repackage, require_schemas, schema_for, tool_missing, validation_errors
from test_docx_escaping import FREE_TEXT_SETTINGS, document

from legaldown_render import DocumentError, render

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
    {"page.size": "22in 22in", "page.margin": "0", "typography.font_size": "1023pt", "typography.line_height": "1584pt",
     "headings.1.size": "1638pt"},
    {"page.size": "1.5in 3in", "page.margin": "0.5in", "typography.font_size": "1pt"},
    {"page.margin": "25MM 1IN", "typography.font_size": "12PT"},
])
def test_the_settings_that_shape_the_parts_keep_them_valid(overrides: dict) -> None:
    source = ("---\nlegaldown: '0.2'\ntitle: Agreement\nversion: '1'\neffective_date: 2026-01-02\n---\n\n"
              "# One\n\ntext\n\n## Two\n\ntext\n")
    assert validation_errors(make(source, overrides=overrides)) == []


def test_an_empty_document_validates() -> None:
    assert validation_errors(make("---\ntitle: T\n---\n")) == []


def moved(text: str, first: str, second: str) -> str:
    """*text* with the element that starts with *first* moved after the one that starts with *second*
    (both self-closing, *first* the earlier)."""
    start = text.index(first)
    end = text.index("/>", start) + 2
    element = text[start:end]
    after = text.index("/>", text.index(second)) + 2
    return text[:start] + text[end:after] + element + text[after:]


def test_the_schema_check_catches_a_wrong_order_in_the_settings() -> None:
    """The validation is not vacuous: ``updateFields`` after ``compat`` (the order both prototypes
    had) is rejected."""
    original = make("---\ntitle: T\n---\n# A\n")
    settings = parts(original)["word/settings.xml"].decode("utf-8")
    element = '<w:updateFields w:val="true"/>'
    assert element in settings
    broken = repackage(original, "word/settings.xml",
                       lambda text: text.replace(element, "").replace("</w:settings>", element + "</w:settings>"))
    assert any("updateFields" in problem for problem in validation_errors(broken))


def test_the_schema_check_catches_an_element_out_of_order_in_a_style_and_in_the_section() -> None:
    original = make("---\ntitle: T\n---\n# A\n")
    assert validation_errors(original) == []
    style = repackage(original, "word/styles.xml",
                      lambda text: text.replace('<w:name w:val="Title"/><w:basedOn w:val="Normal"/>',
                                                '<w:basedOn w:val="Normal"/><w:name w:val="Title"/>', 1))
    assert any("styles.xml" in problem for problem in validation_errors(style))
    section = repackage(original, "word/document.xml", lambda text: moved(text, "<w:pgSz", "<w:pgMar"))
    assert any("document.xml" in problem and "pgSz" in problem for problem in validation_errors(section))
    attribute = repackage(original, "word/document.xml", lambda text: text.replace('w:orient="', 'w:x="').replace(
        "<w:pgSz ", '<w:pgSz w:w="99999999999999999999" ', 1))
    assert validation_errors(attribute)
    relationship = repackage(original, "word/_rels/document.xml.rels", lambda text: text.replace("styles.xml", "nope.xml"))
    assert any("nope.xml" in problem for problem in validation_errors(relationship))


@pytest.mark.parametrize("seed", range(15))
def test_documents_of_any_text_validate(seed: int) -> None:
    rnd = random.Random(1000 + seed)
    overrides = {key: "".join(rnd.choice("ab \x01\x00\ud800<&ž") for _ in range(10)) + "{page}" for key in FREE_TEXT_SETTINGS}
    assert validation_errors(make(document(rnd), overrides=overrides)) == []


UNITS = ["mm", "cm", "in", "pt", "pc", "px", "em", "rem", "%", "ch", "ex", "", "MM"]


def random_length(rnd: random.Random) -> str:
    """A number as small, as large or as odd as a style might write it."""
    whole = rnd.choice(["0", str(rnd.randint(0, 40)), str(rnd.randint(0, 100000)), "9" * rnd.randint(1, 45),
                        "1" + "0" * rnd.randint(1, 400)])
    fraction = rnd.choice(["", "", "." + "".join(rnd.choice("0123456789") for _ in range(rnd.randint(1, 40)))])
    return f"{whole}{fraction}{rnd.choice(UNITS)}"


@pytest.mark.parametrize("seed", range(60))
def test_random_lengths_are_a_style_error_or_a_valid_document(seed: int) -> None:
    from legaldown_render import StyleError

    rnd = random.Random(5000 + seed)
    keys = ["page.size", "page.margin", "typography.font_size", "typography.line_height", "headings.1.size",
            "headings.3.size"]
    overrides = {key: " ".join(random_length(rnd) for _ in range(rnd.randint(1, 4)))
                 for key in rnd.sample(keys, rnd.randint(1, len(keys)))}
    try:
        data = make("---\ntitle: T\n---\n# A\n\ntext\n", overrides=overrides)
    except StyleError as error:
        assert not any(value.split()[0][:12] in str(error) for value in overrides.values() if len(value.split()[0]) > 12)
        return
    assert validation_errors(data) == []


# -- The specification's own documents ---------------------------------------------------------------

SPEC = spec_root()
SPEC_FILES = sorted(path for folder in ("examples", "fixtures") for path in (SPEC / folder).rglob("*.lgd")) if SPEC else []


def test_the_specification_corpus_is_available() -> None:
    """A run that promises its tools (CI) must have the corpus it validates."""
    if SPEC is None:
        tool_missing("no LegalDown specification checkout (set LEGALDOWN_SPEC_DIR)")
    assert SPEC_FILES


@pytest.mark.parametrize("style", ["default", "outline"])
@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda path: str(path.relative_to(SPEC)) if SPEC else str(path))
def test_every_specification_document_validates(path: object, style: str) -> None:
    source = path.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    try:
        data = make(source, style=style)
    except DocumentError:
        pytest.skip("not a document (invalid frontmatter)")
    assert validation_errors(data) == []
