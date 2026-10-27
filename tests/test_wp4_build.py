"""Build fidelity (WP4): what the builder keeps of a comment-only item or
quote (B2), how emphasis next to a directive parses (B3), the spacing where
an inline comment was dropped (B4), and what ``{{term:}}`` shows (B8)."""
from __future__ import annotations

import html as html_escape
import unicodedata

import pytest
from legaldown import parse, validate
from markdown_it import MarkdownIt
from markdown_it.common.utils import isPunctChar

from legaldown_render import render
from legaldown_render.build import _WRAPPERS, build_tree
from legaldown_render.tree import (
    DirectiveSource,
    DraftingNote,
    Emphasis,
    Paragraph,
    Quote,
    Strong,
    Text,
)

FRONT = "---\ntitle: T\n---\n"


def text(body: str, **settings: object) -> str:
    return render(FRONT + body, format="text", **settings).output


def html(body: str, **settings: object) -> str:
    return render(FRONT + body, format="html", **settings).output


def built(body: str):
    document = parse(FRONT + body)
    return build_tree(document, validate(document))


def blocks_of(body: str):
    return built(body).sections[0].blocks


# -- B2: comment-only list items and quotes ----------------------------------


def test_a_list_item_holding_only_a_comment_is_dropped() -> None:
    output = text("# A\n\n- a\n- <!-- removed -->\n- c\n")
    assert "(a) a\n(b) c" in output
    assert "(c)" not in output


def test_dropping_a_comment_only_item_keeps_markers_on_their_items() -> None:
    # Numbering and the marker-to-item mapping are those of the validator's
    # (unfiltered) list; only the output drops the item.
    body = "# A\n\n- <!-- gone -->\n- b {#b}\n  - <!-- gone -->\n  - bb {#bb}\n- c {#c}\n\nSee {{ref: b}}, {{ref: bb}}, {{ref: c}}.\n"
    output = text(body)
    assert "(a) b\n    (i) bb\n(b) c" in output
    assert "See 1(a), 1(a)(i), 1(b)." in output


def test_a_list_left_with_no_items_is_dropped() -> None:
    output = text("# A\n\nBefore.\n\n- <!-- one -->\n- <!-- two -->\n\nAfter.\n")
    assert "(a)" not in output
    assert "Before.\n\nAfter." in output
    assert "<ul" not in html("# A\n\n- <!-- one -->\n")
    assert "<ol" not in html("# A\n\n1. <!-- one -->\n")


def test_an_empty_item_keeps_its_place_but_a_comment_only_one_does_not() -> None:
    # The validator keeps an empty item (CommonMark); a comment is the
    # author's way to remove one.
    output = text("# A\n\n- \n- <!-- c -->\n- b\n")
    assert "(a)\n(b) b" in output


def test_an_item_with_a_comment_and_text_stays() -> None:
    assert "(a) kept" in text("# A\n\n- kept <!-- c -->\n")


def test_an_item_with_a_nested_list_stays_even_when_its_own_text_is_a_comment() -> None:
    output = text("# A\n\n- <!-- c -->\n  - inner\n")
    assert "inner" in output


def test_a_comment_only_quote_is_dropped() -> None:
    assert "<blockquote" not in html("# A\n\nText.\n\n> <!-- c -->\n")
    assert blocks_of("# A\n\n> <!-- c -->\n") == ()
    assert [type(b) for b in blocks_of("# A\n\n> <!-- c -->\n\nText.\n")] == [Paragraph]


def test_a_quote_with_text_stays() -> None:
    assert [type(b) for b in blocks_of("# A\n\n> said <!-- c -->\n")] == [Quote]


def test_an_empty_drafting_note_keeps_its_label() -> None:
    blocks = blocks_of("# A\n\n> [!DRAFTING]\n> <!-- c -->\n")
    assert [type(b) for b in blocks] == [DraftingNote]
    assert "Drafting note" in html("# A\n\n> [!DRAFTING]\n> <!-- c -->\n") or "ld-drafting" in html(
        "# A\n\n> [!DRAFTING]\n> <!-- c -->\n")


def test_a_quote_nested_in_a_quote_that_is_comment_only_is_dropped_with_it() -> None:
    assert blocks_of("# A\n\n> > <!-- c -->\n") == ()


def test_a_comment_only_item_does_not_leave_an_empty_list_in_an_item() -> None:
    output = text("# A\n\n- a\n  - <!-- c -->\n- b\n")
    assert "(a) a\n(b) b" in output


# -- B3: emphasis flanking next to a directive -------------------------------


def _signature(inlines) -> str:
    out = []
    for inline in inlines:
        match inline:
            case Text(text=value):
                out.append(html_escape.escape(value, quote=False))
            case DirectiveSource():
                out.append("{x}")
            case Emphasis(children=children):
                out.append(f"<em>{_signature(children)}</em>")
            case Strong(children=children):
                out.append(f"<strong>{_signature(children)}</strong>")
            case _:
                raise AssertionError(inline)
    return "".join(out)


@pytest.mark.parametrize("source", [
    "_text_{{ref: a}}",
    "{{ref: a}}_text_",
    "__b__{{ref: a}}",
    "{{ref: a}}__b__",
    "*t*{{ref: a}}",
    "{{ref: a}}*t*",
    "x _text_{{ref: a}} y",
    "x{{ref: a}}_text_y",
    "*a{{ref: a}}* b",
    "**a{{ref: a}}**b",
    "_{{ref: a}}_",
    "_a_{{ref: a}}_b_",
    "foo_{{ref: a}}_bar",
])
def test_emphasis_next_to_a_directive_parses_as_it_does_next_to_a_brace(source: str) -> None:
    body = f"# A {{#a}}\n\n{source}\n"
    paragraphs = [b for b in blocks_of(body) if isinstance(b, Paragraph)]
    expected = MarkdownIt("commonmark").renderInline(source.replace("{{ref: a}}", "{x}"))
    assert _signature(paragraphs[0].inlines) == expected


def test_every_sentinel_wrapper_is_punctuation_to_markdown_it() -> None:
    wrappers = _WRAPPERS
    assert wrappers
    for char in wrappers:
        assert isPunctChar(char), hex(ord(char))
        assert unicodedata.category(char).startswith("P")


def test_a_directive_next_to_emphasis_renders() -> None:
    output = html("# A {#a}\n\n_text_{{ref: a}} and {{ref: a}}_more_\n")
    assert "<em>text</em>" in output and "<em>more</em>" in output


def test_wrappers_do_not_leak_into_code_urls_or_alt_text() -> None:
    output = text("# A {#a}\n\n`{{ref: a}}` [x](http://e.com/{{ref: a}}) ![{{ref: a}}](p.png)\n")
    assert "{{ref: a}}" in output
    for char in _WRAPPERS:
        assert char not in output


# -- B4: spacing where an inline comment was dropped -------------------------


@pytest.mark.parametrize(("body", "expected"), [
    ("one <!-- c -->\ntwo\n", "one two"),
    ("one <!-- c -->  \ntwo\n", "one\ntwo"),
    ("one <!-- c -->\\\ntwo\n", "one\ntwo"),
    ("one <!-- c --> <!-- d -->\ntwo\n", "one two"),
    ("one <br>\ntwo\n", "one two"),
])
def test_no_double_space_before_a_line_break(body: str, expected: str) -> None:
    assert text("# A\n\n" + body).endswith("\n\n" + expected + "\n")


def test_no_trailing_space_when_a_dropped_comment_ends_the_block() -> None:
    paragraph = blocks_of("# A\n\nword <!-- c -->\n")[0]
    assert [i.text for i in paragraph.inlines] == ["word"]


def test_a_space_still_parts_words_around_a_dropped_comment() -> None:
    assert "a b" in text("# A\n\na <!-- c --> b\n")
    assert "a b" in text("# A\n\na<br>b\n")


def test_a_dropped_comment_between_text_and_a_hard_break_leaves_no_space() -> None:
    paragraph = blocks_of("# A\n\none <!-- c -->\\\ntwo\n")[0]
    first = paragraph.inlines[0]
    assert isinstance(first, Text) and first.text == "one"


# -- B8: what {{term:}} shows ------------------------------------------------


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


# -- B8: a term that refers to itself ----------------------------------------

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
