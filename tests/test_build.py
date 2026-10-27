"""Building the tree from the validator's document: what is kept of a
comment-only list item or quote (§8.6), how emphasis next to a directive
parses, and the spacing where an inline comment was dropped."""
from __future__ import annotations

import html as html_escape
import unicodedata

import pytest
from conftest import FRONT, html, text
from legaldown import parse, validate
from markdown_it import MarkdownIt
from markdown_it.common.utils import isPunctChar

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


def built(body: str):
    document = parse(FRONT + body)
    return build_tree(document, validate(document))


def blocks_of(body: str):
    return built(body).sections[0].blocks


# -- Comment-only list items and quotes ------------------------------------------

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


def test_a_comment_only_item_with_a_marker_is_kept() -> None:
    # Defensive: an item a marker belongs to never vanishes. The
    # validator does not place a marker on a comment-only item, so a marker
    # is made up here to drive the builder's guard.
    from legaldown import PlacedMarker

    from legaldown_render.build import _Builder

    document = parse(FRONT + "# A\n\n- <!-- c -->\n- b\n")
    result = validate(document)
    marker = PlacedMarker(section=0, block=0, fragment=0, offset=0, source="<!-- c -->",
                          identifier="x", condition="", item=0)
    out = _Builder(document, result).list(document.sections[0].blocks[0], [marker])
    assert out is not None and [item.anchor_id for item in out.items] == ["x", ""]
    plain = _Builder(document, result).list(document.sections[0].blocks[0], [])
    assert plain is not None and len(plain.items) == 1


# -- Emphasis flanking next to a directive ---------------------------------------

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


# -- Spacing where an inline comment was dropped ---------------------------------

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
