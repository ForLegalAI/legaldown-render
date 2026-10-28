"""How the command line reads and writes bytes: UTF-8 whatever the console's
encoding, a clean error for input that is not UTF-8 or output that cannot be
written, LF line endings, in-process ``main()`` leaving the streams alone, and
the rule that ``-o`` names a format by its extension (§13.8)."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from conftest import FEATURES, SRC, run_cli

import legaldown_render
from legaldown_render import DocumentError, StyleError, load_style, render, render_file
from legaldown_render.cli import main
from legaldown_render.writers import file_extension, format_for_path

CZECH = "---\nlegaldown: '0.2'\ntitle: Smlouva o dílo\nlanguage: cs\n---\n\n# Příloha\n\nŽluťoučký kůň úpěl ďábelské ódy – “citát” → konec, ōsaka.\n"


def _narrow(args: list[str], stdin: bytes | None = None):  # type: ignore[no-untyped-def]
    """The program on a Windows-like console: neither stream is UTF-8."""
    return run_cli(*args, stdin=stdin, narrow=True)


def test_the_package_under_test_is_this_worktree() -> None:
    assert Path(legaldown_render.__file__).resolve().is_relative_to(SRC)


# -- Encodings -------------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["html", "text"])
def test_stdin_to_stdout_is_utf8_whatever_the_console_encoding(fmt: str) -> None:
    expected = render(CZECH, format=fmt).output.encode("utf-8")
    done = _narrow(["-", "-f", fmt, "-q"], CZECH.encode("utf-8"))
    assert done.returncode == 0, done.stderr
    # Exact bytes: the output is UTF-8 with LF line endings on every platform, so
    # the same comparison holds on Windows (no CRLF translation of stdout).
    assert done.stdout == expected
    assert "Příloha".encode() in done.stdout


def test_file_to_stdout_is_utf8_whatever_the_console_encoding(tmp_path: Path) -> None:
    path = tmp_path / "smlouva.lgd"
    path.write_bytes(CZECH.encode("utf-8"))
    done = _narrow([str(path), "-f", "text", "-q"])
    assert done.returncode == 0, done.stderr
    # Exact bytes, as above: UTF-8 with LF line endings on every platform.
    assert done.stdout == render(CZECH, format="text").output.encode("utf-8")


def test_print_style_survives_a_narrow_console() -> None:
    done = _narrow(["--print-style", "--style", "continental", "--set", "placeholders.blank=…"])
    assert done.returncode == 0, done.stderr
    assert "…".encode() in done.stdout


def test_stderr_never_crashes_on_a_narrow_console() -> None:
    done = _narrow(["žlutý→.lgd"])
    assert done.returncode == 1
    assert b"Traceback" not in done.stderr
    assert b"cannot read" in done.stderr
    assert b"\\u2192" in done.stderr  # not encodable in cp1252: backslash-escaped, not an error


# -- Input that is not UTF-8 -----------------------------------------------------

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


# -- Output that cannot be written -----------------------------------------------

def test_unwritable_output_is_a_clean_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "missing-folder" / "out.html"
    assert main([FEATURES, "-q", "-o", str(target)]) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"legaldown-render: cannot write {target}: ")
    assert "Traceback" not in err


def test_output_that_is_a_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([FEATURES, "-q", "-f", "html", "-o", str(tmp_path)]) == 1
    assert "cannot write" in capsys.readouterr().err


# -- Streams and line endings ----------------------------------------------------

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


@pytest.mark.parametrize("fmt", ["html", "text"])
def test_standard_output_has_lf_line_endings(fmt: str) -> None:
    done = _narrow([FEATURES, "-f", fmt, "-q"])
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


# -- The extension of -o sets the format, but only for a format that is written ----

@pytest.mark.parametrize(("name", "extension"), [
    ("x.pdf", "pdf"), (".pdf", "pdf"), (".md", "md"), ("x.pdf.", "pdf"), ("x.docx ", "docx"),
    ("x.pdf. .", "pdf"), ("X.PDF", "pdf"), ("a.b.txt", "txt"), ("contract", ""), ("", ""), (".", ""),
    ("..", ""), ("x.", ""),
])
def test_the_extension_of_a_file_name(name: str, extension: str) -> None:
    assert file_extension(name) == extension


@pytest.mark.parametrize(("name", "expected"), [
    (".txt", "text"), ("x.txt.", "text"), ("x.html ", "html"), (".htm", "html"), ("x.unknown", None),
    ("x.docx", "docx"), ("X.DOCX", "docx"), ("x.docx ", "docx"), (".docx", "docx"), ("x.doc", None),
])
def test_the_format_a_file_name_implies(name: str, expected: str | None) -> None:
    assert format_for_path(name) == expected


@pytest.mark.parametrize("name", ["x.pdf", "x.md", "x.doc", "x.odt", "x.rtf", "x.markdown",
                                  "x.epub", "x.tex", "x.v2.Pdf", ".pdf", ".md", "x.pdf.", "dir.pdf", "x.MD"])
def test_unknown_output_extension_without_format_is_a_usage_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str) -> None:
    target = tmp_path / name
    with pytest.raises(SystemExit) as exit_info:
        main([FEATURES, "-q", "-o", str(target)])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert f"cannot tell the output format from '{target}'" in err
    assert "--format html, text or docx" in err
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


# Win32 drops trailing dots and spaces from a file name, so "x.pdf." names the file x.pdf there
# and the test would not exercise the name it says it does.
_TRAILING = pytest.mark.skipif(sys.platform == "win32", reason="Windows strips trailing dots and spaces from names")


@pytest.mark.parametrize("name", ["x.pdf", pytest.param("x.pdf.", marks=_TRAILING), ".md",
                                  pytest.param("x.odt ", marks=_TRAILING)])
def test_explicit_format_is_honoured_whatever_the_extension(tmp_path: Path, name: str) -> None:
    target = tmp_path / name
    assert main([FEATURES, "-q", "-f", "text", "-o", str(target)]) == 0
    assert target.read_text(encoding="utf-8").startswith("Services Agreement")


def test_output_without_an_extension_keeps_the_html_default(tmp_path: Path) -> None:
    for target in (tmp_path / "out", tmp_path / "dir.d" / "out"):
        target.parent.mkdir(exist_ok=True)
        assert main([FEATURES, "-q", "-o", str(target)]) == 0
        assert target.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


@pytest.mark.parametrize("name", ["contract", "contract.v2", "report.2025-10-27", ".out",
                                  pytest.param("out.", marks=_TRAILING), "notes.final", "x.json",
                                  pytest.param("x..", marks=_TRAILING)])
def test_any_other_name_keeps_the_html_default(tmp_path: Path, name: str) -> None:
    target = tmp_path / name
    assert main([FEATURES, "-q", "-o", str(target)]) == 0
    assert target.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


@pytest.mark.parametrize(("name", "start"), [
    ("a.TXT", "Services Agreement"), ("a.text", "Services Agreement"), ("a.htm", "<!DOCTYPE html>"),
    ("a.html", "<!DOCTYPE html>"), (".txt", "Services Agreement"), ("x.txt.", "Services Agreement"),
])
def test_a_known_extension_still_sets_the_format(tmp_path: Path, name: str, start: str) -> None:
    target = tmp_path / name
    assert main([FEATURES, "-q", "-o", str(target)]) == 0
    assert target.read_text(encoding="utf-8").startswith(start)


def test_the_help_states_the_output_rule(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "its extension sets the format" not in help_text
    assert "any other name, with or without an extension, writes HTML" in help_text
    assert ".doc, .odt, .rtf, .pdf, .md, .markdown, .epub, .tex" in help_text
