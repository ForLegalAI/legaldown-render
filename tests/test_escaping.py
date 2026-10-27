"""Nothing a document or the command line supplies reaches a diagnostic or
standard error as a control character: controls, format characters (bidi
overrides, zero-width) and line separators are written out as ``\\xNN`` or
``\\uNNNN``, so a hostile path cannot move the cursor, set a title or forge a
line of its own."""
from __future__ import annotations

from pathlib import Path

from conftest import FRONT, run_cli

from legaldown_render import render
from legaldown_render.urls import printable

ESC = "x\x1b]0;pwn\x07y"


# -- What a diagnostic shows of a document ---------------------------------------

def test_printable_escapes_controls_and_format_characters() -> None:
    assert printable("a\x1b[2J\x07\n\x7f\x85") == "a\\x1b[2J\\x07\\x0a\\x7f\\x85"
    assert printable("\u202e\u200b\ufeff\u2028\u2029") == "\\u202e\\u200b\\ufeff\\u2028\\u2029"
    assert printable("ž é €") == "ž é €"


EVIL = "https://e/%1b[2J%1b]0;pwned%07%0aerror: [x] forged%e2%80%ae"


def test_a_control_character_in_an_image_path_is_escaped_in_the_message() -> None:
    result = render(FRONT + f"# A\n\n![x](<{EVIL}>)\n")
    messages = [d.message for d in result.diagnostics if d.rule == "path-not-relative"]
    assert messages
    for message in messages:
        assert "\x1b" not in message and "\x07" not in message and "\n" not in message
    assert any("\\x1b[2J\\x1b]0;pwned\\x07\\x0aerror: [x] forged\\u202e" in m for m in messages)


def test_a_control_character_in_a_link_is_escaped_in_the_message() -> None:
    result = render(FRONT + f"# A\n\n[x](<ms-msdt:{EVIL[8:]}>)\n")
    message = next(d.message for d in result.diagnostics if d.rule == "render-link-refused")
    assert "\x1b" not in message and "\x07" not in message and "\n" not in message and "\u202e" not in message
    assert "\\x1b" in message


# -- Standard error of the command -----------------------------------------------

def _clean(errors: bytes) -> str:
    text = errors.decode("utf-8")
    assert not any((ord(c) < 0x20 and c != "\n") or 0x7F <= ord(c) < 0xA0 for c in text), text
    return text


def test_the_cli_writes_no_control_character_from_a_document_to_standard_error(tmp_path: Path) -> None:
    source = tmp_path / "evil.lgd"
    source.write_text(FRONT + f"# A\n\n![x](<{EVIL}>) [y](<ms-msdt:{EVIL[8:]}>)\n", encoding="utf-8")
    done = run_cli(str(source), "-f", "text")
    err = done.stderr.decode("utf-8")
    assert "path-not-relative" in err and "render-link-refused" in err
    assert "\x1b" not in err and "\x07" not in err and "\u202e" not in err
    assert "\r" not in err
    assert not any(line.startswith("error: [x]") for line in err.splitlines())


def test_no_control_character_from_the_document_reaches_stderr(tmp_path: Path) -> None:
    body = ("# A {#a}\n\n{{include: a\x1b]0;pwn\x07.lgd}}\n\n{{ref: x\x1b[2Jy}}\n\n{{term: q\x1b[31m}}\n\n"
            "See {{ref: a}}.\n")
    document = tmp_path / "d.lgd"
    document.write_text(FRONT + body, encoding="utf-8")
    errors = _clean(run_cli(str(document), "-f", "text", cwd=tmp_path).stderr)
    assert errors.count("\n") >= 3  # the three findings, one line each
    assert "\\x1b" in errors  # written out, not dropped
    assert "pwn" in errors


# -- Values from the command line ------------------------------------------------

def test_an_input_file_name_is_escaped(tmp_path: Path) -> None:
    result = run_cli(ESC + ".lgd", cwd=tmp_path)
    assert result.returncode == 1
    assert "cannot read" in _clean(result.stderr) and "\\x1b" in _clean(result.stderr)


def test_an_answers_path_is_escaped(tmp_path: Path) -> None:
    document = tmp_path / "d.lgd"
    document.write_text(FRONT + "# A\n", encoding="utf-8")
    result = run_cli(str(document), "--answers", ESC + ".yaml", cwd=tmp_path)
    assert result.returncode == 1
    assert "\\x1b" in _clean(result.stderr)


def test_an_output_path_is_escaped(tmp_path: Path) -> None:
    document = tmp_path / "d.lgd"
    document.write_text(FRONT + "# A\n", encoding="utf-8")
    result = run_cli(str(document), "-o", "no-such-dir\x1b[2J/out.html", cwd=tmp_path)
    assert result.returncode == 1
    assert "\\x1b" in _clean(result.stderr)
    refused = run_cli(str(document), "-o", "x\x1b[2J.pdf", cwd=tmp_path)
    assert refused.returncode == 2
    assert "\\x1b" in _clean(refused.stderr)


def test_a_style_name_is_escaped(tmp_path: Path) -> None:
    document = tmp_path / "d.lgd"
    document.write_text(FRONT + "# A\n", encoding="utf-8")
    for arguments in (["-s", ESC], ["-s", ESC, "--print-style"]):
        result = run_cli(str(document), *arguments, cwd=tmp_path)
        assert result.returncode == 2
        assert "\\x1b" in _clean(result.stderr)


def test_a_locale_is_escaped(tmp_path: Path) -> None:
    document = tmp_path / "d.lgd"
    document.write_text(FRONT + "# A\n", encoding="utf-8")
    result = run_cli(str(document), "--locale", "zz\x1b[31m", cwd=tmp_path)
    _clean(result.stderr)


def test_a_set_key_is_escaped_in_argparse_errors(tmp_path: Path) -> None:
    document = tmp_path / "d.lgd"
    document.write_text(FRONT + "# A\n", encoding="utf-8")
    for setting in ("bad\x1b[31m", "no\x1b[31m.such=1"):
        result = run_cli(str(document), "--set", setting, cwd=tmp_path)
        assert result.returncode == 2
        assert "\\x1b" in _clean(result.stderr)


def test_an_unrecognized_argument_is_escaped(tmp_path: Path) -> None:
    result = run_cli("--bogus\x1b[31m", cwd=tmp_path)
    assert result.returncode == 2
    assert "\\x1b" in _clean(result.stderr)
