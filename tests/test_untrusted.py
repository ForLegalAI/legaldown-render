"""Rendering documents and styles from somebody else (§2.3, docs/architecture.md
Security): ``load_style(trusted=False)`` — built-in names, mappings and YAML
text only, ``extends`` only built-ins, strict per-kind CSS grammars, no raw CSS,
a size cap and no YAML aliases, and messages that never quote the style — and
the ``untrusted`` render option and ``--untrusted`` flag, which also require a
document root. What holds for every style (the forbidden tokens) is in
test_style_validation.py."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from conftest import DOCUMENTS, FEATURES, FRONT

from legaldown_render import OptionsError, RenderOptions, StyleError, builtin_styles, load_style, render
from legaldown_render.cli import main
from legaldown_render.style import HeadingStyle, Style, Typography, style_to_dict
from legaldown_render.style.loader import MAX_STYLE_DEPTH, MAX_UNTRUSTED_STYLE_BYTES

SECRET = "hunter2-correct-horse"
DOC = FRONT + "\n# One {#one}\n\nText {{ref: one}}.\n"


def refused(style: object = None, overrides: dict | None = None) -> StyleError:
    with pytest.raises(StyleError) as caught:
        load_style(style, overrides=overrides, trusted=False)  # type: ignore[arg-type]
    return caught.value


# -- Where an untrusted style may come from -------------------------------------

@pytest.mark.parametrize("name", builtin_styles())
def test_builtin_styles_pass_the_strict_grammar(name: str) -> None:
    assert load_style(name, trusted=False) == load_style(name)


def test_the_default_style_passes_as_an_object_and_as_its_own_dump() -> None:
    assert load_style(Style(), trusted=False) == Style()
    assert load_style(style_to_dict(Style()), trusted=False) == Style()


def test_no_style_at_all_is_the_default() -> None:
    assert load_style(None, trusted=False) == load_style("default")


@pytest.mark.parametrize("spec", ["style.yaml", "style.yml", "./style.yaml", "sub/style", "sub\\style"])
def test_a_path_is_refused_even_when_the_file_exists(spec: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "sub").mkdir()
    for name in ("style.yaml", "style.yml", "sub/style"):
        (tmp_path / name).write_text("version: 1\nlocale: cs-CZ\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert refused(spec).problems == [
        f"an untrusted style is a built-in style (built-in: {', '.join(builtin_styles())}), a mapping, or YAML "
        "text, never a file"]


def test_a_path_object_and_an_absolute_path_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "house.yaml"
    path.write_text("version: 1\nlocale: cs-CZ\n", encoding="utf-8")
    for spec in (path, str(path), "/etc/passwd", "/etc/shadow.yaml"):
        error = refused(spec)
        assert "never a file" in str(error)
        assert str(tmp_path) not in str(error)
        assert "passwd" not in str(error)


def test_an_unknown_name_is_refused_without_quoting_it() -> None:
    assert SECRET not in str(refused(SECRET))


def test_a_mapping_and_yaml_text_are_accepted() -> None:
    assert load_style({"locale": "cs-CZ"}, trusted=False).locale == "cs-CZ"
    assert load_style("version: 1\nlocale: cs-CZ\nnumbering: {scheme: none}\n", trusted=False).numbering.scheme == "none"


def test_extends_may_name_a_builtin_style() -> None:
    assert load_style({"extends": "continental"}, trusted=False).paragraphs.numbered
    assert load_style("extends: outline\nlocale: cs-CZ\n", trusted=False).numbering.scheme == "legal-outline"


@pytest.mark.parametrize("parent", ["parent.yaml", "./parent.yaml", "../parent.yaml", "ABS", "sub/x", "nope", 3, ""])
def test_extends_a_path_or_anything_but_a_builtin_is_refused(parent: object, tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "parent.yaml").write_text("version: 1\nlocale: cs-CZ\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    if parent == "ABS":
        parent = str(tmp_path / "parent.yaml")
    error = refused({"extends": parent})
    assert error.problems and all(problem.startswith("extends:") for problem in error.problems)
    assert str(tmp_path) not in str(error)


def test_a_file_cannot_be_reached_through_an_untrusted_render(tmp_path: Path) -> None:
    (tmp_path / "house.yaml").write_text("version: 1\nlocale: cs-CZ\n", encoding="utf-8")
    with pytest.raises(StyleError, match="never a file"):
        render(DOC, style=tmp_path / "house.yaml", untrusted=True, document_root=tmp_path)
    # The operator's own render still reads it.
    assert render(DOC, style=tmp_path / "house.yaml").style.locale == "cs-CZ"


# -- The strict grammars ----------------------------------------------------------

#: setting -> (values that pass, values that do not)
GRAMMARS: dict[str, tuple[list[str], list[str]]] = {
    "typography.font_size": (
        ["11pt", "1.2em", "0.9rem", ".5em", "12px", "10mm", "0", "100%", "1in", "2ch"],
        ["11", "-1em", "1e3em", "calc(1em)", "var(--x)", "11 pt", " 11pt", "11pt ", "11pt 12pt", "1em!important", "",
         "medium", "url(x)", "1.em", "１１pt", "11pt\n"]),
    "headings.1.size": (["1em", "1.15em", "14pt"], ["big", "1em 2em", "calc(1em + 1px)", "-0.5em"]),
    "typography.line_height": (
        ["normal", "1.5", "2", ".9", "1.2em", "18pt", "150%", "0"],
        ["1.5.5", "-1", "calc(1.5)", "var(--lh)", "1.5 2", "tall", "1,5"]),
    "typography.color": (
        ["#000", "#1a1a1a", "#1a1a1a80", "#abcd", "red", "currentcolor", "rgb(10, 20, 30)", "rgba(0,0,0,.5)",
         "rgb(10 20 30 / 50%)", "hsl(120, 50%, 50%)"],
        ["#12", "#12345", "#ggg", "#1234567", "red blue", "rgb(", "rgb(1,2,3", "var(--ink)", "rgb(var(--x))",
         "url(x)", "r", "expression(x)", "rgb(a)", "red!important", "#fff;"]),
    "typography.link_color": (["#1f4e8c", "blue"], ["var(--link)", "#12"]),
    "headings.1.weight": (
        ["700", "400", "normal", "bold", "bolder", "lighter", "100", "1000", "350", "1"],
        ["0", "1001", "heavy", "700 800", "-700", "7.5", "var(--w)", "bold!important"]),
    "typography.font_family": (
        ['Georgia, "Times New Roman", serif', "serif", "Helvetica Neue, Arial", "'Source Serif', serif",
         '"Palatino Linotype",Palatino,serif', "-apple-system, sans-serif", '"Čeština Sans", serif'],
        ["Georgia; color: red", "serif}", 'url("x")', "Georgia, ", ", serif", '"unterminated', "serif /* c */",
         "var(--font)", "a" * 100, '"a\\"b"', "Georgia,, serif", "Times (New)", "1font", "serif,\nsans"]),
    "page.size": (
        ["A4", "A3", "a5", "letter", "Legal", "A4 landscape", "landscape", "portrait", "auto", "210mm 297mm", "8.5in"],
        ["A4 A3", "huge", "A4 sideways", "A4;", "calc(1mm)", "210 297", "A4 landscape portrait", "A9"]),
    "page.margin": (
        ["25mm", "1in", "20mm 15mm", "0", "1em 2em 3em", "1cm 2cm 3cm 4cm", "5%"],
        ["25", "1cm 2cm 3cm 4cm 5cm", "auto", "20mm,15mm", "calc(1mm)", "-1cm", "var(--m)", "20mm  15mm"]),
    "html.max_width": (
        ["46rem", "100%", "800px", "none", "0", "70ch"],
        ["auto", "calc(100% - 2rem)", "min(46rem, 100%)", "46", "fit-content(20em)", "46rem 2em"]),
}


def test_the_table_covers_every_css_setting() -> None:
    from test_style_validation import CSS_SETTINGS

    assert sorted(GRAMMARS) == sorted(CSS_SETTINGS)


@pytest.mark.parametrize(("key", "value"), [(key, value) for key, (good, _) in GRAMMARS.items() for value in good])
def test_a_plain_value_passes_in_both_modes(key: str, value: str) -> None:
    trusted = load_style("default", overrides={key: value})
    assert load_style("default", overrides={key: value}, trusted=False) == trusted


@pytest.mark.parametrize(("key", "value"), [(key, value) for key, (_, bad) in GRAMMARS.items() for value in bad])
def test_an_unusual_value_is_refused_untrusted_with_the_setting_named(key: str, value: str) -> None:
    error = refused("default", {key: value})
    assert [problem.split(":")[0] for problem in error.problems] == [key]


@pytest.mark.parametrize(("key", "value"), [(key, value) for key, (_, bad) in GRAMMARS.items() for value in bad
                                            if not any(token in value for token in ("/*", "*/", "\\", "\n"))])
def test_the_same_values_load_when_trusted(key: str, value: str) -> None:
    """A trusted style is only held to the forbidden tokens (or a control character)."""
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        pytest.skip("control characters are refused in every mode")
    assert load_style("default", overrides={key: value})


def test_a_number_that_yaml_reads_is_text_to_the_grammar() -> None:
    assert load_style({"headings": {1: {"weight": 700}}, "typography": {"line_height": 1.5}},
                      trusted=False).typography.line_height == "1.5"
    assert refused({"typography": {"font_size": 11}}).problems


def test_every_problem_is_reported_together() -> None:
    error = refused({"typography": {"font_size": "x", "color": "y y"}, "page": {"size": "z z"}})
    assert [problem.split(":")[0] for problem in error.problems] == ["typography.font_size", "typography.color",
                                                                     "page.size"]


def test_overrides_are_held_to_the_grammar_too() -> None:
    assert refused("continental", {"typography": {"font_size": "calc(1em)"}}).problems
    assert load_style("continental", overrides={"typography.font_size": "12pt"}, trusted=False).typography.font_size == "12pt"


# -- Raw CSS ----------------------------------------------------------------------

def test_extra_css_must_be_empty() -> None:
    assert load_style({"html": {"extra_css": ""}}, trusted=False).html.extra_css == ""
    for css in ("p { color: red }", " ", "/* x */"):
        error = refused({"html": {"extra_css": css}})
        assert error.problems == ["html.extra_css: is not allowed in an untrusted style (it is raw CSS); leave it empty"]
    assert refused("default", {"html.extra_css": "a { }"}).problems


def test_extra_css_still_works_for_a_trusted_style() -> None:
    assert load_style("default", overrides={"html.extra_css": "p { color: red }"}).html.extra_css


# -- A Style object ---------------------------------------------------------------

def test_a_style_object_is_held_to_the_grammar() -> None:
    style = Style(typography=Typography(font_size="calc(1em)"))
    assert load_style(style)  # trusted: accepted
    error = refused(style)
    assert [problem.split(":")[0] for problem in error.problems] == ["typography.font_size"]


def test_a_style_object_with_a_heading_or_raw_css_is_refused() -> None:
    assert refused(Style(headings={1: HeadingStyle(size="2 em")})).problems[0].startswith("headings.1.size:")
    style = Style()
    style = Style(**{**{f: getattr(style, f) for f in style_to_dict(style)}, "html": type(style.html)(extra_css="a{}")})
    assert refused(style).problems[0].startswith("html.extra_css:")


def test_a_settings_object_in_an_override_is_held_to_the_grammar() -> None:
    assert refused("default", {"typography": Typography(color="var(--x)")}).problems


# -- YAML text --------------------------------------------------------------------

def test_yaml_aliases_are_refused() -> None:
    text = "version: 1\nlabels: &l {colon: ':'}\ntypography: *l\n"
    error = refused(text)
    assert error.problems == ["YAML aliases are not allowed (line 3)"]
    # The shape that makes a small text enormous.
    bomb = "version: 1\na: &a [x, x, x, x]\nb: &b [*a, *a, *a, *a]\nc: &c [*b, *b, *b, *b]\n"
    assert "aliases" in str(refused(bomb))


def test_yaml_aliases_stay_valid_for_a_trusted_file(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text("version: 1\nlabels: &l {colon: ':'}\nplaceholders: {blank: '__'}\n", encoding="utf-8")
    assert load_style(path).labels.colon == ":"


def _text_of(size: int, *, multibyte: bool = False) -> str:
    head = "version: 1\n# "
    filler = ("é" if multibyte else "x")
    unit = len(filler.encode())
    return head + filler * ((size - len(head.encode()) - 1) // unit) + "\n"


def test_the_size_cap_counts_bytes() -> None:
    at_cap = _text_of(MAX_UNTRUSTED_STYLE_BYTES)
    assert len(at_cap.encode()) == MAX_UNTRUSTED_STYLE_BYTES
    assert load_style(at_cap, trusted=False) == Style()
    assert refused(at_cap + "x").problems == ["the style text is larger than 64 KiB"]
    # Fewer characters than the cap, more bytes.
    wide = _text_of(MAX_UNTRUSTED_STYLE_BYTES + 100, multibyte=True)
    assert len(wide) < MAX_UNTRUSTED_STYLE_BYTES < len(wide.encode())
    assert "64 KiB" in str(refused(wide))


def test_a_trusted_text_has_no_cap() -> None:
    assert load_style(_text_of(MAX_UNTRUSTED_STYLE_BYTES + 1000)) == Style()


def _flow(depth: int) -> str:
    return "a: " + "[" * depth + "\n" + "]" * depth + "\n"


def _block(depth: int) -> str:
    return "".join(" " * level + "a:\n" for level in range(depth)) + " " * depth + "b: 1\n"


@pytest.mark.parametrize("trusted", [True, False])
def test_the_nesting_limit_is_exact(trusted: bool) -> None:
    """A style is read to a fixed depth, in flow and in block form: the limit
    that keeps the scanner's cost linear in the size of the text."""
    limit = MAX_STYLE_DEPTH
    for make in (_flow, _block):
        assert "nested too deeply" in str(_loaded(make(limit + 1), trusted)), make
        assert "nested too deeply" not in str(_loaded(make(limit - 1), trusted)), make
    mixed = "a: " + "[" * (limit - 1) + "]" * (limit - 1) + "\n"
    assert "nested too deeply" not in str(_loaded(mixed, trusted))


def _loaded(text: str, trusted: bool) -> object:
    try:
        load_style(text, trusted=trusted)
    except StyleError as error:
        return error
    return ""


def test_a_deeply_nested_text_is_refused_after_a_bounded_amount_of_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """Counted, not timed: the scanner opens at most MAX_STYLE_DEPTH + 1 levels
    however deep the text goes, so the cost is linear in its size (PyYAML's
    is quadratic in the depth it is allowed to reach)."""
    from legaldown_render.style import loader

    opened = []
    for name in ("fetch_flow_collection_start", "add_indent"):
        original = getattr(yaml.SafeLoader, name)
        monkeypatch.setattr(yaml.SafeLoader, name, lambda self, *args, _o=original: opened.append(1) or _o(self, *args))
    for text, trusted in ((_flow(20000), False), (_block(300), False), ("- " * 30000 + "x\n", False),
                          (_flow(200000), True)):  # a style file has no size cap
        opened.clear()
        error = _loaded(text, trusted)
        assert "the text is nested too deeply" in str(error)
        assert len(opened) <= 2 * (loader.MAX_STYLE_DEPTH + 1)


def test_a_deeply_nested_mapping_is_refused_not_a_crash() -> None:
    deep: dict = {}
    node = deep
    for _ in range(5000):
        node["a"] = {}
        node = node["a"]
    for trusted in (True, False):
        assert "nested too deeply" in str(_loaded_mapping(deep, trusted))
        assert "nested too deeply" in str(_loaded_mapping({"typography": deep}, trusted))
    nested: list = []
    for _ in range(5000):
        nested = [nested]
    assert "nested too deeply" in str(_loaded_mapping({"enumeration": {"levels": nested}}, False))
    assert "nested too deeply" in str(refused("default", {"enumeration": {"levels": nested}}))


def _loaded_mapping(style: dict, trusted: bool) -> object:
    try:
        load_style(style, trusted=trusted)
    except StyleError as error:
        return error
    return ""


def test_a_non_mapping_and_a_wrong_version_are_refused_without_the_value() -> None:
    assert refused(f"- {SECRET}\n- x\n").problems == ["a style template must be a YAML mapping"]
    error = refused(f"version: {SECRET}\n")
    assert SECRET not in str(error)
    assert error.problems[0].startswith("version:")


# -- Messages never quote the style -----------------------------------------------

@pytest.mark.parametrize("text", [
    f"version: 1\napi_key: {SECRET}\ntypography: [unclosed\n",
    f"version: 1\napi_key: {SECRET}\n  bad: indentation\n",
    f"version: 1\napi_key: {SECRET}\ntypography: !{SECRET} x\n",
    f"version: 1\napi_key: {SECRET}\ntypography: *{SECRET}\n",
    f"version: 1\napi_key: {SECRET}\nlabels: {{colon: \"\\{SECRET[0]}\"}}\n",
    f"version: 1\napi_key: {SECRET}\nx: !!python/object:os.system {SECRET}\n",
    f"version: 1\n{SECRET}: [\n",
    f"version: 1\na: &{SECRET} 1\na: *{SECRET}x\n",
    f"version: 1\nx: !{SECRET}!y z\n",
])
def test_a_yaml_error_names_the_place_and_never_quotes_the_text(text: str) -> None:
    error = refused(text)
    assert SECRET not in str(error)
    assert "api_key" not in str(error)
    assert len(error.problems) == 1


def test_a_yaml_error_in_a_trusted_text_still_quotes_it() -> None:
    """The operator's own style: the snippet helps them find the mistake."""
    with pytest.raises(StyleError) as caught:
        load_style(f"version: 1\ntypography: [unclosed\napi_key: {SECRET}\n")
    assert "api_key" in str(caught.value)


@pytest.mark.parametrize("style", [
    {"typography": {"font_size": SECRET}},
    {"typography": {"font_size": [SECRET]}},
    {"typography": SECRET},
    {"numbering": {"scheme": SECRET}},
    {"numbering": {"levels": SECRET}},
    {"definitions": {"style": SECRET}},
    {"contents": {"enabled": SECRET}},
    {"contents": {"depth": SECRET}},
    {"version": SECRET},
    {"extends": SECRET},
    {"html": {"extra_css": SECRET}},
    {"headings": {SECRET: {"size": "1em"}}},
    {"headings": {7: {"size": SECRET}}},
    {"labels": {"condition": SECRET}},
    {"labels": {"condition": "{" + SECRET + "}"}},
])
def test_a_value_of_the_style_is_never_quoted(style: dict) -> None:
    assert SECRET not in str(refused(style))


def test_an_override_that_cannot_hold_keys_is_not_quoted() -> None:
    error = refused("default", {f"typography.font_size.{SECRET}": "1em"})
    assert SECRET not in str(error)
    with pytest.raises(StyleError) as caught:
        load_style("default", overrides={"typography.font_size": "1em", f"typography.font_size.{SECRET}": 1})
    assert SECRET in str(caught.value)


def test_an_untrusted_default_style_is_named_like_any_other_untrusted_style() -> None:
    assert refused("default", {"page.margin": "9" * 400 + "mm"}).source == "'untrusted style'"
    assert refused(None, {"page.margin": "9" * 400 + "mm"}).source == "'untrusted style'"


def test_the_style_object_name_is_not_quoted() -> None:
    error = refused(Style(name=SECRET, typography=Typography(font_size=SECRET)))
    assert SECRET not in str(error)
    assert error.source == "'untrusted style'"


def test_an_unknown_key_is_located_not_quoted() -> None:
    error = refused({"typography": {"x" * 500: 1, "font_sizee": 2, "bad\nkey\x1b[31m": 3}, SECRET: 4})
    message = str(error)
    assert error.problems[0].startswith("typography: unknown key (known: font_family")
    assert "typography: unknown key (did you mean 'font_size'?)" in error.problems
    assert error.problems[-1].startswith("(root): unknown key")
    assert "xxxxx" not in message and SECRET not in message and "\x1b" not in message
    assert message.count("\n") == len(error.problems)


def test_an_unknown_key_of_a_trusted_style_is_named() -> None:
    with pytest.raises(StyleError, match="typography.font_sizee: unknown key"):
        load_style({"typography": {"font_sizee": 2}})


def test_a_locale_is_named_not_quoted() -> None:
    with pytest.raises(StyleError) as caught:
        render(DOC, style={"locale": SECRET}, untrusted=True, document_root=".")
    assert SECRET not in str(caught.value)
    with pytest.raises(StyleError) as caught:
        render(DOC, style={"locale": SECRET})
    assert SECRET in str(caught.value)


# -- The render option ------------------------------------------------------------

def test_untrusted_needs_a_document_root(tmp_path: Path) -> None:
    with pytest.raises(OptionsError, match="document_root"):
        render(DOC, untrusted=True)
    with pytest.raises(OptionsError, match="document_root"):
        render(DOC, RenderOptions(untrusted=True, document_path=tmp_path / "a.lgd"))
    result = render(DOC, untrusted=True, document_root=tmp_path)
    assert result.ok


def test_untrusted_loads_the_style_untrusted() -> None:
    with pytest.raises(StyleError, match="typography.font_size"):
        render(DOC, untrusted=True, document_root=".", overrides={"typography.font_size": "calc(1em)"})
    with pytest.raises(StyleError, match="html.extra_css"):
        render(DOC, untrusted=True, document_root=".", overrides={"html.extra_css": "p{}"})


def test_untrusted_takes_yaml_text_and_a_builtin_name_for_the_style() -> None:
    text = "extends: continental\ntypography: {font_family: 'Georgia, serif', font_size: 12pt}\n"
    result = render(DOC, style=text, untrusted=True, document_root=".")
    assert "font-size: 12pt" in result.output
    assert render(DOC, style="outline", untrusted=True, document_root=".").style.numbering.scheme == "legal-outline"


@pytest.mark.parametrize("fmt", ["html", "text"])
def test_the_defaults_leave_existing_callers_alone(fmt: str) -> None:
    assert RenderOptions().untrusted is False
    css = "p { color: red }"
    assert render(DOC, format=fmt, overrides={"html.extra_css": css}).ok
    assert css in render(DOC, overrides={"html.extra_css": css}).output


def test_nothing_hostile_reaches_the_page() -> None:
    page = render(DOC, untrusted=True, document_root=".").output
    assert page.count("<style>") == 1 and page.count("</style>") == 1


# -- The command line -------------------------------------------------------------

def test_cli_untrusted_without_a_root_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([FEATURES, "--untrusted", "-q"]) == 2
    captured = capsys.readouterr()
    assert "document_root" in captured.err
    assert captured.out == ""


def test_cli_untrusted_with_a_root_renders(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([FEATURES, "--untrusted", "--root", str(DOCUMENTS), "-q", "--style", "continental"]) == 0
    assert "<style>" in capsys.readouterr().out


def test_cli_untrusted_refuses_a_style_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "house.yaml"
    path.write_text("version: 1\nlocale: cs-CZ\n", encoding="utf-8")
    assert main([FEATURES, "--untrusted", "--root", str(DOCUMENTS), "--style", str(path)]) == 2
    err = capsys.readouterr().err
    assert "never a file" in err and str(tmp_path) not in err
    # Without --untrusted the same style file is the operator's own.
    assert main([FEATURES, "--root", str(DOCUMENTS), "--style", str(path), "-q"]) == 0


def test_cli_untrusted_holds_set_values_to_the_grammar(capsys: pytest.CaptureFixture[str]) -> None:
    args = [FEATURES, "--root", str(DOCUMENTS), "--set", "typography.font_size=calc(1em + 2px)", "-q"]
    assert main([*args, "--untrusted"]) == 2
    assert "typography.font_size" in capsys.readouterr().err
    assert main(args) == 0


def test_cli_untrusted_with_stdin_and_a_root(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(DOC))
    assert main(["-", "--untrusted", "--root", str(DOCUMENTS), "-q", "-f", "text"]) == 0
    assert "One" in capsys.readouterr().out


# -- Whatever the YAML holds, an error and never a traceback --------------------------

@pytest.mark.parametrize("text", [
    f"a: !!int {SECRET}", f"a: !!float {SECRET}", f"a: !!bool {SECRET}", f"a: !!timestamp {SECRET}",
    "a: 2001-13-45", "a: 2001-01-01 99:99:99", "a: " + "9" * 5000, "a: 0x" + "f" * 5000,
    "? " + "1" * 5000 + "\n: 1", f"headings:\n  99: {{size: 1em}}\n  {SECRET}: 1\n", "headings:\n  " + "1" * 5000 + ": {}",
])
def test_a_yaml_value_that_does_not_convert_is_a_style_error_that_quotes_nothing(text: str) -> None:
    error = refused("version: 1\n" + text + "\n")
    assert SECRET not in str(error)
    assert len(str(error)) < 2000


def test_heading_levels_out_of_range_do_not_quote_the_key() -> None:
    error = refused({"headings": {99: {"size": "1em"}, SECRET: {"size": "1em"}}})
    assert error.problems == ["headings: heading levels are 1 to 5 (§4.1)", "headings: a key must be a number"]
    with pytest.raises(StyleError, match=r"headings\.99: heading levels"):
        load_style({"headings": {99: {"size": "1em"}}})


def test_any_other_exception_is_a_generic_style_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from legaldown_render.style import loader

    def explode(*args: object, **kwargs: object) -> None:
        raise ValueError(f"invalid literal: {SECRET}")

    monkeypatch.setattr(loader, "_build", explode)
    error = refused({"locale": "cs-CZ"})
    assert error.problems == ["the style could not be read"]
    assert SECRET not in str(error)
    with pytest.raises(ValueError, match=SECRET):  # the operator's own style raises what it raised
        load_style({"locale": "cs-CZ"})


def test_an_enormous_number_in_a_value_or_a_key_is_refused_without_str() -> None:
    big = 10 ** 5000
    for style in ({"typography": {"font_size": big}}, {"headings": {big: {}}}, {"x": {big: 1}},
                  {"enumeration": {"levels": [{"label": big}]}}, {"contents": {"depth": big}}):
        assert refused(style).problems
    assert refused("default", {"typography.font_size": big}).problems


# -- values.date, free text, characters --------------------------------------------------

@pytest.mark.parametrize("name", ["short", "medium", "long", "full"])
def test_the_named_date_formats_pass(name: str) -> None:
    assert load_style({"values": {"date": name}}, trusted=False).values.date == name


@pytest.mark.parametrize("pattern", ["dd.MM.yyyy", "y", "GGGGGGG", "QQQQQQ", "ZZZZZZZZ", "<script>", "", "'", "LONG"])
def test_an_untrusted_date_is_one_of_the_four_names(pattern: str) -> None:
    error = refused({"values": {"date": pattern}})
    assert error.problems == ["values.date: must be one of short, medium, long, full in an untrusted style"]


#: Every setting that holds free text: the name the output shows is repeated wherever it is used.
FREE_TEXT = ["name", "references.format", "placeholders.blank", "template_view.choice_separator",
             "template_view.empty_choice", "numbering.levels.0.label", "numbering.levels.0.ref",
             "enumeration.levels.0.label", "enumeration.levels.0.ref", "paragraphs.format.label",
             "paragraphs.format.ref", *(f"labels.{name}" for name in (
                 "drafting_note", "condition", "identification_number", "date_of_birth", "address", "represented_by",
                 "effective_date", "version", "attachments", "contents", "colon", "attachment_file", "signatures",
                 "signature_date", "signature_place", "signature_name", "signature_title"))]


def _with_text(key: str, text: str) -> dict:
    """A style with *text* at the dotted *key*; a list level is written as ``levels.0.label``."""
    style: dict = {}
    node = style
    parts = key.split(".")
    for index, part in enumerate(parts[:-1]):
        if parts[index + 1] == "0":
            node[part] = [{}]
            node = node[part][0]
        elif part == "0":
            continue
        else:
            node = node.setdefault(part, {})
    node[parts[-1]] = text
    return style


def test_the_free_text_table_covers_every_text_setting() -> None:
    import dataclasses

    from legaldown_render.style import model

    declared = set()
    for cls, prefix in ((model.Style, ""), (model.References, "references."), (model.Placeholders, "placeholders."),
                        (model.TemplateView, "template_view."), (model.Labels, "labels."),
                        (model.LevelFormat, "LEVEL.")):
        for item in dataclasses.fields(cls):
            if "str" in str(item.type) and "css" not in item.metadata:
                declared.add(prefix + item.name)
    declared -= {"version", "extends", "locale", "LEVEL.counter"}  # Style.version is an int; the others are not text shown
    listed = {key for key in FREE_TEXT if ".levels." not in key and "paragraphs." not in key}
    assert declared - {"LEVEL.label", "LEVEL.ref"} <= listed | {"locale"}, declared - listed


#: The field a setting must contain (docs/style-templates.md), so that a text of the right length is otherwise valid.
REQUIRED_FIELD = {"labels.condition": "{condition}", "labels.attachment_file": "{file}",
                  "references.format": "{designation}", "numbering.levels.0.ref": "{n}",
                  "enumeration.levels.0.ref": "{n}"}


@pytest.mark.parametrize("key", FREE_TEXT)
def test_free_text_is_capped_at_200_characters(key: str) -> None:
    prefix = REQUIRED_FIELD.get(key, "")
    load_style(_with_text(key, prefix + "x" * (200 - len(prefix))), trusted=False)
    error = refused(_with_text(key, prefix + "x" * (201 - len(prefix))))
    assert [problem.split(":")[0] for problem in error.problems] == [
        key.replace(".levels.0", ".levels[0]")]
    assert "is longer than 200 characters" in error.problems[0]


@pytest.mark.parametrize("char", ["\x00", "\ud800", "\udfff"])
@pytest.mark.parametrize("key", ["name", "labels.colon", "placeholders.blank", "template_view.empty_choice"])
def test_nul_and_a_lone_surrogate_are_refused_in_free_text(key: str, char: str) -> None:
    error = refused(_with_text(key, f"a{char}b"))
    assert any("NUL or a lone surrogate" in problem for problem in error.problems)
    # The operator's own style is not held to it.
    assert load_style(_with_text("labels.colon", f"a{char}b"))


def test_a_long_text_does_not_add_template_problems() -> None:
    error = refused({"references": {"format": "A" * 60000 + "{designation}"}})
    assert error.problems == ["references.format: is longer than 200 characters"]


def test_a_reference_format_cannot_amplify_the_output() -> None:
    doc = FRONT + "\n# One {#one}\n\n" + "x {{ref: one}} " * 300 + "\n"
    with pytest.raises(StyleError, match="references.format"):
        render(doc, style={"references": {"format": "A" * 60000 + "{designation}"}}, untrusted=True, document_root=".")
    page = render(doc, style={"references": {"format": "A" * 100 + "{designation}"}}, untrusted=True,
                  document_root=".").output
    assert len(page) < 300 * 400 + 20000


# -- A bounded message ------------------------------------------------------------------

def test_a_big_style_with_many_problems_gives_a_short_message() -> None:
    text = "version: 1\n" + "".join(f"k{index}: 1\n" for index in range(5000))
    assert len(text) < MAX_UNTRUSTED_STYLE_BYTES
    error = refused(text)
    assert len(error.problems) == 21 and error.problems[-1] == "and 4980 more"
    assert len(str(error)) < 8000


# -- print-style ------------------------------------------------------------------------

def test_cli_print_style_untrusted_checks_the_style(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "house.yaml"
    path.write_text("version: 1\nlocale: cs-CZ\n", encoding="utf-8")
    assert main(["--print-style", "--untrusted", "--style", str(path)]) == 2
    assert "never a file" in capsys.readouterr().err
    assert main(["--print-style", "--untrusted", "--set", "typography.font_size=calc(1em)"]) == 2
    assert "typography.font_size" in capsys.readouterr().err
    assert main(["--print-style", "--untrusted", "--style", "continental"]) == 0  # no --root needed
    assert "scheme" in capsys.readouterr().out
    # Without --untrusted the file is the operator's own.
    assert main(["--print-style", "--style", str(path)]) == 0


def test_cli_help_says_what_the_style_may_be(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "built-in name or YAML text" in text and "no file path" in text
