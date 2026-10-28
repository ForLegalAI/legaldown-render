"""Defined terms: what ``{{term:}}`` shows (§7.3), which is the rendered text of
the term where it is defined, with its own Markdown, references and
placeholders resolved; terms that refer to themselves or to each other; and
the cap on the length of a term text. How much work nested terms may cost is in
test_terms_budget.py."""
from __future__ import annotations

import pytest
from conftest import FRONT, html, text

from legaldown_render import render

# -- The text of a term ----------------------------------------------------------

def test_a_term_reference_shows_the_rendered_text_of_the_term() -> None:
    output = text('# A\n\n"A *b* c" {{def: abc}} means it.\n\nSee {{term: abc}}.\n')
    assert "See A b c." in output
    assert "*" not in output.split("See", 1)[1]


def test_a_term_reference_label_still_wins() -> None:
    output = text('# A\n\n"A *b* c" {{def: abc}} means it.\n\nSee {{term: abc, label=the thing}}.\n')
    assert "See the thing." in output


def test_a_term_reference_resolves_directives_inside_the_term() -> None:
    body = '# A {#a}\n\n"Fee **under** {{ref: a}}" {{def: fee}} means it.\n\nSee {{term: fee}}.\n'
    output = text(body)
    assert "See Fee under" in output


def test_a_term_reference_uses_the_first_definition() -> None:
    body = '# A\n\n"First *one*" {{def: t}} here.\n\n"Second" {{def: t}} there.\n\nSee {{term: t}}.\n'
    assert "See First one." in text(body)


def test_a_term_reference_before_its_definition() -> None:
    body = '# A\n\nSee {{term: abc}} first.\n\n"A *b* c" {{def: abc}} means it.\n'
    assert "See A b c first." in text(body)


def test_a_plain_term_reference_is_unchanged() -> None:
    assert "See Fee." in text('# A\n\n"Fee" {{def: fee}} means it.\n\nSee {{term: fee}}.\n')


def test_an_empty_term_falls_back_to_the_validator() -> None:
    output = text('# A\n\n"" {{def: late-fee}} means it.\n\nSee {{term: late-fee}}.\n')
    assert "See Late Fee." in output


def test_an_undefined_term_is_still_reported() -> None:
    assert "[UNDEFINED: nope]" in text("# A\n\nSee {{term: nope}}.\n")


# -- A reference, placeholder or definition inside a term ------------------------

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


# -- Terms that refer to themselves or to each other -----------------------------

SELF = '# A\n\n"Self {{term: s}}" {{def: s}} means it.\n\nSee {{term: s}} and {{term: s, label=the label}}.\n'

INDIRECT = ('# A\n\n"A {{term: b}}" {{def: a}} x.\n\n"B {{term: a}}" {{def: b}} y.\n\n'
            "See {{term: a}}, {{term: b}} and {{term: a, label=the label}}.\n")


@pytest.mark.parametrize("output_format", ["text", "html"])
@pytest.mark.parametrize("body", [SELF, INDIRECT], ids=["direct", "indirect"])
def test_a_cyclic_term_renders_in_both_writers(body: str, output_format: str) -> None:
    result = render(FRONT + body, format=output_format)
    assert "the label" in result.output
    assert "[UNDEFINED" not in result.output


def test_a_term_inside_its_own_definition_shows_its_id() -> None:
    output = text(SELF)
    # At the definition and at a reference alike: the inner reference shows
    # the id (its label when it has one), and goes no further.
    assert "Self Self s means it." in output
    assert "See Self s and the label." in output


def test_a_term_reference_in_a_cycle_shows_its_label_else_its_id() -> None:
    output = text(INDIRECT)
    assert output.rstrip().endswith("and the label.")
    # No term shows its own reference as raw directive source.
    assert "{{" not in output


def test_a_cycle_through_a_reference_site_inside_another_terms_definition() -> None:
    body = ('# A\n\n"A" {{def: a}} x.\n\n"B {{term: a}} and {{term: c}}" {{def: b}} y.\n\n'
            '"C {{term: b}}" {{def: c}} z.\n\nSee {{term: b}}.\n')
    assert "See B A and" in text(body)
    assert "B A and" in html(body)


@pytest.mark.parametrize("output_format", ["text", "html"])
def test_cycles_through_refs_still_terminate(output_format: str) -> None:
    body = ('# H {#h}\n\n"A {{term: b}} {{ref: h}}" {{def: a}} x.\n\n"B {{term: a}}" {{def: b}} y.\n\n'
            '"C {{term: c}}" {{def: c}}\n\nSee {{term: a}}, {{term: b}}, {{term: c}}.\n')
    out = render(FRONT + body, format=output_format).output
    assert "{{" not in out
    # Which term shows what inside a cycle follows the order they are first needed in.
    assert "A b 1" in out and "C c" in out


CYCLE = ('# A\n\nUsed first: {{term: ca}} and {{term: cb}}.\n\n'
         '"Alpha {{term: cb}}" {{def: ca}}\n\n"Beta {{term: ca}}" {{def: cb}}\n')


def test_a_term_reference_in_a_cycle_does_not_depend_on_the_order_of_use() -> None:
    # A term shown by {{term:}} is its text with that term open (a reference back to it
    # shows its id), whichever term is used first. (The definition site renders with no term
    # open, so it can read longer: "Beta Alpha Beta ca" there, "Beta Alpha cb" in a reference.)
    ca_first = text(CYCLE)
    cb_first = text(CYCLE.replace("{{term: ca}} and {{term: cb}}", "{{term: cb}} and {{term: ca}}"))
    assert "Used first: Alpha Beta ca and Beta Alpha cb." in ca_first
    assert "Used first: Beta Alpha cb and Alpha Beta ca." in cb_first


# -- The cap on a term text ------------------------------------------------------

def _warnings(body: str, **settings: object) -> list[str]:
    return [d.message for d in render(FRONT + body, format="text", **settings).diagnostics
            if d.rule == "render-term-too-long"]


def test_a_term_over_the_cap_shows_its_id_and_one_warning_names_it() -> None:
    long_term = "word " * 250
    body = f'# A\n\n"{long_term}" {{{{def: big}}}}\n\nSee {{{{term: big}}}} and {{{{term: big}}}}.\n'
    out = text(body)
    assert "See big and big." in out
    assert _warnings(body) == [
        "The term of 'big' is longer than 1000 characters once its references are resolved; "
        "'{{term: big}}' shows the definition id."]


def test_a_label_still_wins_over_the_cap() -> None:
    body = '# A\n\n"' + "word " * 250 + '" {{def: big}}\n\nSee {{term: big, label=Big}}.\n'
    assert "See Big." in text(body)


def test_a_term_at_the_cap_is_shown_whole() -> None:
    term = "x" * 1000
    body = f'# A\n\n"{term}" {{{{def: edge}}}}\n\nSee {{{{term: edge}}}}.\n'
    assert f"See {term}." in text(body)
    assert _warnings(body) == []


def test_a_normal_nested_term_is_unaffected() -> None:
    body = ('# A\n\n"Company" {{def: company}}\n\n"Affiliate of the {{term: company}}" {{def: affiliate}}\n\n'
            'See {{term: affiliate}}.\n')
    assert "See Affiliate of the Company." in text(body)
    assert _warnings(body) == []
