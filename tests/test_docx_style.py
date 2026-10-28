"""The style settings the DOCX writer adds: ``page.header``, ``page.footer`` and
``docx.update_fields_on_open`` (docs/style-templates.md), and how a style's CSS-valued
settings become Word's units, with the problems only DOCX has raised as a
:class:`StyleError` before anything is rendered (docs/decisions/0006, 0008)."""
from __future__ import annotations

import pytest
from conftest import FRONT

from legaldown_render import RenderOptions, StyleError, dump_style, load_style, render
from legaldown_render.style import Docx, Page, PageText, parse_override
from legaldown_render.style.docx import docx_settings

# -- page.header and page.footer ---------------------------------------------------------


def test_the_default_footer_is_the_page_number_and_count_and_the_header_is_empty() -> None:
    page = load_style("default").page
    assert page.footer == PageText(center="{page} / {pages}")
    assert page.header == PageText()
    assert not page.header
    assert page.footer


def test_header_and_footer_are_set_by_side_in_a_file_and_by_a_dotted_override() -> None:
    style = load_style("default", overrides={"page.header.left": "{title}", "page.header.right": "{version}, {date}",
                                             "page.footer": {"center": "", "right": "{page}"}})
    assert style.page.header == PageText(left="{title}", right="{version}, {date}")
    assert style.page.footer == PageText(right="{page}")


def test_every_field_is_accepted_in_every_position() -> None:
    template = "{page} {pages} {title} {version} {date}"
    overrides = {f"page.{part}.{side}": template for part in ("header", "footer") for side in ("left", "center", "right")}
    assert load_style("default", overrides=overrides).page.header.center == template


@pytest.mark.parametrize("key", ["page.header.left", "page.header.center", "page.header.right",
                                 "page.footer.left", "page.footer.center", "page.footer.right"])
def test_an_unknown_field_is_a_style_error_naming_the_setting(key: str) -> None:
    with pytest.raises(StyleError) as caught:
        load_style("default", overrides={key: "{pgae}"})
    assert any(problem.startswith(f"{key}: unknown field {{pgae}}") for problem in caught.value.problems)
    assert "{date}, {page}, {pages}, {title}, {version}" in str(caught.value)


def test_braces_that_are_not_a_field_are_text() -> None:
    style = load_style("default", overrides={"page.footer.center": "{} {1} {a b}"})
    assert style.page.footer.center == "{} {1} {a b}"


def test_a_header_text_is_a_free_text_setting_with_the_untrusted_caps() -> None:
    assert load_style("default", overrides={"page.header.left": "x" * 200}, trusted=False).page.header.left
    with pytest.raises(StyleError, match="page.header.left: is longer than 200 characters"):
        load_style("default", overrides={"page.header.left": "x" * 201}, trusted=False)
    with pytest.raises(StyleError, match="page.footer.right: must not contain NUL or a lone surrogate"):
        load_style("default", overrides={"page.footer.right": "a\x00b"}, trusted=False)


def test_an_untrusted_style_does_not_quote_its_unknown_field() -> None:
    with pytest.raises(StyleError) as caught:
        load_style("default", overrides={"page.header.left": "{secret}"}, trusted=False)
    assert "secret" not in str(caught.value)
    assert "unknown field a {field}" in str(caught.value)


def test_a_header_is_checked_as_text_on_the_command_line() -> None:
    assert parse_override("page.header.left", "{title}") == "{title}"
    assert parse_override("page.footer.center", "'{page}'") == "{page}"


def test_print_style_writes_header_and_footer_and_loads_again() -> None:
    dumped = dump_style(load_style("default", overrides={"page.header.center": "{title}"}))
    assert "header:" in dumped and "footer:" in dumped and "update_fields_on_open: true" in dumped
    assert load_style(dumped).page.header.center == "{title}"


def test_a_page_object_built_by_hand_is_checked_like_data() -> None:
    from legaldown_render.style import Style

    with pytest.raises(StyleError, match="page.footer.center: unknown field"):
        load_style(Style(page=Page(footer=PageText(center="{nope}"))))


# -- docx.update_fields_on_open --------------------------------------------------------


def test_fields_are_updated_on_open_by_default_and_the_setting_is_a_boolean() -> None:
    assert load_style("default").docx == Docx(update_fields_on_open=True)
    assert load_style("default", overrides={"docx.update_fields_on_open": False}).docx.update_fields_on_open is False
    assert parse_override("docx.update_fields_on_open", "false") is False
    with pytest.raises(StyleError, match="docx.update_fields_on_open: must be true or false"):
        load_style("default", overrides={"docx.update_fields_on_open": "maybe"})


# -- docx.reference_text_switch -----------------------------------------------------------


def test_the_text_switch_is_documented_by_default_and_the_setting_is_one_of_three_words() -> None:
    assert load_style("default").docx == Docx(update_fields_on_open=True, reference_text_switch="documented")
    for word in ("off", "documented", "guess"):
        style = load_style("default", overrides={"docx.reference_text_switch": word})
        assert style.docx.reference_text_switch == word
        assert docx_settings(style).reference_text_switch == word
        assert parse_override("docx.reference_text_switch", word) == word
    with pytest.raises(StyleError, match="docx.reference_text_switch: must be one of off, documented, guess"):
        load_style("default", overrides={"docx.reference_text_switch": "always"})
    with pytest.raises(StyleError, match="docx.reference_text_switch: must be one of"):
        load_style("default", overrides={"docx.reference_text_switch": True})
    assert "reference_text_switch: documented" in dump_style(load_style("default"))
    assert load_style(dump_style(load_style("default", overrides={"docx.reference_text_switch": "guess"}))
                      ).docx.reference_text_switch == "guess"


def test_an_untrusted_style_does_not_quote_the_value_of_the_text_switch() -> None:
    with pytest.raises(StyleError) as caught:
        load_style("default", overrides={"docx.reference_text_switch": "secret"}, trusted=False)
    assert "secret" not in str(caught.value) and "must be one of off, documented, guess" in str(caught.value)


def test_an_unknown_docx_key_is_a_style_error_with_a_suggestion() -> None:
    with pytest.raises(StyleError, match=r"docx.update_field_on_open: unknown key \(did you mean 'update_fields_on_open'"):
        load_style("default", overrides={"docx.update_field_on_open": True})


# -- Lengths, colours, fonts: CSS in, Word's units out -----------------------------------


def settings(**overrides: object):  # noqa: ANN201 - a test helper
    return docx_settings(load_style("default", overrides=overrides))


def test_the_default_style_in_word_units() -> None:
    word = settings()
    assert (word.page.width, word.page.height, word.page.landscape) == (11906, 16838, False)
    assert (word.page.top, word.page.right, word.page.bottom, word.page.left) == (1417,) * 4
    assert word.page.text_width == 11906 - 2 * 1417
    assert (word.font, word.half_points, word.color, word.link_color) == ("Georgia", 22, "1A1A1A", "1F4E8C")
    assert (word.line, word.line_rule) == (360, "auto")
    assert word.update_fields_on_open is True
    assert word.headings[1].half_points == 25 and word.headings[1].caps and word.headings[1].bold
    assert word.headings[3].italic and not word.headings[5].bold


@pytest.mark.parametrize(("margin", "expected"), [
    ("1in", (1440,) * 4), ("10mm", (567,) * 4), ("2cm 1cm", (1134, 567, 1134, 567)),
    ("1cm 2cm 3cm", (567, 1134, 1701, 1134)), ("1cm 2cm 3cm 4cm", (567, 1134, 1701, 2268)),
    ("36pt", (720,) * 4), ("1pc", (240,) * 4), ("96px", (1440,) * 4), ("0", (0,) * 4),
    ("1em", (220,) * 4), ("2rem", (440,) * 4),
])
def test_margins_are_css_shorthand_in_twips(margin: str, expected: tuple[int, ...]) -> None:
    page = settings(**{"page.margin": margin}).page
    assert (page.top, page.right, page.bottom, page.left) == expected


def test_em_and_rem_follow_the_font_size() -> None:
    page = settings(**{"page.margin": "2em", "typography.font_size": "10pt"}).page
    assert page.top == 400
    big = settings(**{"typography.font_size": "2em"})
    assert big.half_points == 48  # 2em of the browser's 12pt
    assert settings(**{"typography.font_size": "1rem"}).half_points == 24
    assert settings(**{"headings.2.size": "2em", "typography.font_size": "10pt"}).headings[2].half_points == 40


@pytest.mark.parametrize(("size", "expected"), [
    ("A4", (11906, 16838, False)), ("a3", (16838, 23811, False)), ("A5", (8391, 11906, False)),
    ("letter", (12240, 15840, False)), ("Legal", (12240, 20160, False)), ("B5", (9979, 14173, False)),
    ("A4 landscape", (16838, 11906, True)), ("landscape", (16838, 11906, True)), ("auto", (11906, 16838, False)),
    ("letter portrait", (12240, 15840, False)), ("210mm 297mm", (11906, 16838, False)),
    ("297mm 210mm", (16838, 11906, True)), ("10cm", (5669, 5669, False)),
])
def test_page_sizes(size: str, expected: tuple[int, int, bool]) -> None:
    page = settings(**{"page.size": size}).page
    assert (page.width, page.height, page.landscape) == expected


@pytest.mark.parametrize(("height", "line", "rule"), [
    ("normal", 240, "auto"), ("1", 240, "auto"), ("1.5", 360, "auto"), ("2", 480, "auto"),
    ("18pt", 360, "atLeast"), ("1.5em", 330, "atLeast"),
])
def test_line_height(height: str, line: int, rule: str) -> None:
    word = settings(**{"typography.line_height": height})
    assert (word.line, word.line_rule) == (line, rule)


@pytest.mark.parametrize(("color", "expected"), [
    ("#1a1a1a", "1A1A1A"), ("#abc", "AABBCC"), ("#abcd", "AABBCC"), ("#11223344", "112233"), ("red", "FF0000"),
    ("Navy", "000080"), ("rgb(255, 0, 128)", "FF0080"), ("rgb(100% 0% 0%)", "FF0000"), ("rgba(0,0,255,0.5)", "0000FF"),
    ("hsl(120, 100%, 25%)", "008000"), ("hsl(0 0% 100%)", "FFFFFF"),
])
def test_colours(color: str, expected: str) -> None:
    assert settings(**{"typography.color": color}).color == expected


@pytest.mark.parametrize(("family", "expected"), [
    ('Georgia, "Times New Roman", serif', "Georgia"), ('"Times New Roman", serif', "Times New Roman"),
    ("'Open Sans', sans-serif", "Open Sans"), ("serif", "Times New Roman"), ("sans-serif", "Arial"),
    ("monospace", "Courier New"), ("Calibri", "Calibri"), ("", "Times New Roman"),
])
def test_the_first_font_family(family: str, expected: str) -> None:
    assert settings(**{"typography.font_family": family}).font == expected


@pytest.mark.parametrize(("weight", "bold"), [("700", True), ("bold", True), ("600", True), ("400", False),
                                              ("normal", False), ("lighter", False), ("bolder", True)])
def test_heading_weight(weight: str, bold: bool) -> None:
    assert settings(**{"headings.2.weight": weight}).headings[2].bold is bold


def test_uppercase_and_small_caps_and_alignment() -> None:
    word = settings(**{"headings.2.transform": "small-caps", "headings.2.align": "center"})
    assert word.headings[2].small_caps and not word.headings[2].caps and word.headings[2].align == "center"


# -- What Word cannot express is a StyleError for DOCX only --------------------------------


@pytest.mark.parametrize("unit", ["%", "ch", "ex"])
@pytest.mark.parametrize("key", ["page.margin", "page.size", "typography.font_size", "typography.line_height",
                                 "headings.1.size"])
def test_relative_units_word_cannot_express_are_a_style_error_for_docx(key: str, unit: str) -> None:
    style = load_style("default", overrides={key: f"5{unit}"})  # HTML takes any of them
    with pytest.raises(StyleError) as caught:
        docx_settings(style)
    assert any(problem.startswith(f"{key}: uses '{unit}'") for problem in caught.value.problems)
    assert render(FRONT + "# A\n", RenderOptions(style=style)).output.startswith("<!DOCTYPE html>")


def test_the_render_api_refuses_before_rendering_and_html_still_renders() -> None:
    options = {"page.margin": "5%", "typography.color": "var(--ink)", "typography.font_size": "3ch"}
    with pytest.raises(StyleError) as caught:
        render(FRONT + "# A\n", format="docx", overrides=options)
    assert {problem.split(":")[0] for problem in caught.value.problems} == {
        "typography.font_size", "page.margin", "typography.color"}
    assert render(FRONT + "# A\n", format="html", overrides=options).ok


@pytest.mark.parametrize(("key", "value", "fragment"), [
    ("page.margin", "1cm 2cm 3cm 4cm 5cm", "must be one to four lengths"),
    ("page.margin", "auto", "not a length the DOCX writer can read"),
    ("page.margin", "-1cm", "must not be negative"),
    ("page.margin", "5", "needs a unit"),
    ("page.size", "A4 A3", "not a length"),
    ("page.size", "210mm landscape", "orientation goes with a paper size"),
    ("page.size", "0 10mm", "greater than 0"),
    ("typography.font_size", "0", "between 1pt and 1023pt"),
    ("typography.font_size", "2000pt", "between 1pt and 1023pt"),
    ("headings.1.size", "2000pt", "between 1pt and 1638pt"),
    ("typography.line_height", "0", "greater than 0"),
    ("typography.line_height", "calc(1 + 1)", "not a length"),
    ("typography.color", "hsl(", "cannot read"),
    ("typography.link_color", "rebeccapurple", "write it as #rrggbb"),
    ("headings.1.weight", "heavy", "not a weight"),
])
def test_other_values_word_cannot_read_are_named(key: str, value: str, fragment: str) -> None:
    with pytest.raises(StyleError) as caught:
        docx_settings(load_style("default", overrides={key: value}))
    assert any(problem.startswith(f"{key}: ") and fragment in problem for problem in caught.value.problems), \
        caught.value.problems


def test_every_problem_is_reported_together_and_an_untrusted_style_is_not_quoted() -> None:
    style = load_style("default", overrides={"page.margin": "5%", "typography.font_size": "9ch"}, trusted=False)
    with pytest.raises(StyleError) as caught:
        docx_settings(style, trusted=False)
    assert len(caught.value.problems) == 2
    assert caught.value.source == "'untrusted style'"
    assert "5%" not in str(caught.value) and "9ch" not in str(caught.value)


def test_the_built_in_styles_convert() -> None:
    for name in ("default", "outline", "continental"):
        docx_settings(load_style(name))


# -- Numeric extremes: a StyleError, never a crash and never a value Word or the schema refuses ---------------

DIGITS = ["1" + "0" * 40, "9" * 30, "9" * 33, "9" * 100, "9" * 400, "9" * 5000, "1" + "0" * 40 + "." + "9" * 40]
LENGTH_KEYS = ["page.size", "page.margin", "typography.font_size", "typography.line_height", "headings.1.size"]


@pytest.mark.parametrize("unit", ["mm", "pt", "in", "em", "px"])
@pytest.mark.parametrize("digits", DIGITS)
@pytest.mark.parametrize("key", LENGTH_KEYS)
def test_an_enormous_length_is_a_style_error_that_does_not_quote_it(key: str, digits: str, unit: str) -> None:
    for trusted in (True, False) if len(digits) < 150 else (True,):  # an untrusted text is at most 200 characters
        style = load_style("default", overrides={key: digits + unit}, trusted=trusted)
        with pytest.raises(StyleError) as caught:
            docx_settings(style, trusted=trusted)
        assert digits[:12] not in str(caught.value)
    with pytest.raises(StyleError):
        render(FRONT + "# A\n", format="docx", overrides={key: digits + unit})


@pytest.mark.parametrize("value", ["inf", "nan", "1e999", "9" * 400, "-" + "9" * 400])
@pytest.mark.parametrize("function", ["rgb({0}, 0, 0)", "rgb(0 {0} 0)", "rgba(0,0,{0},1)", "hsl({0} 50% 50%)",
                                      "hsl(10 {0}% 50%)", "hsl(10 50% {0}%)"])
def test_a_colour_component_that_is_not_a_finite_number_is_a_style_error(function: str, value: str) -> None:
    with pytest.raises(StyleError, match="typography.color: is a colour the DOCX writer cannot read"):
        docx_settings(load_style("default", overrides={"typography.color": function.format(value)}))


@pytest.mark.parametrize(("key", "value", "fragment"), [
    ("page.size", "7pt", "each edge must be between 7.2pt and 1584pt"),
    ("page.size", "1600pt 1600pt", "each edge must be between"),
    ("page.size", "5pt 20in", "each edge must be between"),
    ("page.margin", "1600pt", "each margin must be between 0 and 1584pt"),
    ("page.margin", "50mm 100mm", "leave no room for text"),
    ("page.margin", "150mm 0", "leave no room for text"),
    ("page.margin", "105mm", "leave no room for text"),
    ("typography.line_height", "1600pt", "at most 1584pt"),
    ("typography.font_size", "1024pt", "between 1pt and 1023pt"),
])
def test_values_outside_word_s_limits_are_named(key: str, value: str, fragment: str) -> None:
    with pytest.raises(StyleError) as caught:
        docx_settings(load_style("default", overrides={key: value}))
    assert any(problem.startswith(f"{key}: ") and fragment in problem for problem in caught.value.problems), \
        caught.value.problems


def test_the_largest_values_that_fit_are_accepted_and_nothing_is_negative_or_overflows() -> None:
    word = docx_settings(load_style("default", overrides={
        "page.size": "22in 22in", "page.margin": "0", "typography.font_size": "1023pt",
        "typography.line_height": "1584pt", "headings.1.size": "1638pt"}))
    assert (word.page.width, word.page.height, word.page.text_width) == (31680, 31680, 31680)
    assert word.half_points == 2046 and round(word.half_points * 1.6) <= 3276
    narrow = docx_settings(load_style("default", overrides={"page.size": "1.5in 3in", "page.margin": "0.5in"}))
    assert narrow.page.text_width == 720 and narrow.page.header >= 0


@pytest.mark.parametrize("value", ["25MM", "25Mm", "1IN", "72PT", "6PC", "96PX", "1EM", "1.5REM"])
def test_units_are_not_case_sensitive(value: str) -> None:
    assert docx_settings(load_style("default", overrides={"page.margin": value})).page.top > 0
    with pytest.raises(StyleError, match="uses '%'"):
        docx_settings(load_style("default", overrides={"page.margin": "5%"}))
