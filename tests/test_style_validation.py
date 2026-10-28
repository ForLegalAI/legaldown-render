"""Style validation and the style loader's edge cases: an empty placeholder
blank falls back to ``[TBD: id]`` (§13.5 rule 6), ``extends`` cycles are found
on resolved paths, ``--print-style`` is self-contained, ``version`` and
``extends`` cannot be overridden, a YAML error in a ``--set`` value is
reported, and the ``{field}`` placeholders of every text template are checked
where the setting is used (and for typos everywhere). Loading, layering and
dotted overrides are in test_style.py."""
from __future__ import annotations

import dataclasses
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import FRONT, run_cli_text

from legaldown_render import RenderOptions, StyleError, builtin_styles, dump_style, load_style, render
from legaldown_render.style import parse_override

DOC = "---\nlegaldown: '0.2'\ntitle: T\n---\n\n# One {#one}\n\nAmount {{placeholder: fee}} and {{ref: one}}.\n"


# -- An empty or invisible blank (§13.5) -----------------------------------------

@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
@pytest.mark.parametrize("fmt", ["html", "text"])
def test_an_empty_blank_falls_back_to_tbd(blank: str, fmt: str) -> None:
    """§13.5 rule 6: no visual blank means ``[TBD: id]``."""
    result = render(DOC, RenderOptions(format=fmt, overrides={"placeholders.blank": blank}))
    assert "[TBD: fee]" in result.output


def test_a_visible_blank_is_kept() -> None:
    result = render(DOC, RenderOptions(format="text", overrides={"placeholders.blank": "[__]"}))
    assert "[__]" in result.output
    assert "TBD" not in result.output


@pytest.mark.parametrize("blank", ["​", "﻿", " ​‍﻿ ", " ", "‮⁠"])
@pytest.mark.parametrize("fmt", ["html", "text"])
def test_a_blank_of_invisible_characters_falls_back_to_tbd(blank: str, fmt: str) -> None:
    source = FRONT.replace("title: T", "title: T\nplaceholders:\n  - id: fee\n    type: text") + "# A\n\nFee {{placeholder: fee}}.\n"
    result = render(source, format=fmt, overrides={"placeholders.blank": blank})
    assert "[TBD: fee]" in result.output


def test_a_blank_with_a_visible_character_is_kept() -> None:
    source = FRONT.replace("title: T", "title: T\nplaceholders:\n  - id: fee\n    type: text") + "# A\n\nFee {{placeholder: fee}}.\n"
    assert "Fee ​__​." in render(source, format="text", overrides={"placeholders.blank": "​__​"}).output


# -- extends cycles --------------------------------------------------------------

def test_a_cycle_through_a_parent_directory_is_found(tmp_path: Path) -> None:
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "a.yaml").write_text("version: 1\nextends: ../dir/a.yaml\n", encoding="utf-8")
    with pytest.raises(StyleError, match="circular"):
        load_style(tmp_path / "dir" / "a.yaml")


def test_a_cycle_through_two_spellings_of_one_file_is_found(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "a.yaml").write_text("version: 1\nextends: ../x/b.yaml\n", encoding="utf-8")
    (tmp_path / "x" / "b.yaml").write_text("version: 1\nextends: ./a.yaml\n", encoding="utf-8")
    with pytest.raises(StyleError, match="circular"):
        load_style(tmp_path / "x" / "../x/a.yaml")


def test_a_cycle_through_a_symlink_is_found(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("version: 1\nextends: link.yaml\n", encoding="utf-8")
    try:
        (tmp_path / "link.yaml").symlink_to(tmp_path / "a.yaml")
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(StyleError, match="circular"):
        load_style(tmp_path / "a.yaml")


def test_the_same_base_used_twice_is_not_a_cycle(tmp_path: Path) -> None:
    (tmp_path / "base.yaml").write_text("version: 1\nnumbering: {scheme: none}\n", encoding="utf-8")
    (tmp_path / "mid.yaml").write_text("version: 1\nextends: base.yaml\n", encoding="utf-8")
    (tmp_path / "top.yaml").write_text("version: 1\nextends: mid.yaml\n", encoding="utf-8")
    assert load_style(tmp_path / "top.yaml").numbering.scheme == "none"


# -- A dump is self-contained ----------------------------------------------------

def test_a_dump_does_not_extend_anything(tmp_path: Path) -> None:
    (tmp_path / "base.yaml").write_text("version: 1\nnumbering: {scheme: none}\n", encoding="utf-8")
    (tmp_path / "child.yaml").write_text("version: 1\nextends: base.yaml\nlocale: cs-CZ\n", encoding="utf-8")
    style = load_style(tmp_path / "child.yaml")
    text = dump_style(style)
    assert "extends" not in text
    elsewhere = tmp_path / "elsewhere" / "deep"
    elsewhere.mkdir(parents=True)
    dumped = elsewhere / "dumped.yaml"
    dumped.write_text(text, encoding="utf-8")
    reloaded = load_style(dumped)
    assert reloaded.numbering.scheme == "none"
    assert reloaded.locale == "cs-CZ"
    assert reloaded.name == style.name


@pytest.mark.parametrize("name", builtin_styles())
def test_a_dumped_builtin_reloads_equal(name: str, tmp_path: Path) -> None:
    style = load_style(name)
    path = tmp_path / "dump.yaml"
    path.write_text(dump_style(style), encoding="utf-8")
    assert "extends" not in path.read_text(encoding="utf-8")
    # The dump is flattened: it is the same style, minus the record of what it extended.
    assert load_style(path) == replace(style, extends=None)


def test_print_style_of_a_relative_extends_reloads_from_anywhere(tmp_path: Path) -> None:
    (tmp_path / "base.yaml").write_text("version: 1\nplaceholders: {blank: '[__]'}\n", encoding="utf-8")
    (tmp_path / "child.yaml").write_text("version: 1\nextends: base.yaml\n", encoding="utf-8")
    done = run_cli_text("--print-style", "--style", str(tmp_path / "child.yaml"))
    assert done.returncode == 0, done.stderr
    assert "extends" not in done.stdout
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    (elsewhere / "dump.yaml").write_text(done.stdout, encoding="utf-8")
    assert load_style(elsewhere / "dump.yaml").placeholders.blank == "[__]"


# -- version and extends are not overridable -------------------------------------

@pytest.mark.parametrize("key", ["version", "extends"])
def test_version_and_extends_cannot_be_overridden(key: str) -> None:
    with pytest.raises(StyleError, match=key):
        load_style("default", overrides={key: "2" if key == "version" else "continental"})


def test_a_nested_override_of_version_is_refused_too() -> None:
    with pytest.raises(StyleError, match="version"):
        load_style("default", overrides={"version": 1})


@pytest.mark.parametrize("setting", ["version=2", "extends=continental"])
@pytest.mark.parametrize("print_style", [True, False])
def test_the_cli_refuses_them(setting: str, print_style: bool) -> None:
    args = ["--print-style"] if print_style else ["-", "-q"]
    done = run_cli_text(*args, "--set", setting, stdin=DOC)
    assert done.returncode == 2, done.stdout
    assert setting.split("=")[0] in done.stderr
    assert "cannot be overridden" in done.stderr
    assert "Traceback" not in done.stderr


# -- A YAML error in a --set value -----------------------------------------------

def test_a_yaml_error_in_an_override_names_the_key() -> None:
    with pytest.raises(StyleError) as caught:
        parse_override("numbering.levels", "[{counter: decimal")
    message = str(caught.value)
    assert "numbering.levels" in message
    assert "YAML" in message
    assert "flow" in message or "expected" in message or "while" in message  # the YAML problem itself


def test_a_yaml_error_in_an_override_reaches_stderr() -> None:
    done = run_cli_text("--print-style", "--set", "numbering.levels=[{counter: decimal")
    assert done.returncode == 2
    assert "numbering.levels" in done.stderr
    assert "YAML" in done.stderr
    assert "Traceback" not in done.stderr
    assert "must be a list" not in done.stderr  # not the misleading type error


def test_a_yaml_error_in_a_render_run_reaches_stderr() -> None:
    done = run_cli_text("-", "-q", "--set", "enumeration.levels=[{a", stdin=DOC)
    assert done.returncode != 0
    assert "enumeration.levels" in done.stderr
    assert "YAML" in done.stderr
    assert "Traceback" not in done.stderr


def test_valid_yaml_overrides_still_parse() -> None:
    assert parse_override("title_block.version", "true") is True
    assert parse_override("contents.depth", "3") == 3


# -- Template fields -------------------------------------------------------------

@pytest.mark.parametrize("language", ["en", "cs", "de", "fr", "pl", "sk"])
def test_every_builtin_label_passes(language: str) -> None:
    from legaldown_render.style import BUILTIN_LABELS
    labels = BUILTIN_LABELS[language]
    overrides = {f"labels.{item.name}": getattr(labels, item.name) for item in dataclasses.fields(labels)}
    assert load_style("default", overrides=overrides).labels == labels


def _problems(overrides: dict[str, object]) -> list[str]:
    with pytest.raises(StyleError) as caught:
        load_style("default", overrides=overrides)
    return caught.value.problems


def test_labels_condition_must_show_the_condition() -> None:
    """§15.8: a conditional unit's condition MUST be shown."""
    problems = _problems({"labels.condition": "Conditional"})
    assert any(p.startswith("labels.condition:") and "{condition}" in p for p in problems)


def test_references_format_must_show_the_designation() -> None:
    problems = _problems({"references.format": "nope"})
    assert any(p.startswith("references.format:") and "{designation}" in p for p in problems)


def test_a_typo_in_a_field_is_named() -> None:
    problems = _problems({"references.format": "{desgination}"})
    assert any(p.startswith("references.format:") and "{desgination}" in p and "{designation}" in p for p in problems)


@pytest.mark.parametrize(("setting", "value"), [
    ("labels.condition", "Only if {condition} {n}"),
    ("labels.attachment_file", "{condition}"),
    ("references.format", "{n}{designation}"),
    ("numbering.levels", [{"label": "{n}", "ref": "{path}"}]),
    ("numbering.levels", [{"label": "{section}", "ref": "{n}"}]),
    ("numbering.levels", [{"label": "{n}", "ref": ".x"}]),
    ("enumeration.levels", [{"label": "{wrong}", "ref": "{n}"}]),
    ("enumeration.levels", [{"label": "({n})", "ref": "{path}{n}"}]),
    ("paragraphs.format", {"label": "{n}", "ref": "{foo}"}),
    ("paragraphs.format", {"label": "{n}", "ref": "{section}"}),
])
def test_invalid_templates_are_refused(setting: str, value: object) -> None:
    # A paragraph's ref is read only where paragraphs are numbered.
    problems = _problems({setting: value, "paragraphs.numbered": True})
    assert any(p.startswith(setting) for p in problems), problems


def test_the_problem_names_the_level() -> None:
    problems = _problems({"numbering.levels": [{"ref": "{n}"}, {"ref": "x"}]})
    assert any(p.startswith("numbering.levels[1].ref:") for p in problems)


@pytest.mark.parametrize(("setting", "value"), [
    ("labels.condition", "{condition}"),
    ("labels.condition", "Only if: {condition} (see {condition})"),
    ("labels.condition", "[{condition}] {x y} { } {} {1} {{ }} {-n}"),
    ("labels.attachment_file", "no field at all"),
    ("labels.attachment_file", "{file}"),
    ("labels.drafting_note", "Note {1} {} {a b}"),
    ("labels.colon", ""),
    ("references.format", "Section {designation}"),
    ("references.format", "{designation} {a.b} {0}"),
    ("numbering.levels", [{"label": "{path}.", "ref": "{n}"}]),
    ("numbering.levels", [{"label": "Article {n} ({path})", "ref": ".{n}"}]),
    ("enumeration.levels", [{"label": "({n})", "ref": "{section}({n})"}]),
    ("enumeration.levels", [{"label": "{section}.{path}.{n}", "ref": "({n})"}]),
    ("paragraphs.format", {"label": "{section}.{n}", "ref": ".{n}"}),
    ("paragraphs.format", {"label": "{path} {n} {section}", "ref": "{section}.{n}"}),
])
def test_valid_templates_are_accepted(setting: str, value: object) -> None:
    load_style("default", overrides={setting: value})


def test_an_empty_condition_label_keeps_its_render_time_fallback() -> None:
    """The resolver shows the bare condition for an empty ``labels.condition``
    (§15.8 is met), so the style is not refused."""
    style = load_style("default", overrides={"labels.condition": ""})
    assert style.labels.condition == ""
    doc = "---\nlegaldown: '0.2'\ntitle: T\n---\n\n# One {#one when=forum:courts}\n\nText.\n"
    assert "forum:courts" in render(doc, RenderOptions(format="text", overrides={"labels.condition": ""})).output


def test_an_empty_template_without_a_fallback_still_lacks_its_field() -> None:
    for setting in ("references.format", "numbering.levels", "paragraphs.format"):
        value: object = {"label": "{n}", "ref": ""} if setting == "paragraphs.format" else (
            [{"ref": ""}] if setting == "numbering.levels" else "")
        assert any(p.startswith(setting) for p in _problems({setting: value, "paragraphs.numbered": True}))


def test_unset_labels_are_not_checked() -> None:
    assert load_style("default", overrides={"labels.condition": None}).labels.condition is None


def test_braces_that_are_not_identifiers_stay_literal() -> None:
    result = render(DOC, RenderOptions(format="text", overrides={"references.format": "{{{designation}}} {0} {}"}))
    assert "{{1}} {0} {}" in result.output


def test_a_style_object_is_checked_too() -> None:
    from dataclasses import replace

    from legaldown_render.style import References
    style = replace(load_style("default"), references=References(format="oops"))
    with pytest.raises(StyleError, match="references.format"):
        load_style(style)


def test_all_template_problems_are_reported_together() -> None:
    problems = _problems({"references.format": "x", "labels.condition": "y", "placeholders.blank": ["x"]})
    assert any(p.startswith("references.format") for p in problems)
    assert any(p.startswith("labels.condition") for p in problems)
    assert any(p.startswith("placeholders.blank") for p in problems)


# -- Templates are validated where their setting is used -------------------------

NO_N = [{"label": "x", "ref": "x"}]


@pytest.mark.parametrize(("overrides", "key"), [
    ({"enumeration.levels": NO_N}, "enumeration.levels[0].ref"),
    ({"enumeration.levels": NO_N, "enumeration.enabled": False, "enumeration.ordered": "enumerate"},
     "enumeration.levels[0].ref"),
    ({"numbering.levels": NO_N}, "numbering.levels[0].ref"),
    ({"paragraphs.numbered": True, "paragraphs.format": {"label": "x", "ref": "x"}}, "paragraphs.format.ref"),
])
def test_a_ref_that_is_in_use_must_have_its_counter(overrides: dict[str, object], key: str) -> None:
    assert any(p.startswith(key) and "{n}" in p for p in _problems(overrides))


@pytest.mark.parametrize("overrides", [
    {"enumeration.levels": NO_N, "enumeration.enabled": False},
    {"numbering.levels": NO_N, "numbering.scheme": "none"},
    {"paragraphs.format": {"label": "x", "ref": "x"}},
    {"paragraphs.format": {"label": "x", "ref": ""}},
])
def test_a_ref_that_is_not_in_use_may_leave_out_its_counter(overrides: dict[str, object]) -> None:
    load_style("default", overrides=overrides)


def test_a_typo_is_flagged_even_where_the_setting_is_not_used() -> None:
    problems = _problems({"numbering.scheme": "none", "numbering.levels": [{"label": "{n}", "ref": "{m}"}]})
    assert any(p.startswith("numbering.levels[0].ref") and "{m}" in p and "unknown field" in p for p in problems)


def test_a_plain_label_may_contain_braced_words() -> None:
    style = load_style("default", overrides={"labels.drafting_note": "Note {see below}", "labels.colon": "{x}:"})
    assert style.labels.drafting_note == "Note {see below}"
    assert style.labels.colon == "{x}:"


def test_the_labels_that_substitute_still_check_their_fields() -> None:
    assert _problems({"labels.condition": "{condtion}"})
    assert _problems({"labels.attachment_file": "{fil}"})


# -- Values that reach the stylesheet --------------------------------------------

#: Every setting written into the stylesheet, as a dotted key.
CSS_SETTINGS = ["typography.font_family", "typography.font_size", "typography.line_height", "typography.color",
                "typography.link_color", "headings.1.size", "headings.1.weight", "page.size", "page.margin",
                "html.max_width"]
CSS_TOKENS = ["/*", "*/", "</", "\\", "\n", "\r", "\t", "\x07", "\x7f", " "]


def test_the_css_settings_listed_here_are_every_one_the_model_declares() -> None:
    """A setting added to the model with a CSS kind must join the tables here."""
    from legaldown_render.style import model

    declared = [f"{section}.{item.name}"
                for section, cls in (("typography", model.Typography), ("headings.1", model.HeadingStyle),
                                     ("page", model.Page), ("html", model.Html))
                for item in dataclasses.fields(cls) if "css" in item.metadata]
    assert sorted(declared) == sorted([*CSS_SETTINGS, "html.extra_css"])


@pytest.mark.parametrize("trusted", [True, False])
@pytest.mark.parametrize("token", CSS_TOKENS)
@pytest.mark.parametrize("key", CSS_SETTINGS)
def test_a_css_value_with_a_forbidden_token_is_refused_and_named(key: str, token: str, trusted: bool) -> None:
    """Rejected, not stripped: removing a token can form another (``/*`` out of ``//**``)."""
    with pytest.raises(StyleError) as caught:
        load_style("default", overrides={key: f"1em{token}x"}, trusted=trusted)
    assert any(problem.startswith(f"{key}:") for problem in caught.value.problems)


@pytest.mark.parametrize("trusted", [True, False])
@pytest.mark.parametrize("extra", ["</style><script>alert(1)</script><style>", "a { } </STYLE >", "</sTyLe"])
def test_extra_css_cannot_close_the_style_element(extra: str, trusted: bool) -> None:
    with pytest.raises(StyleError, match=r"html\.extra_css: .*</style"):
        load_style("default", overrides={"html.extra_css": extra}, trusted=trusted)
    with pytest.raises(StyleError, match="extra_css"):
        render(DOC, overrides={"html.extra_css": extra}, untrusted=not trusted, document_root=".")


def test_extra_css_is_raw_css_otherwise() -> None:
    css = "a > b { color: red } /* a comment */ p::before { content: '\\201c'; }\n.x { margin: 0 }"
    result = render(DOC, overrides={"html.extra_css": css})
    assert css in result.output


@pytest.mark.parametrize(("key", "value"), [
    ("html.max_width", "calc(100% - 2rem)"),
    ("html.max_width", "min(46rem, 100vw)"),
    ("typography.color", "var(--ink)"),
    ("typography.font_size", "clamp(10pt, 2vw, 14pt)"),
    ("typography.line_height", "calc(1em + 4px)"),
    ("page.margin", "max(20mm, 5%)"),
])
def test_a_trusted_style_may_use_css_functions_the_strict_grammar_refuses(key: str, value: str) -> None:
    style = load_style("default", overrides={key: value})
    assert value in render(DOC, style=style).output
    with pytest.raises(StyleError, match=key):
        load_style("default", overrides={key: value}, trusted=False)


def test_a_style_object_is_checked_for_css() -> None:
    from legaldown_render.style import Typography

    style = replace(load_style("default"), typography=Typography(font_size="12pt /* "))
    with pytest.raises(StyleError, match="typography.font_size"):
        load_style(style)
    with pytest.raises(StyleError, match="typography.font_size"):
        render(DOC, style=style)


def test_a_settings_object_handed_in_as_an_override_is_checked_too() -> None:
    from legaldown_render.style import Typography

    with pytest.raises(StyleError, match="typography.font_size"):
        load_style("default", overrides={"typography": Typography(font_size="1em\n}")})


def test_builtin_styles_and_documented_values_still_load() -> None:
    for name in builtin_styles():
        load_style(name)
    load_style("default", overrides={"typography": {"font_family": "Georgia, serif", "justify": True}})


# -- Numbers, dates and timestamps in YAML (always) --------------------------------

@pytest.mark.parametrize("trusted", [True, False])
@pytest.mark.parametrize("text", [
    "a: !!int SECRETx", "a: !!float SECRETx", "a: !!bool SECRETx", "a: !!timestamp SECRETx",
    "a: 2001-13-45", "a: 2001-01-01 99:99:99", "a: 0x" + "f" * 5000, "a: " + "9" * 5000, "a: 1_" + "0" * 40,
    "a: 1" + "0" * 32, "? " + "1" * 5000 + "\n: 1", "a: 1." + "0" * 40,
])
def test_a_number_or_date_that_is_not_one_is_a_style_error_not_a_crash(text: str, trusted: bool) -> None:
    """PyYAML's constructors let ValueError, KeyError and OverflowError out, with the text in the message."""
    with pytest.raises(StyleError) as caught:
        load_style(f"version: 1\n{text}\n", trusted=trusted)
    assert "SECRETx" not in str(caught.value) or trusted
    assert caught.value.problems[0].startswith("not valid YAML")


def test_a_number_up_to_the_limit_and_the_usual_dates_still_load() -> None:
    from legaldown_render.style.loader import MAX_NUMBER_CHARS

    text = "typography:\n  line_height: 1.5\nheadings:\n  1: {weight: 700}\nx: 2001-01-01\n"
    with pytest.raises(StyleError, match="x: unknown key"):  # read as a date, then refused as a key
        load_style(text)
    assert load_style("typography: {line_height: " + "1" * MAX_NUMBER_CHARS + "}\n").typography.line_height
    assert load_style("headings: {1: {weight: 0x2BC}, 2: {weight: 7_0_0}}\n").headings[1].weight == "700"


@pytest.mark.parametrize("trusted", [True, False])
def test_an_enormous_number_in_a_mapping_is_a_style_error(trusted: bool) -> None:
    big = 10 ** 5000
    for style in ({"headings": {big: {"size": "1em"}}}, {"typography": {"font_size": big}}, {"a": {big: 1}}):
        with pytest.raises(StyleError):
            load_style(style, trusted=trusted)
    with pytest.raises(StyleError):
        load_style("default", overrides={"typography.font_size": big}, trusted=trusted)
    with pytest.raises(StyleError):
        load_style("default", overrides={big: 1}, trusted=trusted)  # type: ignore[dict-item]
    with pytest.raises(StyleError):
        load_style("default", overrides={"contents": {"depth": big}}, trusted=trusted)


def test_an_infinite_key_is_refused_not_a_crash() -> None:
    for trusted in (True, False):
        with pytest.raises(StyleError, match="key must be a number"):
            load_style("headings: {.inf: {size: 1em}}\n", trusted=trusted)


# -- values.date (§13.5) -----------------------------------------------------------

@pytest.mark.parametrize("pattern", ["GGGGGGG", "QQQQQQ", "ZZZZZZZZ", "<script>", "xxxxxx", "bbbbbb"])
def test_a_date_pattern_babel_refuses_is_a_style_error_naming_the_setting(pattern: str) -> None:
    """Otherwise every render of a document with a date raises, quoting the pattern."""
    with pytest.raises(StyleError, match="values.date") as caught:
        load_style("default", overrides={"values.date": pattern})
    assert pattern not in str(caught.value)
    with pytest.raises(StyleError, match="values.date"):
        render(DOC + "\nOn {{date: 2026-07-01}}.\n", overrides={"values.date": pattern})


@pytest.mark.parametrize("pattern", ["short", "medium", "long", "full", "dd.MM.yyyy", "d MMMM y", "EEEE, d. MMMM y",
                                     "yyyy-MM-dd'T'", "y", "GGGGG"])
def test_a_valid_date_format_loads_and_renders(pattern: str) -> None:
    result = render(DOC + "\nOn {{date: 2026-07-01}}.\n", overrides={"values.date": pattern})
    assert 'class="ld-value ld-date"' in result.output


# -- A message lists a bounded number of problems --------------------------------------

def test_a_message_lists_at_most_twenty_problems() -> None:
    from legaldown_render.style.loader import MAX_PROBLEMS

    style = {f"k{index}": 1 for index in range(5000)}
    for trusted in (True, False):
        with pytest.raises(StyleError) as caught:
            load_style(style, trusted=trusted)
        problems = caught.value.problems
        assert len(problems) == MAX_PROBLEMS + 1
        assert problems[-1] == f"and {5000 - MAX_PROBLEMS} more"
        assert len(str(caught.value)) < 10_000


def test_exactly_twenty_problems_are_all_listed() -> None:
    with pytest.raises(StyleError) as caught:
        load_style({f"k{index}": 1 for index in range(20)})
    assert len(caught.value.problems) == 20
    assert not caught.value.problems[-1].startswith("and ")


# -- Characters no output can write ------------------------------------------------------

@pytest.mark.parametrize("trusted", [True, False])
@pytest.mark.parametrize("key", CSS_SETTINGS)
@pytest.mark.parametrize("char", ["\x00", "\ud800", "\udfff"])
def test_nul_and_a_lone_surrogate_are_refused_in_every_css_setting(key: str, char: str, trusted: bool) -> None:
    with pytest.raises(StyleError) as caught:
        load_style("default", overrides={key: f"1em{char}"}, trusted=trusted)
    assert any(problem.startswith(f"{key}:") for problem in caught.value.problems)


def test_nul_and_a_lone_surrogate_are_refused_in_raw_css_too() -> None:
    for char in ("\x00", "\ud800"):
        with pytest.raises(StyleError, match="html.extra_css"):
            load_style("default", overrides={"html.extra_css": f"a {{ color: red }}{char}"})
    assert load_style("default", overrides={"html.extra_css": "a {\n\tcolor: red\n}"})  # other whitespace is CSS
