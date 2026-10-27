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
    (tmp_path / "dir" / "a.yaml").write_text("version: 1\nextends: ../dir/a.yaml\n")
    with pytest.raises(StyleError, match="circular"):
        load_style(tmp_path / "dir" / "a.yaml")


def test_a_cycle_through_two_spellings_of_one_file_is_found(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "a.yaml").write_text("version: 1\nextends: ../x/b.yaml\n")
    (tmp_path / "x" / "b.yaml").write_text("version: 1\nextends: ./a.yaml\n")
    with pytest.raises(StyleError, match="circular"):
        load_style(tmp_path / "x" / "../x/a.yaml")


def test_a_cycle_through_a_symlink_is_found(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("version: 1\nextends: link.yaml\n")
    try:
        (tmp_path / "link.yaml").symlink_to(tmp_path / "a.yaml")
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(StyleError, match="circular"):
        load_style(tmp_path / "a.yaml")


def test_the_same_base_used_twice_is_not_a_cycle(tmp_path: Path) -> None:
    (tmp_path / "base.yaml").write_text("version: 1\nnumbering: {scheme: none}\n")
    (tmp_path / "mid.yaml").write_text("version: 1\nextends: base.yaml\n")
    (tmp_path / "top.yaml").write_text("version: 1\nextends: mid.yaml\n")
    assert load_style(tmp_path / "top.yaml").numbering.scheme == "none"


# -- A dump is self-contained ----------------------------------------------------

def test_a_dump_does_not_extend_anything(tmp_path: Path) -> None:
    (tmp_path / "base.yaml").write_text("version: 1\nnumbering: {scheme: none}\n")
    (tmp_path / "child.yaml").write_text("version: 1\nextends: base.yaml\nlocale: cs-CZ\n")
    style = load_style(tmp_path / "child.yaml")
    text = dump_style(style)
    assert "extends" not in text
    elsewhere = tmp_path / "elsewhere" / "deep"
    elsewhere.mkdir(parents=True)
    dumped = elsewhere / "dumped.yaml"
    dumped.write_text(text)
    reloaded = load_style(dumped)
    assert reloaded.numbering.scheme == "none"
    assert reloaded.locale == "cs-CZ"
    assert reloaded.name == style.name


@pytest.mark.parametrize("name", builtin_styles())
def test_a_dumped_builtin_reloads_equal(name: str, tmp_path: Path) -> None:
    style = load_style(name)
    path = tmp_path / "dump.yaml"
    path.write_text(dump_style(style))
    assert "extends" not in path.read_text()
    # The dump is flattened: it is the same style, minus the record of what it extended.
    assert load_style(path) == replace(style, extends=None)


def test_print_style_of_a_relative_extends_reloads_from_anywhere(tmp_path: Path) -> None:
    (tmp_path / "base.yaml").write_text("version: 1\nplaceholders: {blank: '[__]'}\n")
    (tmp_path / "child.yaml").write_text("version: 1\nextends: base.yaml\n")
    done = run_cli_text("--print-style", "--style", str(tmp_path / "child.yaml"))
    assert done.returncode == 0, done.stderr
    assert "extends" not in done.stdout
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    (elsewhere / "dump.yaml").write_text(done.stdout)
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
