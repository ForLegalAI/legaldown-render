"""CLI and API robustness: stream encodings (S1), non-UTF-8 input (S4), an
unwritable output (S5), an unknown output extension (S6), the error
hierarchy (S2), picklable errors (S3), and the ``--set`` error path (S10, CLI
side)."""
from __future__ import annotations

import io
import os
import pickle
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import DOCUMENTS
from legaldown import Diagnostic

import legaldown_render
import legaldown_render.cli as cli
from legaldown_render import (
    DocumentError,
    OptionsError,
    RenderError,
    RenderOptions,
    RenderRefused,
    StyleError,
    load_style,
    render,
    render_file,
)
from legaldown_render.cli import main

SRC = Path(__file__).resolve().parents[1] / "src"
FEATURES = str(DOCUMENTS / "features.lgd")
CZECH = "---\nlegaldown: '0.2'\ntitle: Smlouva o dílo\nlanguage: cs\n---\n\n# Příloha\n\nŽluťoučký kůň úpěl ďábelské ódy – “citát” → konec, ōsaka.\n"


def _run(args: list[str], stdin: bytes | None = None, *, narrow: bool = True) -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ, PYTHONPATH=str(SRC))
    if narrow:
        # A Windows-like console: neither stream is UTF-8.
        env.update(PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
    return subprocess.run([sys.executable, "-m", "legaldown_render.cli", *args], input=stdin,
                          capture_output=True, env=env, check=False)


def test_the_package_under_test_is_this_worktree() -> None:
    assert Path(legaldown_render.__file__).resolve().is_relative_to(SRC)


# --- S1: encodings ---------------------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["html", "text"])
def test_stdin_to_stdout_is_utf8_whatever_the_console_encoding(fmt: str) -> None:
    expected = render(CZECH, format=fmt).output.encode("utf-8")
    done = _run(["-", "-f", fmt, "-q"], CZECH.encode("utf-8"))
    assert done.returncode == 0, done.stderr
    assert done.stdout == expected
    assert "Příloha".encode() in done.stdout


def test_file_to_stdout_is_utf8_whatever_the_console_encoding(tmp_path: Path) -> None:
    path = tmp_path / "smlouva.lgd"
    path.write_bytes(CZECH.encode("utf-8"))
    done = _run([str(path), "-f", "text", "-q"])
    assert done.returncode == 0, done.stderr
    assert done.stdout == render(CZECH, format="text").output.encode("utf-8")


def test_print_style_survives_a_narrow_console() -> None:
    done = _run(["--print-style", "--style", "continental", "--set", "placeholders.blank=…"])
    assert done.returncode == 0, done.stderr
    assert "…".encode() in done.stdout


def test_stderr_never_crashes_on_a_narrow_console() -> None:
    done = _run(["žlutý→.lgd"])
    assert done.returncode == 1
    assert b"Traceback" not in done.stderr
    assert b"cannot read" in done.stderr
    assert b"\\u2192" in done.stderr  # not encodable in cp1252: backslash-escaped, not an error


# --- S4: not UTF-8 ---------------------------------------------------------------------------

def test_latin1_input_file_is_a_clean_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "latin1.lgd"
    path.write_bytes("# Příloha\n\nCaf\xe9\n".encode("latin-1", errors="replace"))
    assert main([str(path)]) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"legaldown-render: cannot read {path}: not valid UTF-8 (byte 0x")
    assert "at offset" in err
    assert "Traceback" not in err


def test_the_offset_is_in_the_file_not_in_a_buffer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "late.lgd"
    path.write_bytes(b"a" * 20000 + b"\xe9")
    assert main([str(path)]) == 1
    assert "(byte 0xe9 at offset 20000)" in capsys.readouterr().err


def test_latin1_stdin_is_a_clean_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"# Caf\xe9\n"), encoding="cp1252"))
    assert main(["-"]) == 1
    assert "legaldown-render: cannot read <stdin>: not valid UTF-8 (byte 0xe9 at offset 5)" in capsys.readouterr().err


def test_stdin_is_decoded_as_utf8_not_as_the_locale(monkeypatch: pytest.MonkeyPatch,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(CZECH.encode("utf-8")), encoding="cp1252"))
    assert main(["-", "-f", "text", "-q"]) == 0
    assert "Žluťoučký kůň" in capsys.readouterr().out


def test_non_utf8_style_file_is_a_style_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "bad.yaml"
    path.write_bytes(b"version: 1\nname: Caf\xe9\n")
    with pytest.raises(StyleError, match="not valid UTF-8"):
        load_style(path)
    assert main([FEATURES, "--style", str(path)]) == 2
    err = capsys.readouterr().err
    assert "not valid UTF-8" in err
    assert "Traceback" not in err


def test_render_file_raises_document_error_for_non_utf8(tmp_path: Path) -> None:
    path = tmp_path / "latin1.lgd"
    path.write_bytes(b"# Caf\xe9\n")
    with pytest.raises(DocumentError, match=r"not valid UTF-8 \(byte 0xe9 at offset 5\)"):
        render_file(path)
    good = tmp_path / "good.lgd"
    good.write_bytes(CZECH.encode("utf-8"))
    assert "Příloha" in render_file(good, format="text").output


# --- S5: unwritable output -------------------------------------------------------------------

def test_unwritable_output_is_a_clean_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "missing-folder" / "out.html"
    assert main([FEATURES, "-q", "-o", str(target)]) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"legaldown-render: cannot write {target}: ")
    assert "Traceback" not in err


def test_output_that_is_a_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([FEATURES, "-q", "-f", "html", "-o", str(tmp_path)]) == 1
    assert "cannot write" in capsys.readouterr().err


# --- S6: an extension of a format not written -----------------------------------------------

@pytest.mark.parametrize("name", ["x.docx", "x.pdf", "x.md", "x.DOCX", "x.doc", "x.odt", "x.rtf", "x.markdown",
                                  "x.epub", "x.tex", "x.v2.Pdf"])
def test_unknown_output_extension_without_format_is_a_usage_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str) -> None:
    target = tmp_path / name
    with pytest.raises(SystemExit) as exit_info:
        main([FEATURES, "-q", "-o", str(target)])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert f"cannot tell the output format from '{target}'" in err
    assert "--format html or text" in err
    assert not target.exists()


def test_explicit_format_is_honoured_whatever_the_extension(tmp_path: Path) -> None:
    target = tmp_path / "x.docx"
    assert main([FEATURES, "-q", "-f", "text", "-o", str(target)]) == 0
    assert target.read_text(encoding="utf-8").startswith("Services Agreement")


def test_output_without_an_extension_keeps_the_html_default(tmp_path: Path) -> None:
    for target in (tmp_path / "out", tmp_path / "dir.d" / "out"):
        target.parent.mkdir(exist_ok=True)
        assert main([FEATURES, "-q", "-o", str(target)]) == 0
        assert target.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


@pytest.mark.parametrize("name", ["contract.v2", "report.2025-10-27", ".out", "out.", "notes.final", "x.json"])
def test_any_other_extension_keeps_the_html_default(tmp_path: Path, name: str) -> None:
    target = tmp_path / name
    assert main([FEATURES, "-q", "-o", str(target)]) == 0
    assert target.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_a_known_extension_still_sets_the_format(tmp_path: Path) -> None:
    for name, start in (("a.TXT", "Services Agreement"), ("a.text", "Services Agreement"),
                        ("a.htm", "<!DOCTYPE html>"), ("a.html", "<!DOCTYPE html>")):
        target = tmp_path / name
        assert main([FEATURES, "-q", "-o", str(target)]) == 0
        assert target.read_text(encoding="utf-8").startswith(start)


def test_the_help_states_the_output_rule(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "its extension sets the format" not in help_text
    assert "any other name, with or without an extension, writes HTML" in help_text
    assert ".docx, .doc, .odt, .rtf, .pdf, .md, .markdown, .epub, .tex" in help_text


# --- S2: error hierarchy ---------------------------------------------------------------------

def test_style_error_is_a_render_error_and_still_a_value_error() -> None:
    assert issubclass(StyleError, RenderError)
    assert issubclass(StyleError, ValueError)


def test_style_error_is_importable_from_where_it_always_was() -> None:
    from legaldown_render import errors
    from legaldown_render.style import StyleError as from_style
    from legaldown_render.style.loader import StyleError as from_loader

    assert errors.StyleError is StyleError is from_style is from_loader


def test_unknown_format_raises_options_error() -> None:
    assert issubclass(OptionsError, RenderError)
    assert issubclass(OptionsError, ValueError)
    with pytest.raises(OptionsError, match="unknown output format 'pdf'"):
        render("# x\n", RenderOptions(format="pdf"))
    with pytest.raises(OptionsError):
        render("# x\n", format="docx")
    with pytest.raises(RenderError):
        render("# x\n", format="docx")


def test_invalid_style_through_the_api_is_a_render_error() -> None:
    with pytest.raises(RenderError):
        render("# x\n", style="no-such-style")


# --- S3: pickling ----------------------------------------------------------------------------

def test_render_refused_round_trips_through_pickle() -> None:
    diagnostics = [Diagnostic(rule="ref-broken", level="error", message="No such target", line=3)]
    for reason in (None, "Assembly refused: 1 error(s)."):
        error = RenderRefused(diagnostics, reason)
        copy = pickle.loads(pickle.dumps(error))
        assert type(copy) is RenderRefused
        assert copy.diagnostics == diagnostics
        assert str(copy) == str(error)
        assert copy.reason == error.reason


def test_a_real_refusal_round_trips_through_pickle() -> None:
    with pytest.raises(RenderRefused) as caught:
        render(Path(FEATURES).read_text(encoding="utf-8"), strict=True)
    copy = pickle.loads(pickle.dumps(caught.value))
    assert copy.diagnostics == caught.value.diagnostics
    assert str(copy) == str(caught.value)


def test_style_error_round_trips_through_pickle() -> None:
    error = StyleError("overrides", ["a.b: wrong", "c: also wrong"])
    copy = pickle.loads(pickle.dumps(error))
    assert type(copy) is StyleError
    assert (copy.source, copy.problems) == ("overrides", ["a.b: wrong", "c: also wrong"])
    assert str(copy) == str(error)


def test_options_error_round_trips_through_pickle() -> None:
    error = OptionsError("unknown output format 'pdf'")
    assert str(pickle.loads(pickle.dumps(error))) == str(error)


# --- S10 (CLI side): a StyleError from --set reaches the user ---------------------------------

def test_set_style_error_is_shown_by_argparse(monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    def refuse(key: str, text: str) -> object:
        raise StyleError("overrides", [f"{key}: the value is no good"])

    monkeypatch.setattr(cli, "parse_override", refuse)
    with pytest.raises(SystemExit) as exit_info:
        main([FEATURES, "--set", "numbering.scheme=oops"])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "numbering.scheme: the value is no good" in err
    assert "--set" in err
