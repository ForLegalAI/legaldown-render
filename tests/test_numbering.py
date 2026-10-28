"""Numbering and labels (§13.1–§13.3): counter formats, filling a label
template, and the preamble, where an empty ``{section}`` drops its field and
its dot so that a preamble list item reads "1", not ".1"."""
from __future__ import annotations

import pytest
from conftest import html, text

from legaldown_render.resolve.numbering import extend, fill, format_counter

# -- Counters and templates ------------------------------------------------------

@pytest.mark.parametrize(("value", "counter", "expected"), [
    (1, "lower-alpha", "a"), (26, "lower-alpha", "z"), (27, "lower-alpha", "aa"), (3, "upper-alpha", "C"),
    (4, "lower-roman", "iv"), (14, "upper-roman", "XIV"), (7, "decimal", "7"),
])
def test_counters(value: int, counter: str, expected: str) -> None:
    assert format_counter(value, counter) == expected


def test_fill_and_extend() -> None:
    assert fill("{section}.{n} {other}", n="2", section="5") == "5.2 {other}"
    assert extend("", ".1", textual=False) == "1"
    assert extend("2.1", "(b)", textual=False) == "2.1(b)"
    assert extend("Termination", "(a)", textual=True) == "Termination (a)"


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


# -- Preamble list items agree with their designation ----------------------------

PREAMBLE_AND_SECTION = (
    "\nPreamble.\n\n- first pre\n- second pre\n\n# A {#a}\n\n- in a\n"
)


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
