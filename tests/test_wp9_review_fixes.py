"""Review fixes after wave 1 (WP9): a ``{{ref:}}`` or placeholder inside a
defined term as ``{{term:}}`` shows it, a fraction beyond Python's int/str digit
limit renders, ``-o`` rejects only the extensions of formats the renderer does
not write, in-process ``main()`` leaves the streams alone, and output has LF
line endings."""
from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import DOCUMENTS

from legaldown_render import render
from legaldown_render.cli import main

SRC = Path(__file__).resolve().parents[1] / "src"
FEATURES = str(DOCUMENTS / "features.lgd")
FRONT = "---\ntitle: T\n---\n\n"


def text(body: str, **settings: object) -> str:
    return render(FRONT + body, format="text", **settings).output


# -- 1: {{term:}} shows what the definition shows -------------------------------


def test_a_term_reference_keeps_a_ref_inside_the_term() -> None:
    output = text('# H {#h}\n\n"Fee per {{ref: h}}" {{def: a}}\n\n{{term: a}}\n')
    assert output.count("Fee per 1") == 2


def test_a_term_reference_before_the_heading_keeps_the_ref() -> None:
    output = text('{{term: a}}\n\n# H {#h}\n\n"Fee per {{ref: h}}" {{def: a}}\n')
    assert output.count("Fee per 1") == 2


def test_a_term_reference_before_its_definition_and_its_target() -> None:
    output = text('# First\n\nSee {{term: a}}.\n\n# H {#h}\n\n"Fee per {{ref: h}}" {{def: a}}\n')
    assert "See Fee per 2." in output


def test_a_term_reference_keeps_a_ref_to_a_subsection() -> None:
    body = '# A\n\n"Fee per {{ref: s}}" {{def: a}}\n\n{{term: a}}\n\n## Sub {#s}\n\nx\n'
    output = text(body)
    assert output.count("Fee per 1.1") == 2


def test_a_term_reference_in_a_textual_heading_and_in_the_body() -> None:
    # The none scheme reads a heading as text before every target is known; the
    # term text must not be fixed from that early pass.
    body = '# Fee {{term: a}}\n\n"Fee per {{ref: h}}" {{def: a}}\n\n# H {#h}\n\n{{term: a}}\n'
    output = render(FRONT + body, format="text", overrides={"numbering.scheme": "none"}).output
    assert output.rstrip().endswith("Fee per H")
    assert "[BROKEN REF" not in output


def test_a_term_reference_shows_the_placeholder_as_the_definition_does() -> None:
    body = '# A\n\n"Fee {{placeholder: p}}" {{def: a}} {{term: a}} {{placeholder: p, type=money}}\n'
    result = render(FRONT + body, format="text")
    line = next(row for row in result.output.splitlines() if "INVALID PLACEHOLDER" in row)
    assert line.count("Fee [INVALID PLACEHOLDER: p]") == 2
    assert "[_____]" not in result.output


def test_a_definition_that_defines_a_term_twice_uses_the_first() -> None:
    body = '# H {#h}\n\n"First {{ref: h}}" {{def: t}}\n\n"Second" {{def: t}}\n\n{{term: t}}\n'
    assert text(body).rstrip().endswith("First 1")


def test_a_term_with_a_ref_adds_no_duplicate_diagnostics() -> None:
    # The term holds an image with a remote path: one report however often it is resolved.
    body = '# H {#h}\n\n"Fee ![x](http://e.com/i.png) {{ref: h}}" {{def: a}}\n\n{{term: a}} {{term: a}}\n'
    diagnostics = render(FRONT + body, format="text").diagnostics
    keys = [(d.rule, d.message) for d in diagnostics]
    assert len(keys) == len(set(keys))
    assert sum(d.rule == "path-not-relative" for d in diagnostics) == 1


def test_a_term_that_holds_a_definition_does_not_take_its_anchor() -> None:
    body = '# A\n\nSee {{term: outer}}.\n\n"Outer "Inner" {{def: inner}}" {{def: outer}}\n\n{{term: inner}}\n'
    html = render(FRONT + body, format="html").output
    assert html.count('id="def:inner"') == 1
    assert html.count('id="def:outer"') == 1


@pytest.mark.parametrize("output_format", ["text", "html"])
def test_cycles_through_refs_still_terminate(output_format: str) -> None:
    body = ('# H {#h}\n\n"A {{term: b}} {{ref: h}}" {{def: a}} x.\n\n"B {{term: a}}" {{def: b}} y.\n\n'
            '"C {{term: c}}" {{def: c}}\n\nSee {{term: a}}, {{term: b}}, {{term: c}}.\n')
    out = render(FRONT + body, format=output_format).output
    assert "{{" not in out
    # Which term shows what inside a cycle follows the order they are first needed in.
    assert "A b 1" in out and "C c" in out


# -- 2: a fraction beyond Python's int/str digit limit ---------------------------

LONG = "2" * 4400


def test_a_duration_with_a_4400_digit_fraction_renders() -> None:
    output = render(FRONT + f"# A\n\n{{{{duration: 1.{LONG}, unit=D}}}}", format="text").output
    assert f"1.{LONG} days" in output


def test_a_money_amount_in_name_mode_with_a_4400_digit_fraction_renders() -> None:
    output = render(FRONT + f"# A\n\n{{{{money: 1.{LONG}, currency=EUR}}}}", format="text",
                    overrides={"values.money": "name"}).output
    assert f"1.{LONG} euros" in output


def test_a_4400_digit_fraction_uses_the_other_category_in_czech() -> None:
    output = render(FRONT + f"# A\n\n{{{{duration: 1.{LONG}, unit=Y}}}}", format="text", locale="cs-CZ").output
    assert output.rstrip().endswith(" let")  # "other", not the "many" of "2,5 roku"


# -- 4: only the program reconfigures the streams -------------------------------


def _wrapper(encoding: str, **options: object) -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding, **options)  # type: ignore[arg-type]


def test_in_process_main_leaves_the_streams_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    out, err = _wrapper("cp1252", newline="\r\n"), _wrapper("cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    assert main([FEATURES, "-f", "text", "-q"]) == 0
    assert main(["--list-styles"]) == 0
    assert (sys.stdout.encoding, sys.stdout.errors) == ("cp1252", "strict")
    assert (sys.stderr.encoding, sys.stderr.errors) == ("cp1252", "strict")
    out.flush()
    # Not even the newline translation of the caller's stream changed.
    assert b"\r\n" in out.buffer.getvalue()  # type: ignore[attr-defined]


def test_main_as_the_program_reconfigures_the_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    out, err = _wrapper("cp1252", newline="\r\n"), _wrapper("cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setattr(sys, "argv", ["legaldown-render", "--list-styles"])
    assert main() == 0
    out.flush()
    assert (out.encoding, err.errors) == ("utf-8", "backslashreplace")
    data = out.buffer.getvalue()  # type: ignore[attr-defined]
    assert data and b"\r" not in data and data.endswith(b"\n")


def test_the_console_script_calls_main_without_argv() -> None:
    # The script entry point calls main() with no argument, so it is "the program".
    pyproject = (SRC.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert 'legaldown-render = "legaldown_render.cli:main"' in pyproject


# -- 5: LF line endings everywhere ------------------------------------------------


def _run(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
    return subprocess.run([sys.executable, "-m", "legaldown_render.cli", *args], capture_output=True, env=env,
                          check=False)


@pytest.mark.parametrize("fmt", ["html", "text"])
def test_standard_output_has_lf_line_endings(fmt: str) -> None:
    done = _run([FEATURES, "-f", fmt, "-q"])
    assert done.returncode == 0, done.stderr
    assert b"\n" in done.stdout and b"\r" not in done.stdout


@pytest.mark.parametrize("fmt", ["html", "text"])
def test_an_output_file_has_lf_line_endings(tmp_path: Path, fmt: str) -> None:
    target = tmp_path / "out.bin"
    assert main([FEATURES, "-q", "-f", fmt, "-o", str(target)]) == 0
    data = target.read_bytes()
    assert b"\n" in data and b"\r" not in data
    assert data == render(Path(FEATURES).read_text(encoding="utf-8"), format=fmt).output.encode("utf-8")


def test_an_output_file_is_written_without_newline_translation(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Windows' text mode would turn "\n" into CRLF unless the file is opened
    # with newline="\n": that is what the CLI asks for.
    calls: list[dict[str, object]] = []
    real = Path.write_text

    def spy(self: Path, data: str, *args: object, **kwargs: object) -> int:
        calls.append(kwargs)
        return real(self, data, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", spy)
    assert main([FEATURES, "-q", "-o", str(tmp_path / "out.txt")]) == 0
    assert calls == [{"encoding": "utf-8", "newline": "\n"}]
