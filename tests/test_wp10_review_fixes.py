"""Review fixes after wave 2 (WP10): links are an allow-list and a refused one is
reported, control characters never reach a diagnostic, style templates are
validated only where a setting is used, and small wording fixes."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from legaldown_render import StyleError, render
from legaldown_render.style import load_style
from legaldown_render.urls import displayed_url, is_link_target, printable

SRC = Path(__file__).resolve().parents[1] / "src"
FRONT = "---\ntitle: T\n---\n\n"


def html(body: str, **settings: object) -> str:
    return render(FRONT + body, format="html", **settings).output


def text(body: str, **settings: object) -> str:
    return render(FRONT + body, format="text", **settings).output


# -- 1: links are an allow-list ---------------------------------------------------

HANDLERS = [
    "ms-msdt:/id PCWDiagnostic", "ms-word:ofe|u|https://x/a.docx", "ms-officecmd:{}", "search-ms:query=x",
    "ms-appinstaller:?source=https://x/a.appx", "vscode://file/x", "intent://x#Intent;end", "blob:https://x/1",
    "about:blank", "jar:https://x/a.jar!/", "view-source:https://x", "mhtml:file://x", "ms-its:x.chm::/a.htm",
    "C:\\x", "C:/x", "localhost:8080", "magnet:?xt=urn:btih:x", "javascript:alert(1)", "file:///etc/passwd",
    "data:text/html,x", " MS-MSDT:x",
]
ALLOWED = [
    "http://e/x", "https://e/x", "mailto:a@e", "tel:+1", "ftp://e/x", "ftps://e/x", "sftp://e/x", "sip:a@e",
    "sips:a@e", "sms:+1", "urn:isbn:1", "geo:1,2", "news:a.b", "a/b.pdf", "../a.pdf", "#top", "?q=1",
]


@pytest.mark.parametrize("href", HANDLERS)
def test_a_protocol_handler_or_pseudo_scheme_is_not_a_link(href: str) -> None:
    assert not is_link_target(href)
    body = f"# A\n\nSee [click](<{href}>) now.\n"
    result = render(FRONT + body, format="html")
    assert "See click now." in result.output
    assert "<a href" not in result.output.split("</header>")[1]
    assert "See click now." in text(body)
    assert "<" not in text(body).split("See click now.")[0].split("\n")[-1]


def test_the_scheme_is_read_as_a_browser_reads_it() -> None:
    # markdown-it percent-encodes a tab in a URL it parses ("ms%09-msdt:x" is a
    # relative path to a browser); the check still holds for a URL it is given
    # directly, as a browser would read it.
    for href in ("ms\t-msdt:x", "jav\nascript:x", "\x01javascript:x", "\u0000 vscode://x"):
        assert not is_link_target(href), repr(href)


@pytest.mark.parametrize("href", ALLOWED)
def test_an_allowed_scheme_or_a_relative_url_is_a_link(href: str) -> None:
    assert is_link_target(href)
    assert f'<a href="{href}">click</a>' in html(f"# A\n\nSee [click](<{href}>).\n")


def test_a_refused_link_is_reported_once_as_a_warning() -> None:
    body = "# A\n\n[a](ms-msdt:x) and [b](ms-msdt:x) and [c](vscode://x) and [d](http://ok) and [e]().\n"
    result = render(FRONT + body, format="html")
    refused = [d for d in result.diagnostics if d.rule == "render-link-refused"]
    assert [d.level for d in refused] == ["warning", "warning"]
    assert "Link to 'ms-msdt:x' is not rendered as a link" in refused[0].message
    assert "its text is shown" in refused[0].message
    assert "vscode://x" in refused[1].message
    assert result.ok  # a Warning does not refuse, even under --strict's Errors
    assert render(FRONT + body, format="text").diagnostics == result.diagnostics


def test_an_empty_or_allowed_link_is_not_reported() -> None:
    result = render(FRONT + "# A\n\n[a]() [b](<>) [c](https://e) [d](#x) [e](rel/path)\n")
    assert not [d for d in result.diagnostics if d.rule == "render-link-refused"]


# -- 3, 4, 5: what a diagnostic shows of a document ------------------------------


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


def test_the_cli_writes_no_control_character_from_a_document_to_standard_error(tmp_path: Path) -> None:
    source = tmp_path / "evil.lgd"
    source.write_text(FRONT + f"# A\n\n![x](<{EVIL}>) [y](<ms-msdt:{EVIL[8:]}>)\n", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(SRC))
    done = subprocess.run([sys.executable, "-m", "legaldown_render.cli", str(source), "-f", "text"],
                          capture_output=True, env=env, check=False)
    err = done.stderr.decode("utf-8")
    assert "path-not-relative" in err and "render-link-refused" in err
    assert "\x1b" not in err and "\x07" not in err and "\u202e" not in err
    assert "\r" not in err
    assert not any(line.startswith("error: [x]") for line in err.splitlines())


def test_a_path_that_starts_another_is_not_covered_by_the_validators_report() -> None:
    # Whatever the validator reports for one path, a different path whose text
    # it contains is still the renderer's to report.
    result = render(FRONT + "# A\n\n![a](/x.png) ![b](/x.png2)\n")
    messages = [d.message for d in result.diagnostics if d.rule == "path-not-relative"]
    assert any("'/x.png2'" in m for m in messages)
    assert any("'/x.png'" in m for m in messages)


@pytest.mark.parametrize("path", ["javascript:alert(1)", "data:text/html,x", "https://e/p.png"])
def test_the_not_relative_message_is_generic(path: str) -> None:
    result = render(FRONT + f"# A\n\n![x](<{path}>)\n")
    message = next(d.message for d in result.diagnostics if d.rule == "path-not-relative")
    assert "never fetched" not in message
    assert "file references are relative paths with no URI scheme. Its alt text is shown instead." in message


# -- 8: a link with no text shows the URL as written ----------------------------


def test_displayed_url_undoes_the_encoding_unless_it_shows_a_control() -> None:
    assert displayed_url(" https://e/a%20b ") == "https://e/a b"
    assert displayed_url("https://e/a%0ab") == "https://e/a%0ab"
    assert displayed_url("https://e/%E2%80%AE") == "https://e/%E2%80%AE"


def test_a_link_with_no_text_shows_the_written_url() -> None:
    body = "# A\n\nSee [](<https://e/a b/é>).\n"
    assert 'href="https://e/a%20b/%C3%A9">https://e/a b/é</a>' in html(body)
    assert "See https://e/a b/é." in text(body)


# -- 6: a style is validated where its settings are used ------------------------

def _problems(overrides: dict[str, object]) -> list[str]:
    with pytest.raises(StyleError) as caught:
        load_style("default", overrides=overrides)
    return caught.value.problems


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


# -- 7: a blank of invisible characters is no visual blank ------------------------


@pytest.mark.parametrize("blank", ["​", "﻿", " ​‍﻿ ", " ", "‮⁠"])
@pytest.mark.parametrize("fmt", ["html", "text"])
def test_a_blank_of_invisible_characters_falls_back_to_tbd(blank: str, fmt: str) -> None:
    source = FRONT.replace("title: T", "title: T\nplaceholders:\n  - id: fee\n    type: text") + "# A\n\nFee {{placeholder: fee}}.\n"
    result = render(source, format=fmt, overrides={"placeholders.blank": blank})
    assert "[TBD: fee]" in result.output


def test_a_blank_with_a_visible_character_is_kept() -> None:
    source = FRONT.replace("title: T", "title: T\nplaceholders:\n  - id: fee\n    type: text") + "# A\n\nFee {{placeholder: fee}}.\n"
    assert "Fee ​__​." in render(source, format="text", overrides={"placeholders.blank": "​__​"}).output
