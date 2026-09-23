from __future__ import annotations

from pathlib import Path

import pytest

from legaldown_render import Style, StyleError, builtin_styles, dump_style, load_style


def test_builtin_styles_load() -> None:
    assert {"default", "continental", "outline"} <= set(builtin_styles())
    for name in builtin_styles():
        assert load_style(name).name == name


def test_default_is_the_dataclass_defaults() -> None:
    assert load_style() == Style()


def test_dotted_overrides_merge_field_by_field() -> None:
    style = load_style("default", overrides={"headings.1.align": "center", "numbering.scheme": "mixed"})
    assert style.headings[1].align == "center"
    assert style.headings[1].transform == "uppercase"  # the rest of level 1 keeps its default
    assert style.numbering.scheme == "mixed"


def test_every_problem_is_reported_with_its_path() -> None:
    with pytest.raises(StyleError) as caught:
        load_style(overrides={"numbering.scheme": "roman", "numbring": 1, "enumeration.enabled": "yes",
                              "headings.9.size": "2em"})
    problems = "\n".join(caught.value.problems)
    assert "numbering.scheme: must be one of" in problems
    assert "numbring: unknown key (did you mean 'numbering'?)" in problems
    assert "enumeration.enabled: must be true or false" in problems
    assert "headings.9: heading levels are 1 to 6" in problems


def test_file_extends_a_builtin(tmp_path: Path) -> None:
    path = tmp_path / "house.yaml"
    path.write_text("version: 1\nextends: continental\nlocale: cs-CZ\ndefinitions: {style: small-caps}\n")
    style = load_style(path)
    assert style.name == "house"
    assert style.paragraphs.numbered is True  # from continental
    assert style.locale == "cs-CZ"
    assert style.definitions.style == "small-caps"


def test_relative_extends_and_cycles(tmp_path: Path) -> None:
    (tmp_path / "base.yaml").write_text("version: 1\nnumbering: {scheme: none}\n")
    (tmp_path / "child.yaml").write_text("version: 1\nextends: base.yaml\n")
    assert load_style(tmp_path / "child.yaml").numbering.scheme == "none"
    (tmp_path / "a.yaml").write_text("version: 1\nextends: b.yaml\n")
    (tmp_path / "b.yaml").write_text("version: 1\nextends: a.yaml\n")
    with pytest.raises(StyleError, match="circular"):
        load_style(tmp_path / "a.yaml")


def test_unknown_builtin_and_bad_version(tmp_path: Path) -> None:
    with pytest.raises(StyleError, match="no built-in style named 'fancy'"):
        load_style("fancy")
    path = tmp_path / "future.yaml"
    path.write_text("version: 2\n")
    with pytest.raises(StyleError, match="style format 1"):
        load_style(path)


def test_dump_round_trips(tmp_path: Path) -> None:
    style = load_style("continental", overrides={"locale": "de-DE"})
    path = tmp_path / "dumped.yaml"
    path.write_text(dump_style(style))
    assert load_style(path) == style


def test_command_line_values_are_typed_by_their_setting() -> None:
    from legaldown_render.style import parse_override

    assert parse_override("placeholders.blank", "[__]") == "[__]"
    assert parse_override("references.format", "{designation}") == "{designation}"
    assert parse_override("template_view.choice_separator", " | ") == " | "
    assert parse_override("enumeration.enabled", "false") is False
    assert parse_override("locale", "null") is None
    assert parse_override("headings.1.size", "2em") == "2em"
    assert parse_override("numbering.levels", "[{counter: decimal}]") == [{"counter": "decimal"}]


def test_overrides_merge_in_order_and_never_modify_the_callers_values() -> None:
    nested = {"levels": []}
    for overrides in ({"numbering.scheme": "mixed", "numbering": nested},
                      {"numbering": nested, "numbering.scheme": "mixed"}):
        assert load_style(overrides=overrides).numbering.scheme == "mixed"
    assert nested == {"levels": []}


def test_yaml_quoted_text_values_are_unquoted() -> None:
    from legaldown_render.style import parse_override

    assert parse_override("numbering.scheme", '"none"') == "none"
    assert parse_override("references.format", '"§ {designation}"') == "§ {designation}"
    assert parse_override("template_view.choice_separator", "' | '") == " | "
    assert load_style(overrides={"numbering.scheme": parse_override("numbering.scheme", "'none'")}).numbering.scheme == "none"


def test_only_a_single_quoted_string_is_unquoted() -> None:
    from legaldown_render.style import parse_override

    assert parse_override("references.format", """'"{designation}"'""") == '"{designation}"'
    for literal in ('"Section" {designation} "x"', '"C:\\docs"', "'it's'"):
        assert parse_override("references.format", literal) == literal
