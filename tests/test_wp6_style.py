"""Style validation: an empty blank falls back to ``[TBD: id]`` (R6), ``extends``
cycles are found on resolved paths (S7), ``--print-style`` is self-contained
(S8), ``version`` and ``extends`` cannot be overridden (S9), a YAML error in a
``--set`` value is reported (S10, loader side), and the ``{field}``
placeholders of every text template are validated (S11)."""
from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from legaldown_render import RenderOptions, StyleError, builtin_styles, dump_style, load_style, render
from legaldown_render.style import parse_override

SRC = Path(__file__).resolve().parents[1] / "src"
DOC = "---\nlegaldown: '0.2'\ntitle: T\n---\n\n# One {#one}\n\nAmount {{placeholder: fee}} and {{ref: one}}.\n"


def _cli(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONUTF8="1")
    return subprocess.run([sys.executable, "-m", "legaldown_render.cli", *args], input=stdin,
                          capture_output=True, text=True, env=env, check=False)


# --- R6: an empty blank --------------------------------------------------------------------------

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


# --- S7: extends cycles ----------------------------------------------------------------------------

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


# --- S8: a dump is self-contained ----------------------------------------------------------------------

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
    done = _cli("--print-style", "--style", str(tmp_path / "child.yaml"))
    assert done.returncode == 0, done.stderr
    assert "extends" not in done.stdout
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    (elsewhere / "dump.yaml").write_text(done.stdout)
    assert load_style(elsewhere / "dump.yaml").placeholders.blank == "[__]"


# --- S9: version and extends are not overridable ------------------------------------------------------------

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
    done = _cli(*args, "--set", setting, stdin=DOC)
    assert done.returncode == 2, done.stdout
    assert setting.split("=")[0] in done.stderr
    assert "cannot be overridden" in done.stderr
    assert "Traceback" not in done.stderr


# --- S10: a YAML error in a --set value -------------------------------------------------------------------

def test_a_yaml_error_in_an_override_names_the_key() -> None:
    with pytest.raises(StyleError) as caught:
        parse_override("numbering.levels", "[{counter: decimal")
    message = str(caught.value)
    assert "numbering.levels" in message
    assert "YAML" in message
    assert "flow" in message or "expected" in message or "while" in message  # the YAML problem itself


def test_a_yaml_error_in_an_override_reaches_stderr() -> None:
    done = _cli("--print-style", "--set", "numbering.levels=[{counter: decimal")
    assert done.returncode == 2
    assert "numbering.levels" in done.stderr
    assert "YAML" in done.stderr
    assert "Traceback" not in done.stderr
    assert "must be a list" not in done.stderr  # not the misleading type error


def test_a_yaml_error_in_a_render_run_reaches_stderr() -> None:
    done = _cli("-", "-q", "--set", "enumeration.levels=[{a", stdin=DOC)
    assert done.returncode != 0
    assert "enumeration.levels" in done.stderr
    assert "YAML" in done.stderr
    assert "Traceback" not in done.stderr


def test_valid_yaml_overrides_still_parse() -> None:
    assert parse_override("title_block.version", "true") is True
    assert parse_override("contents.depth", "3") == 3
    assert parse_override("numbering.levels", "[{counter: decimal}]") == [{"counter": "decimal"}]


# --- S11: template fields ------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", builtin_styles())
def test_every_builtin_style_passes(name: str) -> None:
    load_style(name)


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
    ("labels.drafting_note", "{note}"),
    ("labels.colon", "{n}"),
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
    problems = _problems({setting: value})
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
