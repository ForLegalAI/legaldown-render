"""Small fixes (WP7): a label filled with an empty ``{section}`` drops the field
and its dot, so a preamble item reads "1", not ".1" (R5); the print stylesheet
breaks before the attachments container, not inside it (W2)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from legaldown_render import render
from legaldown_render.resolve.numbering import fill

FRONT = "---\ntitle: T\n---\n"
PREAMBLE_AND_SECTION = (
    "\nPreamble.\n\n- first pre\n- second pre\n\n# A {#a}\n\n- in a\n"
)


def text(body: str, **settings: object) -> str:
    return render(FRONT + body, format="text", **settings).output


def html(body: str, **settings: object) -> str:
    return render(FRONT + body, format="html", **settings).output


# -- R5: an empty section drops its dot (numbering.fill) -----------------------------------------

def test_fill_drops_an_empty_section_with_one_following_dot() -> None:
    assert fill("{section}.{n}", n="1", section="") == "1"
    assert fill("{section}.{n}", n="1", section="2") == "2.1"
    # Only ONE dot goes with the field.
    assert fill("{section}..{n}", n="1", section="") == ".1"


def test_fill_keeps_other_text_of_an_empty_section_as_written() -> None:
    # No dot right after the field: the field alone is removed, as before.
    assert fill("({section}){n}", n="a", section="") == "()a"
    assert fill("{section}", n="1", section="") == ""


def test_fill_without_a_section_value_keeps_the_dot() -> None:
    # The rule applies only when the section value is given and empty.
    assert fill("{section}.{n}", n="1") == ".1"


# -- R5: preamble list items agree with their designation ----------------------------------------

def test_a_preamble_list_is_numbered_without_a_leading_dot_in_text() -> None:
    output = text(PREAMBLE_AND_SECTION, style="continental")
    assert "1 first pre" in output
    assert "2 second pre" in output
    assert not any(line.startswith(".") for line in output.splitlines())


def test_a_preamble_list_is_numbered_without_a_leading_dot_in_html() -> None:
    output = html(PREAMBLE_AND_SECTION, style="continental")
    assert '<span class="ld-label">1</span><div class="ld-item"><p class="ld-p">first pre</p>' in output
    assert '<span class="ld-label">2</span><div class="ld-item"><p class="ld-p">second pre</p>' in output
    assert 'class="ld-label">.1<' not in output


def test_a_list_in_a_section_keeps_its_section_prefix() -> None:
    output = text(PREAMBLE_AND_SECTION, style="continental")
    assert "1.1 in a" in output
    html_output = html(PREAMBLE_AND_SECTION, style="continental")
    assert '<span class="ld-label">1.1</span><div class="ld-item"><p class="ld-p">in a</p>' in html_output


# -- W2: print breaks before the attachments container --------------------------------------------

SRC = Path(__file__).resolve().parents[1] / "src"
ATTACHED = (
    "---\ntitle: T\nattachments:\n"
    "  - id: one\n    title: First attachment\n    file: one.pdf\n"
    "  - id: two\n    title: Second attachment\n    file: two.pdf\n"
    "---\n\n# A {#a}\n\nBody.\n"
)


def _cli(*args: str, stdin: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONUTF8="1")
    return subprocess.run([sys.executable, "-m", "legaldown_render.cli", *args], input=stdin,
                          capture_output=True, text=True, env=env, check=False)


def test_a_standalone_page_break_render_has_the_attachment_break_rules() -> None:
    done = _cli("-", "--set", "attachments.separator=page-break", stdin=ATTACHED)
    assert done.returncode == 0, done.stderr
    assert "<!DOCTYPE html>" in done.stdout
    assert 'class="ld-attachments ld-separator-page-break"' in done.stdout
    # The break goes before the container (heading included), then before each later attachment.
    assert ".ld-attachments.ld-separator-page-break { break-before: page; }" in done.stdout
    assert ".ld-separator-page-break .ld-attachment + .ld-attachment { break-before: page; }" in done.stdout
    assert ".ld-attachments-heading { break-after: avoid; }" in done.stdout
    # The old rule broke before every attachment, the first one included, inside the container.
    assert ".ld-separator-page-break .ld-attachment { break-before: page; }" not in done.stdout


def test_without_a_page_break_separator_the_container_is_not_broken() -> None:
    done = _cli("-", "--set", "attachments.separator=none", stdin=ATTACHED)
    assert done.returncode == 0, done.stderr
    # The rules are scoped by the separator class, which no element carries here.
    assert 'class="ld-attachments ld-separator-none"' in done.stdout
    assert 'ld-separator-page-break"' not in done.stdout
