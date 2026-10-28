"""The DOCX plan (docs/architecture.md, "The DOCX planner"): which abstractNums and nums a document needs,
which (numId, ilvl) each section, list item and numbered paragraph takes, and that Word, simulated, numbers every unit
as the resolver did, or a diagnostic says why not; and, from the numbering, the fields of every cross-reference, the
bookmarks and the table of contents. Everything is checked on the plan's data; no file is written (the LibreOffice
round trip is in test_docx_roundtrip.py)."""
from __future__ import annotations

import functools
import random
import re
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from conftest import DOCUMENTS, FRONT, spec_root

from legaldown_render import DocumentError, render
from legaldown_render.errors import StyleError
from legaldown_render.resolve.docx_bookmarks import BookmarkNames
from legaldown_render.resolve.docx_cover import Field, Title, cover, suppressed, uncovered
from legaldown_render.resolve.docx_numbering import NUM_FMT, Count, Ghost, Lit, word_counter
from legaldown_render.resolve.docx_plan import (
    DocxPlan,
    FieldSeg,
    Literal_,
    Segment,
    docx_style_errors,
    plan_docx,
)
from legaldown_render.resolve.numbering import format_counter, formats_of
from legaldown_render.style import load_style
from legaldown_render.style.docx import docx_settings
from legaldown_render.tree import (
    CrossRef,
    DraftingNote,
    List,
    ListItem,
    Paragraph,
    Quote,
    RenderTree,
    Section,
    iter_tree_inlines,
)

NONE = {"numbering.scheme": "none"}
PARAGRAPHS = {"paragraphs.numbered": True}
ALTERNATIVES = FRONT.replace("title: T", (
    "title: T\nquestions:\n  forum:\n    type: choice\n    choices:\n      courts: Courts\n      arb: Arbitration"))

#: The styles every document is planned under: the built-in ones, and the overrides that change which lists exist
#: and which labels need the section.
VARIANTS: dict[str, dict] = {
    "default": {},
    "outline": {"style": "outline"},
    "continental": {"style": "continental"},
    "mixed": {"overrides": {"numbering.scheme": "mixed"}},
    "none": {"overrides": NONE},
    "paragraphs": {"overrides": PARAGRAPHS},
    "continental-none": {"style": "continental", "overrides": NONE},
    "enumerate-ordered": {"overrides": {"enumeration.ordered": "enumerate"}},
    "bullets": {"overrides": {"enumeration.enabled": False}},
    # labels with words and symbols, a label that shows the path of an item, and a paragraph number with a symbol
    "article": {"overrides": {"numbering.levels": [
        {"counter": "upper-roman", "label": "Article {n}", "ref": "{n}"},
        {"counter": "decimal", "label": "{path}", "ref": ".{n}"},
        {"counter": "lower-alpha", "label": "({n})", "ref": "({n})"}]}},
    "section-sign": {"overrides": {"enumeration.levels": [
        {"counter": "decimal", "label": "\u00a7 {n}", "ref": "{n}"},
        {"counter": "lower-alpha", "label": "{n})", "ref": "({n})"},
        {"counter": "lower-roman", "label": "({n})", "ref": "({n})"}]}},
    "path-items": {"overrides": {"enumeration.levels": [
        {"counter": "lower-alpha", "label": "({n})", "ref": "({n})"},
        {"counter": "decimal", "label": "{path}", "ref": "({n})"},
        {"counter": "lower-roman", "label": "{n}.", "ref": "({n})"}]}},
    "path-items-continental": {"style": "continental", "overrides": {"enumeration.levels": [
        {"counter": "lower-alpha", "label": "({n})", "ref": "({n})"},
        {"counter": "decimal", "label": "{path}", "ref": "({n})"},
        {"counter": "lower-roman", "label": "{n}.", "ref": "({n})"}]}},
    "paragraph-sign": {"overrides": {"paragraphs.numbered": True, "paragraphs.format": {
        "counter": "decimal", "label": "\u00b6 {n}", "ref": "{n}"}}},
}


def planned(body: str, front: str = FRONT, **settings: object):  # noqa: ANN201
    result = render(front + body, format="text", **settings)
    plan, diagnostics = plan_docx(result.tree)
    return result.tree, plan, diagnostics


def rules(diagnostics: list) -> set[str]:  # noqa: ANN001
    return {d.rule for d in diagnostics}


# -- A walk of the units ----------------------------------------------------------


def _blocks(blocks: tuple) -> Iterator[Section | ListItem | Paragraph]:
    for block in blocks:
        match block:
            case Paragraph() if block.unit is not None:
                yield block
            case List():
                for item in block.items:
                    yield item
                    yield from _blocks(item.blocks)
            case Quote(blocks=inner) | DraftingNote(blocks=inner):
                yield from _blocks(inner)


def units(tree: RenderTree) -> list[Section | ListItem | Paragraph]:
    found = list(_blocks(tree.preamble))
    for section in tree.sections:
        found.append(section)
        found.extend(_blocks(section.blocks))
    return found


def lists(tree: RenderTree) -> list[List]:
    found: list[List] = []

    def walk(blocks: tuple) -> None:
        for block in blocks:
            match block:
                case List():
                    found.append(block)
                    for item in block.items:
                        walk(item.blocks)
                case Quote(blocks=inner) | DraftingNote(blocks=inner):
                    walk(inner)

    walk(tree.preamble)
    for section in tree.sections:
        walk(section.blocks)
    return found


def items(tree: RenderTree) -> list[ListItem]:
    """Every list item, in document order (unit ids also count paragraphs, so tests ask for items by position)."""
    return [u for u in units(tree) if isinstance(u, ListItem)]


def shown(plan: DocxPlan, *nodes: ListItem) -> list[tuple]:
    """(numId, ilvl, label) of each node."""
    return [(plan.units[n.unit].num_id, plan.units[n.unit].ilvl, plan.units[n.unit].label) for n in nodes]


# -- The abstractNums ---------------------------------------------------------------


def test_the_default_style_needs_a_heading_a_list_and_a_renumber_abstract_num() -> None:
    _, plan, diagnostics = planned("# A\n\n- x\n\n1. y\n")
    assert [(a.id, a.kind, a.nsid, a.multi_level) for a in plan.abstract_nums] == [
        (0, "headings", "4C440000", "multilevel"),
        (1, "enumerated", "4C440001", "hybridMultilevel"),
        (2, "renumber", "4C440002", "hybridMultilevel")]
    assert all(len(a.levels) == 9 for a in plan.abstract_nums)
    assert plan.heading_num_id == 1 and plan.nums[0].id == 1 and plan.nums[0].start_overrides == ()
    assert diagnostics == []


def test_abstract_nums_are_made_in_the_order_of_use_and_only_when_used() -> None:
    _, plan, _ = planned("# A\n\n1. y\n\n- x\n")
    assert [a.kind for a in plan.abstract_nums] == ["headings", "renumber", "enumerated"]
    _, plan, _ = planned("Text.\n")
    assert [a.kind for a in plan.abstract_nums] == ["headings"]


def test_heading_levels_follow_the_scheme() -> None:
    def texts(**settings: object) -> list[tuple[str, str]]:
        _, plan, _ = planned("# A\n", **settings)
        return [(level.num_fmt, level.lvl_text) for level in plan.abstract_nums[0].levels[:5]]

    assert texts() == [("decimal", "%1."), ("decimal", "%1.%2"), ("decimal", "%1.%2.%3"),
                       ("decimal", "%1.%2.%3.%4"), ("decimal", "%1.%2.%3.%4.%5")]
    assert texts(style="outline") == [("upperRoman", "%1."), ("upperLetter", "%2."), ("decimal", "%3."),
                                      ("lowerLetter", "%4."), ("lowerRoman", "(%5)")]
    assert texts(overrides={"numbering.scheme": "mixed"}) == [
        ("decimal", "%1."), ("lowerLetter", "(%2)"), ("lowerRoman", "(%3)"), ("upperLetter", "(%4)"),
        ("decimal", "(%5)")]
    assert texts(style="continental")[:2] == [("decimal", "%1."), ("decimal", "%1.%2")]


def test_heading_levels_are_linked_to_the_heading_styles_and_unused_levels_are_neutral() -> None:
    _, plan, _ = planned("# A\n")
    levels = plan.abstract_nums[0].levels
    assert [level.p_style for level in levels] == [f"Heading{k}" for k in range(1, 6)] + [None] * 4
    assert [(level.num_fmt, level.lvl_text) for level in levels[5:]] == [("decimal", f"%{k}") for k in range(6, 10)]


def test_a_custom_heading_label_is_a_level_text_and_a_word_label_is_followed_by_a_space() -> None:
    levels = [{"counter": "upper-roman", "label": "Article {n}", "ref": "{n}"}]
    _, plan, _ = planned("# A\n\n## B\n", overrides={"numbering.levels": levels})
    first, second = plan.abstract_nums[0].levels[:2]
    assert (first.num_fmt, first.lvl_text, first.suff) == ("upperRoman", "Article %1", "space")
    assert (second.lvl_text, second.suff) == ("%1.%2", "tab")
    assert plan.units["s1"].label == "Article I"


def test_level_indents_step_by_level_and_headings_are_as_wide_as_their_widest_label() -> None:
    _, plan, _ = planned("# A\n\n- x\n")
    headings, items = plan.abstract_nums[0].levels, plan.abstract_nums[1].levels
    assert [(level.left, level.hanging) for level in items[:3]] == [(567, 567), (1134, 567), (1701, 567)]
    assert headings[0].left == headings[0].hanging == 567
    assert headings[4].left == headings[4].hanging > headings[1].left > headings[0].left


def test_the_none_scheme_has_no_heading_abstract_num_and_sections_are_unnumbered() -> None:
    tree, plan, _ = planned("# A\n\n- x\n", overrides=NONE)
    assert plan.heading_num_id is None
    assert [a.kind for a in plan.abstract_nums] == ["enumerated"]
    assert plan.units["s1"].num_id is None and plan.units["s1"].explicit is False and plan.units["s1"].count is None
    assert plan.nums[0].abstract_id == 0


# -- Headings ------------------------------------------------------------------------


def test_headings_take_the_style_num_at_their_level() -> None:
    tree, plan, _ = planned("# A\n\n## B\n\n### C\n\n# D\n")
    assert [(u.num_id, u.ilvl, u.explicit, u.label, u.word_ref, u.count) for u in map(plan.units.get, ("s1", "s2", "s3", "s4"))] == [
        (1, 0, False, "1.", "1", 1), (1, 1, False, "1.1", "1.1", 1), (1, 2, False, "1.1.1", "1.1.1", 1),
        (1, 0, False, "2.", "2", 2)]


def test_a_skipped_heading_level_is_numbered_as_the_resolver_does() -> None:
    # heading-skip is a Core Error of the document: the plan says nothing more, and notes the cause on the unit.
    tree, plan, diagnostics = planned("# A\n\n### B\n\n## C\n")
    assert [plan.units[s].label for s in ("s1", "s2", "s3")] == ["1.", "1.1.1", "1.2"]
    assert plan.units["s2"].cause == ("skipped-level",) and plan.units["s3"].cause == ()
    assert diagnostics == []


def test_a_document_that_starts_below_level_one_skips_nothing() -> None:
    _, plan, diagnostics = planned("## A\n\n## B\n")
    assert [plan.units[s].label for s in ("s1", "s2")] == ["1.", "2."] and diagnostics == []


# -- Lists ---------------------------------------------------------------------------

NESTED = """# A

- one
  - nested a
  - nested b
    - deep
- two
- three

1. first
1. second
"""


def test_every_top_level_list_has_a_num_that_starts_it_at_one() -> None:
    tree, plan, _ = planned(NESTED)
    items = [u for u in units(tree) if isinstance(u, ListItem)]
    first_list, ordered = plan.units[items[0].unit], plan.units[items[-2].unit]
    assert (first_list.num_id, first_list.ilvl, first_list.explicit, first_list.label) == (2, 0, True, "(a)")
    assert (ordered.num_id, ordered.ilvl, ordered.label) == (3, 0, "1.")
    assert plan.nums[1].start_overrides == ((0, 1),)
    assert plan.nums[2].abstract_id == 2


def test_two_lists_in_a_section_restart_each_at_its_own_num() -> None:
    tree, plan, _ = planned("# A\n\n- x\n- y\n\nText.\n\n- z\n")
    assert [(n, label) for n, _, label in shown(plan, *items(tree))] == [(2, "(a)"), (2, "(b)"), (3, "(a)")]


def test_a_nested_list_of_the_same_kind_uses_a_deeper_level_of_the_parents_num() -> None:
    tree, plan, diagnostics = planned(NESTED)
    assert shown(plan, *items(tree)[:6]) == [
        (2, 0, "(a)"), (2, 1, "(i)"), (2, 1, "(ii)"), (2, 2, "(A)"), (2, 0, "(b)"), (2, 0, "(c)")]
    assert len(plan.nums) == 3 and diagnostics == []


def test_a_list_in_a_new_item_restarts_by_structure_and_needs_no_override() -> None:
    tree, plan, _ = planned("# A\n\n- a\n  - x\n- b\n  - y\n")
    _, x, _, y = items(tree)
    assert shown(plan, x, y) == [(2, 1, "(i)"), (2, 1, "(i)")] and plan.units[y.unit].cause == ()


def test_sibling_lists_in_one_item_share_the_num_so_that_the_second_continues() -> None:
    body = "# A\n\n- a\n  - x\n  - y\n\n  Between.\n\n  - z\n"
    tree, plan, diagnostics = planned(body)
    nested = items(tree)[1:]
    assert [i.number for i in nested] == [1, 2, 3]  # the resolver goes on with the second list
    assert shown(plan, *nested) == [(2, 1, "(i)"), (2, 1, "(ii)"), (2, 1, "(iii)")]
    assert len(plan.nums) == 2 and diagnostics == []


def test_a_sibling_list_of_another_kind_that_continues_gets_a_num_starting_at_its_number() -> None:
    # The nested ordered list and the second nested ordered list share one counter in the resolver: the second
    # list's first item is 3. Another kind of list in between puts them on different nums.
    body = "# A\n\n- a\n  1. x\n  1. y\n\n  Between.\n\n  - w\n\n  1. z\n"
    tree, plan, diagnostics = planned(body)
    ordered = [u for u in units(tree) if isinstance(u, ListItem) and u.label and u.label.endswith(".")]
    assert [item.number for item in ordered] == [1, 2, 3]
    assert [plan.units[item.unit].label for item in ordered] == ["1.", "2.", "3."]
    # Another kind of list in between: the second ordered list cannot go on from the first by structure, so its num
    # starts at its number, 3: a fixed start that will not follow items added before it, which is a warning (the
    # first ordered list restarts at 1, which is information only).
    last = plan.units[ordered[-1].unit]
    assert last.num_id != plan.units[ordered[0].unit].num_id and last.cause == ("resync",)
    assert plan.nums[last.num_id - 1].start_overrides == ((1, 3),)
    assert [(d.rule, d.level) for d in diagnostics] == [
        ("render-docx-static-restart", "info"), ("render-docx-shared-counter", "warning")]


def test_a_nested_list_of_another_kind_has_a_num_of_its_own_and_is_reported_as_information() -> None:
    tree, plan, diagnostics = planned("# A\n\n- a\n  1. x\n  1. y\n- b\n  1. z\n")
    _, x, y, _, z = items(tree)
    assert shown(plan, x, y, z) == [(3, 1, "1."), (3, 1, "2."), (4, 1, "1.")]
    assert plan.units[x.unit].cause == ("restart-family",)
    assert plan.nums[2].start_overrides == ((1, 1),) and plan.nums[2].abstract_id == 2
    assert [(d.rule, d.level) for d in diagnostics] == [("render-docx-static-restart", "info")]
    assert diagnostics[0].line == tree.sections[0].blocks[0].items[0].blocks[1].items[0].line


def test_a_list_in_a_quote_inside_an_item_is_numbered_from_the_section_with_a_num_of_its_own() -> None:
    body = "# A\n\n- a\n  - x\n\n  > - quoted\n  > - quoted two\n\n  - y\n- b\n"
    tree, plan, diagnostics = planned(body)
    _, x, quoted, quoted_too, y, _ = items(tree)
    assert [quoted.parent_unit, quoted_too.parent_unit] == ["s1", "s1"]  # built on the section, not on the item
    # a num of its own, at the level its depth gives: fresh counter, as the resolver has
    assert shown(plan, quoted, quoted_too) == [(3, 1, "(i)"), (3, 1, "(ii)")]
    # The list after the quote goes on from the first nested list (the resolver counts them as siblings), but
    # Word's counter of that level was used by the quote's list: it starts again where the resolver is, by a
    # fixed setting.
    assert shown(plan, x, y) == [(2, 1, "(i)"), (4, 1, "(ii)")]
    assert plan.nums[3].start_overrides == ((1, 2),) and plan.units[y.unit].cause == ("resync",)
    assert [(d.rule, d.level) for d in diagnostics] == [("render-docx-shared-counter", "warning")]


def test_bullets_count_nothing_so_one_num_serves_every_bullet_list() -> None:
    body = "# A\n\n- a\n  - b\n\n1. o\n   - c\n\n# B\n\n- d\n"
    tree, plan, diagnostics = planned(body, overrides={"enumeration.enabled": False})
    first, second, _, third, fourth = items(tree)
    assert [a.kind for a in plan.abstract_nums] == ["headings", "bullet", "renumber"]
    bullet = plan.abstract_nums[1]
    assert shown(plan, first, second, third, fourth)[:2] == [(2, 0, ""), (2, 1, "")]
    assert {plan.units[n.unit].num_id for n in (first, second, third, fourth)} == {2}
    assert all(num.start_overrides == () for num in plan.nums if num.abstract_id == bullet.id)
    assert plan.units[first.unit].count is None
    assert [level.lvl_text for level in bullet.levels[:4]] == ["\u2022", "\u2013", "\u25e6", "\u2022"]
    assert diagnostics == []  # bullets under an ordered list need no restart and no report


def test_ordered_lists_that_are_enumerated_take_the_enumeration_levels() -> None:
    _, plan, _ = planned("# A\n\n1. x\n   1. y\n", overrides={"enumeration.ordered": "enumerate"})
    assert [a.kind for a in plan.abstract_nums] == ["headings", "enumerated"]
    assert [plan.units[u].label for u in ("u1", "u2")] == ["(a)", "(i)"]


def test_an_item_in_the_preamble_is_numbered_without_a_section() -> None:
    _, plan, _ = planned("- a\n- b\n\n# A\n", style="continental")
    assert [(plan.units[u].label, plan.units[u].num_id) for u in ("u1", "u2")] == [("1", 2), ("2", 2)]


def test_items_in_a_quote_or_a_drafting_note_are_numbered_like_any_item() -> None:
    tree, plan, _ = planned("# A\n\n> - quoted\n\n> [!DRAFTING]\n> - noted\n")
    assert [(n, label) for n, _, label in shown(plan, *items(tree))] == [(2, "(a)"), (3, "(a)")]


def test_a_list_deeper_than_word_has_levels_is_clamped_and_reported_once() -> None:
    body = "# A\n\n" + "".join("  " * i + f"- level {i}\n" for i in range(11))
    tree, plan, diagnostics = planned(body)
    deep = items(tree)
    assert [plan.units[i.unit].ilvl for i in deep] == [0, 1, 2, 3, 4, 5, 6, 7, 8, 8, 8]
    assert [plan.units[i.unit].cause for i in deep[8:]] == [(), ("clamped",), ("clamped",)]
    # each clamped list restarts on a num of its own: Word's level 9 would count on otherwise
    assert len({plan.units[i.unit].num_id for i in deep[8:]}) == 3
    assert all(plan.units[i.unit].count == 1 for i in deep[8:])
    [found] = [d for d in diagnostics if d.rule == "render-docx-list-depth"]
    assert found.level == "warning" and found.line == deep[9].line and "9" in found.message
    assert rules(diagnostics) == {"render-docx-list-depth"}


def test_items_of_lists_past_level_nine_keep_their_numbers_and_a_sibling_at_level_nine_is_restarted() -> None:
    body = "# A\n\n" + "".join("  " * i + f"- level {i}\n" for i in range(9))
    body += "".join("  " * 9 + f"- nine {k}\n" for k in "abc")
    body += "".join("  " * 10 + f"- ten {k}\n" for k in "ab")
    body += "  " * 8 + "- level 8 again\n"
    tree, plan, diagnostics = planned(body)
    found = items(tree)
    nine, ten, again = found[9:12], found[12:14], found[14]
    # every item of a clamped list is Word's level 9 in the format of level 9, with the resolver's count
    assert [plan.units[i.unit].cause for i in (*nine, *ten)] == [("clamped",)] * 5
    assert [(plan.units[i.unit].ilvl, plan.units[i.unit].count) for i in (*nine, *ten)] == [
        (8, 1), (8, 2), (8, 3), (8, 1), (8, 2)]
    assert [plan.units[i.unit].label for i in (*nine, *ten)] == ["(A)", "(B)", "(C)", "(A)", "(B)"]
    assert [i.label for i in (*nine, *ten)] == ["(a)", "(b)", "(c)", "(i)", "(ii)"]  # the resolver's cycle
    assert len({plan.units[i.unit].num_id for i in (nine[0], ten[0])}) == 2
    # Word's counter of level 9 was used by the clamped lists: the item after them is restarted where it is
    again_plan = plan.units[again.unit]
    assert (again.number, again_plan.count, again_plan.cause) == (2, 2, ("resync",))
    assert plan.nums[again_plan.num_id - 1].start_overrides == ((8, 2),)
    assert rules(diagnostics) == {"render-docx-list-depth", "render-docx-shared-counter"}
    assert all(d.level == "warning" for d in diagnostics)


def test_a_list_that_goes_on_from_a_sibling_with_a_fixed_start_is_a_warning_not_a_restart() -> None:
    _, _, diagnostics = planned("# A\n\n- a\n  1. x\n\n  Between.\n\n  - w\n\n  1. z\n")
    assert [(d.rule, d.level) for d in diagnostics] == [
        ("render-docx-static-restart", "info"), ("render-docx-shared-counter", "warning")]
    assert "Restart at 1" not in diagnostics[1].message


# -- Paragraphs ----------------------------------------------------------------------


def test_numbered_paragraphs_have_a_num_per_section_that_starts_at_one() -> None:
    body = "# A\n\nOne.\n\nTwo.\n\n# B\n\nThree.\n"
    tree, plan, diagnostics = planned(body, style="continental")
    paragraphs = [u for u in units(tree) if isinstance(u, Paragraph)]
    assert [(plan.units[p.unit].num_id, plan.units[p.unit].ilvl, plan.units[p.unit].label) for p in paragraphs] == [
        (2, 0, "(1)"), (2, 0, "(2)"), (3, 0, "(1)")]
    assert plan.abstract_nums[1].kind == "paragraphs" and plan.abstract_nums[1].levels[0].lvl_text == "(%1)"
    assert diagnostics == []


def test_paragraphs_are_not_numbered_unless_the_style_numbers_them() -> None:
    _, plan, _ = planned("# A\n\nOne.\n")
    assert plan.units["u1"].num_id is None and plan.units["u1"].count is None and plan.units["u1"].label == ""


# -- Labels that need the section -----------------------------------------------------


def test_a_label_that_needs_the_sections_number_is_written_without_it_and_reported() -> None:
    body = "# A\n\n- x\n- y\n\n## B\n\n- z\n"
    tree, plan, diagnostics = planned(body, style="continental")
    assert [plan.units[i.unit].label for i in items(tree)] == ["1", "2", "1"]  # the resolver wrote 1.1, 1.2, 1.1.1
    assert [u.label for u in units(tree) if isinstance(u, ListItem)] == ["1.1", "1.2", "1.1.1"]
    assert plan.units[items(tree)[0].unit].cause == ("label-fallback",)
    [found] = [d for d in diagnostics if d.rule == "render-docx-section-label"]
    assert found.level == "warning" and "{section}.{n}" in found.message and "'1'" in found.message
    assert found.line == tree.sections[0].blocks[0].items[0].line


def test_paragraph_numbers_that_need_the_section_are_reported_too() -> None:
    tree, plan, diagnostics = planned("# A\n\nOne.\n", overrides=PARAGRAPHS)
    [paragraph] = [u for u in units(tree) if isinstance(u, Paragraph)]
    assert plan.units[paragraph.unit].label == "1" and plan.units[paragraph.unit].cause == ("label-fallback",)
    assert rules(diagnostics) == {"render-docx-section-label"}


def test_a_preamble_item_needs_no_section_and_is_not_reported() -> None:
    _, plan, diagnostics = planned("- x\n\n# A\n", style="continental")
    assert plan.units["u1"].label == "1" and diagnostics == []


# -- Counters Word writes differently --------------------------------------------------


def _items(n: int) -> str:
    return "# A\n\n" + "".join(f"- item {i}\n" for i in range(1, n + 1))


def test_letters_past_twenty_six_are_reported_once_for_the_list() -> None:
    tree, plan, diagnostics = planned(_items(60))
    items = [u for u in units(tree) if isinstance(u, ListItem)]
    assert plan.units[items[26].unit].label == "(aa)" and plan.units[items[27].unit].label == "(bb)"
    assert items[27].label == "(ab)"
    [found] = diagnostics
    assert (found.rule, found.level, found.line) == ("render-docx-letter-overflow", "warning", items[27].line)
    assert "'ab'" in found.message and "'bb'" in found.message
    assert plan.units[items[27].unit].count == 28


def test_an_overflow_that_reads_the_same_in_two_lists_is_one_finding_at_the_first() -> None:
    tree, _, diagnostics = planned(_items(30) + "\nText.\n\n" + _items(30)[5:])
    [found] = diagnostics
    assert found.rule == "render-docx-letter-overflow" and found.line == items(tree)[27].line


def test_roman_numerals_from_4000_are_reported() -> None:
    levels = [{"counter": "upper-roman", "label": "{n}.", "ref": "{n}"}]
    body = "".join(f"# H{i}\n\n" for i in range(1, 4))
    tree, plan, diagnostics = planned(body, overrides={"numbering.levels": levels})
    assert diagnostics == []
    levels = [{"counter": "lower-roman", "label": "({n})", "ref": "({n})"}] * 3
    many = "".join(f"- i{i}\n" for i in range(1, 4002))
    tree, plan, diagnostics = planned("# A\n\n" + many, overrides={"enumeration.levels": levels})
    items = [u for u in units(tree) if isinstance(u, ListItem)]
    assert (items[3998].label, plan.units[items[3998].unit].label) == ("(mmmcmxcix)", "(mmmcmxcix)")
    assert (items[3999].label, plan.units[items[3999].unit].label) == ("(4000)", "(mmmm)")
    [found] = [d for d in diagnostics if d.rule == "render-docx-roman-overflow"]
    assert found.line == items[3999].line and "'4000'" in found.message and "'mmmm'" in found.message


# -- Alternatives: planned in a later step; today Word's numbers differ and the finding says so ---------


def test_an_alternative_that_shares_a_number_is_reported_while_it_is_numbered_in_sequence() -> None:
    body = "# Disputes {#d when=forum:courts}\n\n# Disputes {#d when=forum:arb}\n\n# Last\n"
    tree, plan, diagnostics = planned(body, ALTERNATIVES)
    assert tree.sections[1].shares_number_with == "s1"
    assert [plan.units[s].label for s in ("s1", "s2", "s3")] == ["1.", "2.", "3."]
    # the second section and the one after it are off by the same amount: one finding
    [found] = diagnostics
    assert (found.rule, found.line) == ("render-docx-number-mismatch", tree.sections[1].line)
    assert "'1.'" in found.message and "'2.'" in found.message and "the following 1" in found.message


# -- Diagnostics: lines, files, order, repeats --------------------------------------------


def test_diagnostics_name_the_file_and_the_line_and_come_in_line_order() -> None:
    result = render(FRONT + "# A\n\n- x\n  1. y\n\n- p\n- q\n" + "- r\n" * 30, format="text")
    plan, diagnostics = plan_docx(result.tree, file="deal.lgd")
    assert [(d.rule, d.file) for d in diagnostics] == [
        ("render-docx-static-restart", "deal.lgd"), ("render-docx-letter-overflow", "deal.lgd")]
    lines = [d.line for d in diagnostics]
    assert lines == sorted(lines) and None not in lines
    _, anonymous = plan_docx(result.tree)
    assert {d.file for d in anonymous} == {""}


def test_the_same_finding_is_reported_once() -> None:
    _, _, diagnostics = planned("# A\n\n- a\n  1. x\n- b\n  1. y\n- c\n  1. z\n")
    assert [d.rule for d in diagnostics] == ["render-docx-static-restart"]


def test_untrusted_text_in_a_message_is_escaped() -> None:
    overrides = {"enumeration.levels": [{"counter": "lower-alpha", "label": "{section}\u0007{n}", "ref": "({n})"}]}
    _, _, diagnostics = planned("# A\n\n- x\n", overrides=overrides)
    [found] = diagnostics
    assert found.rule == "render-docx-section-label" and "\u0007" not in found.message


# -- Determinism and the plan's data -------------------------------------------------------


def test_the_same_tree_gives_an_equal_plan_and_the_plan_is_frozen() -> None:
    result = render(FRONT + NESTED, format="text", style="continental")
    first = plan_docx(result.tree)
    again = plan_docx(result.tree)
    assert first[0] == again[0] and first[1] == again[1]
    with pytest.raises(AttributeError):
        first[0].heading_num_id = 5  # type: ignore[misc]
    assert list(first[0].units) == [u.unit for u in units(result.tree)]
    assert first[0].refs == {} and first[0].toc == () and first[0].anchors == {"a": "ld_a"}


def test_unit_plans_carry_the_whole_plan_for_a_unit() -> None:
    _, plan, _ = planned("# A\n\n- x\n")
    unit = plan.units["u1"]
    assert (unit.kind, unit.ind, unit.alt, unit.bookmarks, unit.toc_bookmark) == ("item", None, None, (), None)
    assert plan.units["s1"].kind == "section"


# -- Style checks (ADR 0006) ----------------------------------------------------------------


def test_formats_of_a_style_are_the_formats_in_the_tree() -> None:
    for settings in VARIANTS.values():
        result = render(FRONT + "# A\n", format="text", **settings)
        assert formats_of(result.style) == result.tree.formats


def test_the_built_in_styles_are_fine_for_docx() -> None:
    for name in ("default", "outline", "continental"):
        assert docx_style_errors(load_style(name)) == []


@pytest.mark.parametrize(("key", "value"), [
    ("numbering.levels", [{"counter": "decimal", "label": "%1 {n}", "ref": "{n}"}]),
    ("numbering.levels", [{"counter": "decimal", "label": "%{n}", "ref": "{n}"}]),
    ("numbering.levels", [{"counter": "decimal", "label": "{n}", "ref": "%2{n}"}]),
    ("enumeration.levels", [{"counter": "decimal", "label": "{n}%9", "ref": "{n}"}]),
    ("paragraphs.format", {"counter": "decimal", "label": "%3{n}", "ref": "{n}"}),
])
def test_a_percent_that_lvl_text_cannot_write_is_a_style_error(key: str, value: object) -> None:
    overrides = {key: value, "paragraphs.numbered": True}
    style = load_style(overrides=overrides)
    problems = docx_style_errors(style)
    assert len(problems) == 1 and key.split(".")[0] in problems[0] and "'%'" in problems[0]
    with pytest.raises(StyleError) as caught:  # the DOCX settings step refuses it before anything is rendered
        docx_settings(style)
    assert caught.value.problems == problems


@pytest.mark.parametrize(("levels", "setting"), [
    # a ref ending in '%' meets the counter that starts the next level's ref once the label is put together
    ([{"counter": "decimal", "label": "{path}", "ref": "{n}%"}, {"counter": "decimal", "label": "{path}", "ref": "{n}"}],
     "numbering.levels"),
    ([{"counter": "decimal", "label": "{path}", "ref": "{n}%"}, {"counter": "decimal", "label": "{path}", "ref": "5{n}"}],
     "numbering.levels"),
])
def test_a_percent_that_meets_a_counter_where_the_label_is_put_together_is_a_style_error(
        levels: list[dict], setting: str) -> None:
    style = load_style(overrides={"numbering.levels": levels})
    [problem] = docx_style_errors(style)
    assert problem.startswith(setting) and "put together" in problem
    with pytest.raises(StyleError):
        docx_settings(style)


def test_a_percent_that_cannot_be_mistaken_is_fine() -> None:
    levels = [{"counter": "decimal", "label": "{n}% of", "ref": "{n}"}]
    assert docx_style_errors(load_style(overrides={"numbering.levels": levels})) == []
    tree, plan, _ = planned("# A\n", overrides={"numbering.levels": levels})
    assert plan.abstract_nums[0].levels[0].lvl_text == "%1% of"


def test_settings_that_are_off_are_not_checked() -> None:
    overrides = {"enumeration.enabled": False, "enumeration.levels": [
        {"counter": "decimal", "label": "%1{n}", "ref": "{n}"}]}
    assert docx_style_errors(load_style(overrides=overrides)) == []
    assert docx_style_errors(load_style(overrides={"numbering.scheme": "none"})) == []


def test_a_level_text_longer_than_word_allows_is_a_style_error() -> None:
    long = "x" * 260
    style = load_style(overrides={"numbering.levels": [{"counter": "decimal", "label": long + "{n}", "ref": "{n}"}]})
    [problem] = docx_style_errors(style)
    assert problem.startswith("numbering.levels") and "255" in problem
    with pytest.raises(StyleError):
        docx_settings(style)


# -- Labels that show what Word cannot, and runs of units that Word counts off ------------------------


def test_what_a_label_shows_is_judged_on_the_filled_label_not_on_its_spelling() -> None:
    # `{path}` of an item in a section starts from the section's number: it shows it. In the preamble it does not.
    levels = [{"counter": "lower-alpha", "label": "({n})", "ref": "({n})"},
              {"counter": "decimal", "label": "{path}", "ref": "({n})"}]
    tree, plan, diagnostics = planned("# A\n\n- a\n  - x\n  - y\n- b\n", overrides={"enumeration.levels": levels})
    first, nested, _, _ = items(tree)[:4]
    assert plan.units[first.unit].cause == () and plan.units[nested.unit].cause == ("label-fallback",)
    [found] = diagnostics
    # the reading is the one at the list's own depth: the level-2 text without the section ("(%1)(%2)" has only
    # the list's own counters), not the level-1 text
    assert found.rule == "render-docx-section-label" and "'(a)(1)'" in found.message
    tree, plan, diagnostics = planned("- a\n  - x\n\n# A\n", overrides={"enumeration.levels": levels})
    assert diagnostics == [] and plan.units[items(tree)[1].unit].cause == ()


def test_a_label_that_needs_a_level_nothing_numbers_is_not_left_to_word() -> None:
    # An ordered list's items are numbered by another abstractNum than the enumerated item nested in one: the level
    # above the nested item is never used, which Word shows as 0, not 1.
    levels = [{"counter": "lower-alpha", "label": "({n})", "ref": "({n})"},
              {"counter": "decimal", "label": "{path}", "ref": "({n})"}]
    tree, plan, diagnostics = planned("1. a\n   - x\n\n# A\n", overrides={"enumeration.levels": levels})
    _, nested = items(tree)
    assert plan.units[nested.unit].cause == ("restart-family", "label-fallback")
    assert plan.units[nested.unit].label == "()(1)"  # the level above reads as nothing, not as "(a)"
    assert {d.rule for d in diagnostics} == {"render-docx-static-restart", "render-docx-section-label"}


def test_a_run_of_units_counted_off_by_the_same_amount_is_one_finding() -> None:
    body = "".join(f"# S{i}\n\n- a\n- b\n\n" for i in range(4))
    body += "# D {#d when=forum:courts}\n\n- x\n\n# D {#d when=forum:arb}\n\n- y\n\n"
    body += "".join(f"# T{i}\n\n- a\n\n## sub\n\n- q\n\n" for i in range(10))
    tree, plan, diagnostics = planned(body, ALTERNATIVES)
    [found] = diagnostics
    assert found.rule == "render-docx-number-mismatch" and found.line == tree.sections[5].line
    assert "1 later than the renderer" in found.message and "the following 10" in found.message


def test_a_different_difference_is_a_finding_of_its_own() -> None:
    body = ("# D {#d when=forum:courts}\n\n# D {#d when=forum:arb}\n\n# Mid\n\n"
            "# E {#e when=forum:courts}\n\n# E {#e when=forum:arb}\n\n# End\n")
    tree, _, diagnostics = planned(body, ALTERNATIVES)
    assert [(d.rule, d.line) for d in diagnostics] == [
        ("render-docx-number-mismatch", tree.sections[1].line), ("render-docx-number-mismatch", tree.sections[4].line)]
    assert "1 later" in diagnostics[0].message and "2 later" in diagnostics[1].message


# -- Word's counters against LibreOffice's, observed -----------------------------------------------------

#: What LibreOffice 24.2 writes for these values, from a document with one list per value (it follows the OOXML
#: definition, which Word documents too): letters repeat after 26, Roman numerals go on with `m` from 4000.
LIBREOFFICE_COUNTERS = {
    "lowerLetter": {1: "a", 2: "b", 25: "y", 26: "z", 27: "aa", 28: "bb", 29: "cc", 52: "zz", 53: "aaa", 54: "bbb",
                    55: "ccc", 78: "zzz", 79: "aaaa", 100: "vvvv"},
    "upperLetter": {27: "AA", 28: "BB", 52: "ZZ", 53: "AAA", 100: "VVVV"},
    "lowerRoman": {1: "i", 28: "xxviii", 79: "lxxix", 100: "c", 3999: "mmmcmxcix", 4000: "mmmm"},
    "upperRoman": {29: "XXIX", 78: "LXXVIII", 3999: "MMMCMXCIX", 4000: "MMMM"},
    "decimal": {1: "1", 26: "26", 4000: "4000"},
}


@pytest.mark.parametrize("num_fmt", LIBREOFFICE_COUNTERS)
def test_word_counters_are_the_ones_libreoffice_wrote(num_fmt: str) -> None:
    for value, written in LIBREOFFICE_COUNTERS[num_fmt].items():
        assert word_counter(value, num_fmt) == written


# -- Word numbers every unit as the resolver did, or a diagnostic says why not ---------------------

def numbered(tree: RenderTree) -> Iterator[tuple[Section | ListItem | Paragraph, str]]:
    """Every numbered unit with the counter the resolver wrote its number in."""
    formats = tree.formats

    def blocks(found: tuple) -> Iterator[tuple[Section | ListItem | Paragraph, str]]:
        for block in found:
            match block:
                case Paragraph() if block.number is not None and formats.paragraphs is not None:
                    yield block, formats.paragraphs.counter
                case List() if block.format is not None:
                    for item in block.items:
                        yield item, block.format.counter
                        yield from blocks(item.blocks)
                case List():
                    for item in block.items:
                        yield from blocks(item.blocks)
                case Quote(blocks=inner) | DraftingNote(blocks=inner):
                    yield from blocks(inner)

    yield from blocks(tree.preamble)
    for section in tree.sections:
        if section.number is not None:
            yield section, formats.headings[section.level - 1].counter
        yield from blocks(section.blocks)


def assert_planned_as_resolved(result) -> None:  # noqa: ANN001
    """The property, unit by unit: Word's count is the resolver's number, and Word's label is the resolver's label,
    unless that unit's own counter reads differently in Word (a letter past 26, a Roman numeral from 4000: its label
    and those of its descendants may differ), its label cannot show what the resolver's does (the plan says so on the
    unit), or its depth is past Word's. A document without alternatives has no other exception; a diagnostic is
    reported exactly where there is an exception."""
    tree = result.tree
    plan, diagnostics = plan_docx(tree)
    found = rules(diagnostics)
    shares = any(unit.shares_number_with for unit in units(tree))
    overflows: dict[str, str] = {}  # unit -> the rule its counter reads differently under (itself or an ancestor)
    parents = {unit.unit: unit.parent_unit for unit in units(tree)}
    count_differs = False
    own_rules: set[str] = set()
    for node, counter in numbered(tree):
        mine = plan.units[node.unit]
        clamped = "clamped" in mine.cause
        if not shares:
            assert mine.count == node.number, (node.unit, mine.count, node.number)
        count_differs = count_differs or mine.count != node.number
        own = ""
        if not clamped and word_counter(node.number, NUM_FMT[counter]) != format_counter(node.number, counter):
            own = "render-docx-roman-overflow" if counter.endswith("roman") else "render-docx-letter-overflow"
        inherited = overflows.get(parents[node.unit] or "", "") or ""
        overflows[node.unit] = own or inherited
        if not shares and not (own or inherited or clamped or "label-fallback" in mine.cause):
            assert mine.label == node.label, (node.unit, mine.label, node.label)
        for cause, rule in (("label-fallback", "render-docx-section-label"), ("clamped", "render-docx-list-depth")):
            if cause in mine.cause:
                assert rule in found, (node.unit, cause)
        own_rules.update([own] if own else [])
    assert found & {"render-docx-roman-overflow", "render-docx-letter-overflow"} == own_rules
    if shares:
        assert count_differs <= ("render-docx-number-mismatch" in found)
    else:
        assert "render-docx-number-mismatch" not in found, "a placement is wrong: " + "; ".join(
            d.message for d in diagnostics if d.rule == "render-docx-number-mismatch")
    assert all(d.rule.startswith("render-docx-") for d in diagnostics)
    assert {u.unit for u in units(tree)} == set(plan.units)
    # Every num with an override is used, by a unit whose level the override names.
    first: dict[int, str] = {}
    for unit in units(tree):
        mine = plan.units[unit.unit]
        if mine.num_id is not None:
            first.setdefault(mine.num_id, unit.unit)
    for num in plan.nums:
        assert num.id in first, f"num {num.id} is never used"
        if num.start_overrides:
            assert plan.units[first[num.id]].ilvl == num.start_overrides[0][0]


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("name", ["features.lgd", "template.lgd"])
def test_the_test_documents_are_planned_as_resolved(name: str, variant: str) -> None:
    source = (DOCUMENTS / name).read_text(encoding="utf-8")
    assert_planned_as_resolved(render(source, format="text", **VARIANTS[variant]))


SPEC = spec_root()


def _spec_documents() -> list[Path]:
    if SPEC is None:
        return []
    return sorted((SPEC / "examples").rglob("*.lgd")) + sorted((SPEC / "fixtures" / "valid").rglob("*.lgd"))


@pytest.mark.conformance
@pytest.mark.skipif(SPEC is None, reason="LegalDown specification checkout not found")
@pytest.mark.parametrize("variant", VARIANTS)
def test_the_specifications_documents_are_planned_as_resolved(variant: str) -> None:
    for path in _spec_documents():
        try:
            result = render(path.read_text(encoding="utf-8"), format="text", **VARIANTS[variant])
        except DocumentError:
            continue
        try:
            assert_planned_as_resolved(result)
        except AssertionError as error:
            raise AssertionError(f"{path.relative_to(SPEC)}: {error}") from error


# -- Random documents ---------------------------------------------------------------------------


def _random_list(rng: random.Random, depth: int, indent: str, limit: int) -> list[str]:
    ordered = rng.random() < 0.3
    marker = "1. " if ordered else "- "
    lines: list[str] = []
    for index in range(rng.choice([1, 2, 2, 3, 5, 30]) if depth == 0 else rng.randint(1, 3)):
        lines.append(f"{indent}{marker}item {depth}.{index}")
        inner = indent + " " * len(marker)
        chain = limit > 4 and index == 0  # a deep list is a chain: only its first item nests, or it would explode
        if depth < limit and (chain or (limit <= 4 and rng.random() < 0.45)):
            lines.extend(_random_list(rng, depth + 1, inner, limit))
            if rng.random() < 0.3:  # a sibling list after text: it goes on from the first
                lines += ["", f"{inner}Between.", ""]
                lines.extend(_random_list(rng, depth + 1, inner, limit))
        if depth < 3 and rng.random() < 0.12:
            lines += ["", f"{inner}> - quoted", f"{inner}> - quoted too", ""]
    return lines + [""]


def _random_document(rng: random.Random) -> str:
    out: list[str] = []
    level = 1
    deep = rng.random() < 0.12  # a list nested past the nine levels Word has
    for _ in range(rng.randint(1, 7)):
        move = rng.random()
        level = max(1, min(5, level + (1 if move < 0.4 else -1 if move < 0.55 else 2 if move < 0.6 else 0)))
        out.append("#" * level + " Heading\n")
        for _ in range(rng.randint(0, 3)):
            kind = rng.random()
            if kind < 0.45:
                out.extend(_random_list(rng, 0, "", 11 if deep else 4))
            elif kind < 0.7:
                out.append("A paragraph.\n")
            elif kind < 0.85:
                out.append("> quoted\n")
            else:
                out.append("> [!DRAFTING]\n> - a note\n> - and another\n")
    if rng.random() < 0.3:
        out.insert(0, "- preamble item\n  - nested\n")
    return "\n".join(out)


def _random_alternatives(rng: random.Random) -> str:
    """Sections, items and paragraphs that share a number, among others."""
    out: list[str] = []
    for index in range(rng.randint(2, 6)):
        out.append(f"# Section {index}\n")
        if rng.random() < 0.5:
            out.append(f"Para. {{#p{index} when=forum:courts}}\n\nPara arb. {{#p{index} when=forum:arb}}\n")
        if rng.random() < 0.5:
            out.append(f"- item {{#i{index} when=forum:courts}}\n- item arb {{#i{index} when=forum:arb}}\n  - nested\n"
                       f"- after\n")
        if rng.random() < 0.4:
            out.append(f"# Disputes {{#d{index} when=forum:courts}}\n\n## Sub\n\n"
                       f"# Disputes {{#d{index} when=forum:arb}}\n\n## Sub alt\n")
    return "\n".join(out)


@functools.cache
def _random_renders(variant: str) -> tuple:
    rng = random.Random(20261028)
    return tuple(render(FRONT + _random_document(rng), format="text", **VARIANTS[variant]) for _ in range(30))


@pytest.mark.parametrize("variant", VARIANTS)
def test_random_documents_are_planned_as_resolved(variant: str) -> None:
    for result in _random_renders(variant):
        try:
            assert_planned_as_resolved(result)
        except AssertionError as error:
            raise AssertionError(f"{error}\n--- document ---\n{result.tree.preamble}\n{result.output}") from error


@pytest.mark.parametrize("variant", ["default", "continental", "paragraphs", "none"])
def test_random_alternatives_are_planned_and_reported(variant: str) -> None:
    rng = random.Random(7)
    for _ in range(20):
        result = render(ALTERNATIVES + _random_alternatives(rng), format="text", **VARIANTS[variant])
        assert_planned_as_resolved(result)


def test_the_random_documents_reach_every_case_the_property_is_about() -> None:
    seen_rules: set[str] = set()
    seen_causes: set[str] = set()
    depth = 0
    for variant in VARIANTS:
        for result in _random_renders(variant):
            plan, diagnostics = plan_docx(result.tree)
            seen_rules |= rules(diagnostics)
            seen_causes |= {cause for unit in plan.units.values() for cause in unit.cause}
            depth = max(depth, max((lst.depth for lst in lists(result.tree)), default=0))
    assert {"render-docx-letter-overflow", "render-docx-section-label", "render-docx-static-restart",
            "render-docx-list-depth", "render-docx-shared-counter"} <= seen_rules
    assert {"restart-family", "resync", "label-fallback", "skipped-level", "clamped"} <= seen_causes
    assert depth >= 10


# =====================================================================================================
# Cross-references, bookmarks and the table of contents (docs/architecture.md, "The DOCX planner")
# =====================================================================================================


def show(plan: DocxPlan, segments: Sequence[Segment]) -> str:
    """*segments* as one line: literal text in quotes, a field as ``REF`` and the anchor of the bookmark it points at,
    ``\\t`` after it when it suppresses the text around the counter, ``\\h`` for the text of a heading."""
    anchors = {name: anchor for anchor, name in plan.anchors.items()}
    pieces = []
    for segment in segments:
        if isinstance(segment, Literal_):
            pieces.append(f'"{segment.text}"')
        else:
            pieces.append(f"REF {anchors[segment.bookmark]}" + (" \\t" if segment.suppress_text else "")
                          + ("" if segment.number else " \\h"))
    return " + ".join(pieces)


def cached(segments: Sequence[Segment]) -> str:
    return "".join(s.text if isinstance(s, Literal_) else s.cached for s in segments)


#: A document that has every kind of target; the references at the end are the ones the tests read.
TARGETS = """# Alpha {#alpha}

Intro paragraph one. {#p1}

- first item {#i1}
- second item {#i2}
  - nested one {#n1}
  - nested two {#n2}
    - deep one {#d1}
- third item {#i3}

## Beta {#beta}

### Gamma {#gamma}

1. ordered one {#o1}
2. ordered two {#o2}
   - bullet in ordered {#b1}

#### Delta {#delta}

##### Epsilon {#eps}

- eps item {#ei}

# Second {#second}

- a {#sa}
"""
REFERENCES = "\n" + " ".join(f"{{{{ref: {anchor}}}}}" for anchor in (
    "alpha", "p1", "i1", "n2", "d1", "beta", "gamma", "o2", "b1", "delta", "eps", "ei", "second", "sa", "i3"))

ARTICLE = {"numbering.levels": [{"counter": "upper-roman", "label": "Article {n}", "ref": "{n}"}]}
SECTION_SIGN = {"numbering.levels": [{"counter": "decimal", "label": "§ {n}", "ref": "{n}"}],
                "references.format": "§ {designation}"}


def covers(variant: str, **settings: object) -> dict[str, tuple[str, str]]:
    """Anchor of each target to (what the renderer shows, the plan's cover), for the document of :data:`TARGETS`."""
    result = render(FRONT + TARGETS + REFERENCES, format="text", **settings)
    plan, _ = plan_docx(result.tree)
    units_of = {n.unit: n for n in units(result.tree)}
    found = {}
    for inline in iter_tree_inlines(result.tree):
        if isinstance(inline, CrossRef):
            anchor = units_of[inline.target_unit].anchor
            found[anchor] = (inline.text, show(plan, plan.refs[inline.target_unit].segments))
    return found


# What each style's references are made of: fields wherever Word draws the number, literal text only for the
# delimiters between them. A field for the target alone covers a ``{path}`` label (``1.1.1``), one field per
# level covers a ``{n}`` label (``I.A.1``).
COVERS = [
    # default: decimal headings, (a) (i) (A) items, 1.1.1(2) for an ordered item in a section
    ("default", {}, "alpha", "1", "REF alpha"),
    ("default", {}, "i1", "1(a)", "REF alpha + REF i1"),
    ("default", {}, "d1", "1(b)(ii)(A)", "REF alpha + REF i2 + REF n2 + REF d1"),
    ("default", {}, "gamma", "1.1.1", "REF gamma"),
    ("default", {}, "o2", "1.1.1(2)", 'REF gamma + "(" + REF o2 + ")"'),
    ("default", {}, "eps", "1.1.1.1.1", "REF eps"),
    ("default", {}, "ei", "1.1.1.1.1(a)", "REF eps + REF ei"),
    # outline: every part of the number is a counter of its own level
    ("outline", {"style": "outline"}, "alpha", "I", "REF alpha"),
    ("outline", {"style": "outline"}, "beta", "I.A", 'REF alpha + "." + REF beta'),
    ("outline", {"style": "outline"}, "eps", "I.A.1.a(i)",
     'REF alpha + "." + REF beta + "." + REF gamma + "." + REF delta + REF eps'),
    ("outline", {"style": "outline"}, "i3", "I(c)", "REF alpha + REF i3"),
    # mixed: 1(a)(i)
    ("mixed", {"overrides": {"numbering.scheme": "mixed"}}, "gamma", "1(a)(i)", "REF alpha + REF beta + REF gamma"),
    ("mixed", {"overrides": {"numbering.scheme": "mixed"}}, "o2", "1(a)(i)(2)",
     'REF alpha + REF beta + REF gamma + "(" + REF o2 + ")"'),
    # continental: 1.1 for an item (the number of the section is not part of the item's own level in Word yet),
    # a(n) item nested in an item written "b)" and referred to as "(b)"
    ("continental", {"style": "continental"}, "i1", "1.1", 'REF alpha + "." + REF i1'),
    ("continental", {"style": "continental"}, "n2", "1.2(b)", 'REF alpha + "." + REF i2 + "(" + REF n2'),
    ("continental", {"style": "continental"}, "p1", "1(1)", "REF alpha + REF p1"),
    ("continental", {"style": "continental"}, "gamma", "1.1.1", "REF gamma"),
    # the none scheme: the text of the heading, then the pieces after a space
    ("none", {"overrides": {"numbering.scheme": "none"}}, "alpha", "Alpha", "REF alpha \\h"),
    ("none", {"overrides": {"numbering.scheme": "none"}}, "d1", "Alpha (b) (ii) (A)",
     'REF alpha \\h + " " + REF i2 + " " + REF n2 + " " + REF d1'),
    ("none", {"overrides": {"numbering.scheme": "none"}}, "o2", "Gamma (2)", 'REF gamma \\h + " (" + REF o2 + ")"'),
    # words and symbols in a label that the reference leaves out: \t drops them, the format puts them back
    ("article", {"overrides": {**ARTICLE, "references.format": "Article {designation}"}}, "i1", "Article I(a)",
     '"Article " + REF alpha \\t + REF i1'),
    ("article", {"overrides": {**ARTICLE, "references.format": "Article {designation}"}}, "beta", "Article I.1",
     '"Article " + REF beta'),
    ("section-sign", {"overrides": SECTION_SIGN}, "d1", "§ 1(b)(ii)(A)",
     '"§ " + REF alpha \\t + REF i2 + REF n2 + REF d1'),
]


@pytest.mark.parametrize(("variant", "settings", "anchor", "text", "expected"), COVERS)
def test_a_reference_is_the_renderers_designation_covered_by_fields_and_delimiters(
        variant: str, settings: dict, anchor: str, text: str, expected: str) -> None:
    assert covers(variant, **settings)[anchor] == (text, expected)


def test_a_cover_reads_as_the_renderer_does_and_a_field_is_cached_as_word_draws_it() -> None:
    result = render(FRONT + TARGETS + REFERENCES, format="text", style="outline")
    plan, diagnostics = plan_docx(result.tree)
    for inline in iter_tree_inlines(result.tree):
        if isinstance(inline, CrossRef):
            reference = plan.refs[inline.target_unit]
            assert reference.exact and cached(reference.segments) == inline.text
            for segment in reference.segments:
                if isinstance(segment, FieldSeg):
                    assert segment.number and segment.hyperlink and not segment.suppress_text
                    assert segment.cached_lo == segment.cached
    # a section's own field returns its number without the period of its label
    assert plan.units["s1"].label == "I." and plan.units["s1"].word_ref == "I"
    assert next(s for s in plan.refs["s1"].segments if isinstance(s, FieldSeg)).cached == "I"
    assert diagnostics and all(d.rule == "render-docx-static-restart" for d in diagnostics)


def test_a_reference_to_an_item_in_another_section_is_built_from_the_chain_of_its_own_section() -> None:
    body = "# One {#one}\n\n## Inner {#inner}\n\n- deep {#deep}\n  - deeper {#deeper}\n\n# Two {#two}\n\n" \
           "See {{ref: deeper}} and {{ref: inner}}, {{ref: one}}.\n"
    tree, plan, diagnostics = planned(body, style="outline")
    refs = {i.text: plan.refs[i.target_unit] for i in iter_tree_inlines(tree) if isinstance(i, CrossRef)}
    assert [(text, show(plan, r.segments)) for text, r in refs.items()] == [
        ("I.A(a)(i)", 'REF one + "." + REF inner + REF deep + REF deeper'),
        ("I.A", 'REF one + "." + REF inner'), ("I", "REF one")]
    assert all(r.exact for r in refs.values()) and not [d for d in diagnostics if "reference" in d.rule]


def test_a_reference_to_a_unit_the_style_does_not_number_shows_the_designation_of_its_section() -> None:
    body = "# Alpha {#alpha}\n\n## Beta {#beta}\n\n- bullet {#bullet}\n\nSee {{ref: bullet}}.\n"
    tree, plan, diagnostics = planned(body, overrides={"enumeration.enabled": False})
    [reference] = [i for i in iter_tree_inlines(tree) if isinstance(i, CrossRef)]
    assert reference.text == "1.1" and reference.target_unit != reference.section_unit
    assert show(plan, plan.refs[reference.target_unit].segments) == "REF beta"
    assert plan.refs[reference.target_unit].exact and cached(plan.refs[reference.target_unit].segments) == "1.1"
    # the bullet itself is a target nobody's field points at: it keeps its own bookmark for links
    assert plan.units[reference.target_unit].bookmarks == ("ld_bullet",)


def test_references_format_wraps_every_occurrence_of_the_designation() -> None:
    body = "# Alpha {#alpha}\n\n- x {#x}\n\nSee {{ref: alpha}} {{ref: x}}.\n"
    for fmt, expected in (("Section {designation} hereof", ["Section 1 hereof", "Section 1(a) hereof"]),
                          ("{designation} and {designation}", ["1 and 1", "1(a) and 1(a)"]),
                          ("[{designation}]", ["[1]", "[1(a)]"])):
        tree, plan, _ = planned(body, overrides={"references.format": fmt})
        found = [i for i in iter_tree_inlines(tree) if isinstance(i, CrossRef)]
        assert [i.text for i in found] == expected
        assert [cached(plan.refs[i.target_unit].segments) for i in found] == expected
        assert all(plan.refs[i.target_unit].exact for i in found)
    tree, plan, _ = planned(body, overrides={"references.format": "{designation} and {designation}"})
    [first, second] = [plan.refs[i.target_unit] for i in iter_tree_inlines(tree) if isinstance(i, CrossRef)]
    assert show(plan, first.segments) == 'REF alpha + " and " + REF alpha'
    assert show(plan, second.segments) == 'REF alpha + REF x + " and " + REF alpha + REF x'


def test_a_reference_to_the_same_target_is_planned_once() -> None:
    tree, plan, _ = planned("# A {#a}\n\n{{ref: a}} {{ref: a}} and again {{ref: a}}.\n")
    assert list(plan.refs) == ["s1"]


def test_the_fields_of_a_unit_that_is_not_a_target_have_a_bookmark_of_their_own() -> None:
    """Every unit a field points at has a bookmark: its anchor's, or a hidden one when it has no anchor."""
    tree, plan, _ = planned("# A\n\n- x\n  - y {#y}\n\nSee {{ref: y}}.\n")
    assert plan.units["u2"].bookmarks == ("ld_y",)
    assert plan.units["u1"].bookmarks == ("_Ld1",) and plan.units["s1"].bookmarks == ("ld_a",)
    assert plan.units["s1"].bookmarks[0] in plan.anchors.values() and "_Ld1" not in plan.anchors.values()
    [reference] = plan.refs.values()
    assert [s.bookmark for s in reference.segments] == ["ld_a", "_Ld1", "ld_y"]


# -- Why a reference cannot be built from fields -------------------------------------------------------------


def references_of(tree: RenderTree, plan: DocxPlan) -> list[tuple[str, str, bool]]:
    return [(i.text, show(plan, plan.refs[i.target_unit].segments), plan.refs[i.target_unit].exact)
            for i in iter_tree_inlines(tree) if isinstance(i, CrossRef)]


def test_a_skipped_heading_level_leaves_a_part_of_the_designation_no_field_stands_for() -> None:
    body = "# A {#a}\n\n### B {#b}\n\nSee {{ref: b}}.\n"
    tree, plan, diagnostics = planned(body, style="outline")
    assert references_of(tree, plan) == [("I.A.1", "REF b", False)]
    [found] = [d for d in diagnostics if d.rule == "render-docx-reference"]
    assert found.level == "warning" and found.line == tree.sections[1].blocks[0].line
    assert found.message.startswith("The cross-reference 'I.A.1' cannot be built from Word's numbering fields: a "
                                    "heading level is skipped before it")
    assert "reads '1' in Word" in found.message
    # the decimal scheme writes the whole number in the heading's own field, so nothing is missing there
    tree, plan, diagnostics = planned(body)
    assert references_of(tree, plan) == [("1.1.1", "REF b", True)] and not diagnostics


def test_text_around_a_counter_that_word_cannot_drop_leaves_the_reference_as_one_field() -> None:
    body = "# A\n\n- x {#x}\n\nSee {{ref: x}}.\n"
    for label, shown in (("({n})", "(1)"), ("{n}:", "1:"), ("No. {n}", "No. 1")):
        overrides = {"enumeration.levels": [{"counter": "decimal", "label": label, "ref": "{n}"}]}
        tree, plan, diagnostics = planned(body, overrides=overrides)
        [(text, cover_, exact)] = references_of(tree, plan)
        assert (text, cover_, exact) == ("11", "REF x", False)
        [found] = [d for d in diagnostics if d.rule == "render-docx-reference"]
        assert f"Word draws its number as '{shown}'" in found.message and "'11'" in found.message


def test_a_cover_that_reads_differently_in_word_is_reported_unless_a_unit_already_says_why() -> None:
    many = "\n".join(f"- i{i} {{#m{i}}}" for i in range(1, 30))
    tree, plan, diagnostics = planned(f"# A {{#a}}\n\n{many}\n\nSee {{{{ref: m28}}}} {{{{ref: m1}}}}.\n")
    # the letter counter past 26 is a finding of its own: the references to it are not reported again
    assert references_of(tree, plan) == [("1(ab)", "REF a + REF m28", False), ("1(a)", "REF a + REF m1", True)]
    assert [d.rule for d in diagnostics] == ["render-docx-letter-overflow"]
    assert cached(plan.refs[tree.sections[0].blocks[0].items[27].unit].segments) == "1(bb)"


def test_a_reference_whose_text_the_renderer_cut_is_reported() -> None:
    title = "word " * 300
    body = f"# {title.strip()} {{#long}}\n\nSee {{{{ref: long}}}}.\n"
    tree, plan, diagnostics = planned(body, overrides=NONE)
    [reference] = [i for i in iter_tree_inlines(tree) if isinstance(i, CrossRef)]
    assert len(reference.text) < len(title) - 100
    assert not plan.refs["s1"].exact
    [found] = [d for d in diagnostics if d.rule == "render-docx-reference"]
    assert "will read" in found.message and "numbered or written differently" in found.message


def test_a_cover_with_the_switch_word_ignores_in_libreoffice_is_an_information_for_the_document() -> None:
    body = "# A {#a}\n\n# B {#b}\n\nSee {{ref: a}} {{ref: b}}.\n"
    settings = {"overrides": {**ARTICLE, "references.format": "Article {designation}"}}
    tree, plan, diagnostics = planned(body, **settings)
    first, second = (plan.refs[i.target_unit].segments for i in iter_tree_inlines(tree) if isinstance(i, CrossRef))
    assert [(s.suppress_text, s.cached, s.cached_lo) for s in first if isinstance(s, FieldSeg)] == [(True, "I", "Article I")]
    assert [(s.suppress_text, s.cached, s.cached_lo) for s in second if isinstance(s, FieldSeg)] == [(True, "II", "Article II")]
    [found] = [d for d in diagnostics if d.rule == "render-docx-reference-word-only"]
    assert found.level == "info" and found.line is None and "'Article I'" in found.message
    assert "reads 'Article Article I'" in found.message and "so do 1 more" in found.message
    assert all(plan.refs[i].exact for i in plan.refs)


def test_reference_findings_name_the_file_and_the_first_line_of_a_repeated_reference() -> None:
    body = "# A {#a}\n\n### B {#b}\n\nFirst {{ref: b}}.\n\nSecond {{ref: b}}.\n"
    result = render(FRONT + body, format="text", style="outline")
    _, diagnostics = plan_docx(result.tree, file="deal.lgd")
    [found] = [d for d in diagnostics if d.rule == "render-docx-reference"]
    assert found.file == "deal.lgd" and found.line == result.tree.sections[1].blocks[0].line


def test_untrusted_text_in_a_reference_message_is_escaped() -> None:
    body = "# A\n\n### B {#b}\n\nSee {{ref: b}}.\n"
    levels = [{"counter": "decimal", "label": "{n}\u0007", "ref": "{n}\u0007"}, {"counter": "decimal", "label": "{n}",
                                                                                  "ref": "{n}"}]
    _, _, diagnostics = planned(body, overrides={"numbering.levels": levels})
    assert all("\u0007" not in d.message for d in diagnostics)


# -- The cover on its own ----------------------------------------------------------------------------------


def field(owner: str, *tokens: object, number: bool = True, suppress: bool = False) -> Field:
    return Field(owner, number, suppress, tuple(tokens))  # type: ignore[arg-type]


def test_fewest_fields_win_then_fewest_switches_then_fewest_literals() -> None:
    designation = [Count("a", "1"), Lit("."), Count("b", "2")]
    whole = field("b", Count("a", "1"), Lit("."), Count("b", "2"))
    parts = [field("a", Count("a", "1")), field("b", Count("b", "2"))]
    assert cover(designation, [*parts, whole]) == [whole]
    assert cover(designation, parts) == [parts[0], ".", parts[1]]
    plain = field("a", Lit("Article "), Count("a", "I"))
    bare = field("a", Count("a", "I"), suppress=True)
    assert cover([Count("a", "I")], [plain, bare]) == [bare]
    assert cover([Lit("Article "), Count("a", "I")], [plain, bare]) == [plain]  # the words are in the field
    assert cover([Lit("Article "), Count("a", "I")], [bare]) == ["Article ", bare]  # or come from the literal
    assert cover([], []) == []


def test_a_literal_stands_for_delimiters_only_never_for_a_counter_or_a_title() -> None:
    assert cover([Count("a", "1")], []) is None
    assert cover([Title("a", "Alpha")], []) is None
    assert cover([Lit("(")], []) == ["("]
    assert cover([Title("a", "Alpha")], [field("a", Title("a", "Alpha"), number=False)]) is not None


def test_a_field_fits_where_the_counters_belong_to_the_same_units_whatever_their_text() -> None:
    designation = [Count("a", "ab")]
    assert cover(designation, [field("a", Count("a", "bb"))]) == [field("a", Count("a", "bb"))]
    assert cover(designation, [field("a", Count("other", "ab"))]) is None
    assert cover([Count(Ghost("s2", 1), "1")], [field("s2", Count("s2", "1"))]) is None


def test_the_atom_nothing_can_stand_for_is_found() -> None:
    designation = [Count("a", "I"), Lit("."), Count(Ghost("s2", 1), "1"), Lit("."), Count("b", "1")]
    fields = [field("a", Count("a", "I")), field("b", Count("b", "1"))]
    assert cover(designation, fields) is None and uncovered(designation, fields) == 2
    assert uncovered([Count("a", "I")], fields) is None


def test_the_switch_that_drops_text_is_modelled_only_where_it_leaves_bare_counters() -> None:
    assert suppressed([Lit("Article "), Count("a", "I")]) == (Count("a", "I"),)
    assert suppressed([Lit("§ "), Count("a", "3")]) == (Count("a", "3"),)
    assert suppressed([Lit("("), Count("a", "a"), Lit(")")]) is None
    assert suppressed([Count("a", "1"), Lit("."), Count("b", "2")]) is None
    assert suppressed([Lit("No. "), Count("a", "1")]) is None
    assert suppressed([Lit("Article")]) is None


# -- Bookmarks ----------------------------------------------------------------------------------------------


def test_bookmark_names_have_a_prefix_for_what_they_mark_and_write_dashes_as_underscores() -> None:
    names = BookmarkNames()
    assert names.anchor("ld_", "delay-cause") == "ld_delay_cause"
    assert names.anchor("lddef_", "business-day") == "lddef_business_day"
    assert names.anchor("ldatt_", "schedule-a") == "ldatt_schedule_a"
    assert names.fixed("_LdAttachments") == "_LdAttachments"
    assert names.toc() == "_Toc100000001" and names.toc() == "_Toc100000002"


def test_a_name_that_cannot_be_used_is_a_hidden_one_counted_in_the_order_they_are_given() -> None:
    names = BookmarkNames()
    assert names.anchor("ld_", "a-b") == "ld_a_b"
    assert names.anchor("ld_", "a_b") == "_Ld1"  # the same name once the dash is an underscore: not lossless
    assert names.anchor("ld_", "A-B") == "_Ld2"  # the same ignoring case
    assert names.anchor("ld_", "clanek-č") == "_Ld3"  # a letter a name cannot hold
    assert names.anchor("ld_", "x" * 37) == "ld_" + "x" * 37  # 40 characters
    assert names.anchor("ld_", "y" * 38) == "_Ld4"  # 41
    assert names.hidden() == "_Ld5"


def test_hidden_names_are_numbered_in_document_order_across_anchors_and_units() -> None:
    tree, plan, _ = planned("# A\n\n- x\n  - y {#y}\n\nSee {{ref: y}} and " + "{{ref: " + "z" * 50 + "}}.\n")
    assert plan.units["u1"].bookmarks == ("_Ld1",)


def test_an_anchor_too_long_for_a_name_is_hidden_and_still_found_by_the_plan() -> None:
    anchor = "a-very-long-identifier-that-goes-on-and-on-past-forty"
    tree, plan, _ = planned(f"# A {{#{anchor}}}\n\n{{{{ref: {anchor}}}}}\n")
    assert plan.anchors == {anchor: "_Ld1"} and plan.units["s1"].bookmarks == ("_Ld1",)
    assert [s.bookmark for s in plan.refs["s1"].segments if isinstance(s, FieldSeg)] == ["_Ld1"]


def test_the_names_do_not_depend_on_what_was_rendered_before() -> None:
    body = "# A {#a-b}\n\n- x {#x}\n\nSee {{ref: x}}.\n"
    first = planned(body)[1]
    render(FRONT + "# Other {#a}\n", format="text")
    assert planned(body)[1] == first


def test_definitions_and_attachments_have_bookmarks_too() -> None:
    result = render((DOCUMENTS / "features.lgd").read_text(encoding="utf-8"), format="text")
    plan, _ = plan_docx(result.tree)
    assert {a: n for a, n in plan.anchors.items() if not n.startswith("ld_")} == {
        "def:agreement": "lddef_agreement", "def:services": "lddef_services", "def:business-day": "lddef_business_day",
        "schedule-a": "ldatt_schedule_a", "exhibit-1": "ldatt_exhibit_1", "ld:attachments": "_LdAttachments"}
    assert plan.anchors["services"] == "ld_services" and plan.anchors["delay-cause"] == "ld_delay_cause"
    assert all(plan.units[unit.unit].bookmarks == (plan.anchors[unit.anchor],)
               for unit in units(result.tree) if unit.anchor)


def test_every_name_is_valid_and_unique_ignoring_case() -> None:
    for name in ("features.lgd", "template.lgd"):
        for settings in VARIANTS.values():
            result = render((DOCUMENTS / name).read_text(encoding="utf-8"), format="text", **settings)
            plan, _ = plan_docx(result.tree)
            assert_names_are_sound(plan)


def assert_names_are_sound(plan: DocxPlan) -> None:
    names = [*(n for u in plan.units.values() for n in u.bookmarks), *plan.anchors.values(),
             *(t.bookmark for t in plan.toc)]
    names = list(dict.fromkeys(names))  # an anchor's name is the first of its unit's
    assert all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,39}", n) for n in names), names
    assert len({n.lower() for n in names}) == len(names), names


# -- The table of contents ---------------------------------------------------------------------------------------


ATTACHED = """---
title: T
attachments:
  - id: schedule-a
    title: Schedule A
    file: a.lgd
  - id: exhibit-1
    title: Exhibit 1
    file: b.pdf
---
"""


def test_a_contents_entry_names_the_section_it_lists() -> None:
    result = render(ATTACHED + "# A\n\n## B\n\n### C\n\n# D\n", format="text",
                    overrides={"contents.enabled": True, "contents.depth": 2})
    assert [(e.level, e.unit) for e in result.tree.contents] == [
        (1, "s1"), (2, "s2"), (1, "s4"), (1, None), (2, None), (2, None)]
    result = render(ATTACHED + "# A\n", format="text")
    assert result.tree.contents == ()


def test_the_contents_come_from_the_numbers_word_draws() -> None:
    body = "# Alpha\n\n## Beta\n\n### Gamma\n\n# Delta\n"
    tree, plan, _ = planned(body, ATTACHED, overrides={"contents.enabled": True, "contents.depth": 2})
    assert [(e.unit, e.level, e.number, e.sep, e.condition_label, e.cached_page) for e in plan.toc] == [
        ("s1", 1, "1.", "\t", None, ""), ("s2", 2, "1.1", "\t", None, ""), ("s4", 1, "2.", "\t", None, ""),
        (None, 1, "", "", None, ""), (None, 2, "", "", None, ""), (None, 2, "", "", None, "")]
    assert [e.title for e in plan.toc[:3]] == [tree.sections[0].title, tree.sections[1].title, tree.sections[3].title]
    assert [e.bookmark for e in plan.toc] == [f"_Toc10000000{k}" for k in range(1, 7)]
    assert [plan.units[s].toc_bookmark for s in ("s1", "s2", "s3", "s4")] == [
        "_Toc100000001", "_Toc100000002", None, "_Toc100000003"]
    assert [e.title for e in plan.toc[3:]] == [tree.contents[3].title, tree.contents[4].title, tree.contents[5].title]
    assert_names_are_sound(plan)


def test_a_contents_entry_has_the_separator_the_level_has_and_the_number_of_the_style() -> None:
    body = "# Alpha\n\n## Beta\n"
    _, plan, _ = planned(body, overrides={"contents.enabled": True, **ARTICLE})
    assert [(e.number, e.sep) for e in plan.toc] == [("Article I", " "), ("I.1", "\t")]
    _, plan, _ = planned(body, overrides={"contents.enabled": True, "numbering.scheme": "legal-outline"})
    assert [(e.number, e.sep) for e in plan.toc] == [("I.", "\t"), ("A.", "\t")]
    _, plan, _ = planned(body, overrides={"contents.enabled": True, **NONE})
    assert [(e.number, e.sep) for e in plan.toc] == [("", ""), ("", "")]
    _, plan, _ = planned(body, overrides={"contents.enabled": True, "contents.attachments": False}, front=ATTACHED)
    assert [e.unit for e in plan.toc] == ["s1"] * 0 + ["s1", "s2"]


def test_the_contents_carry_the_condition_label_of_a_conditional_heading() -> None:
    _, plan, _ = planned("# A {when=forum:courts}\n\n# B\n", ALTERNATIVES, overrides={"contents.enabled": True})
    assert [e.condition_label for e in plan.toc] == ["Only if: forum:courts", None]


def test_there_are_no_entries_unless_the_style_asks_for_contents() -> None:
    _, plan, _ = planned("# A\n", ATTACHED)
    assert plan.toc == () and plan.units["s1"].toc_bookmark is None


# -- The property: the plan's references read as the renderer wrote them, or a diagnostic says why not ---------


def with_anchors(source: str) -> str:
    """*source* with an anchor on every heading, item, quoted item and plain paragraph the random documents hold."""
    out, count = [], 0
    for line in source.splitlines():
        if re.match(r"^\s*(#+ Heading|[-]|\d+\.) ", line) or line == "A paragraph." or re.match(r"^\s*> - ", line):
            count += 1
            line += f" {{#a{count}}}"
        out.append(line)
    return "\n".join(out)


def with_references(source: str) -> str:
    """*source* with a paragraph at its end that refers to every anchor in it."""
    anchors = dict.fromkeys(re.findall(r"\{#([a-z][a-z0-9-]*)", source))
    return source + "\n\nRefs: " + " ".join(f"{{{{ref: {anchor}}}}}" for anchor in anchors) + "\n"


def assert_references_as_resolved(result) -> None:  # noqa: ANN001
    """For every reference: its cover reads as the renderer's text (``exact``, and the cached results say so), or the
    plan says why not: a unit on the way is numbered differently in Word (a diagnostic of the numbering says so, as the
    property of the numbering above has it), or a ``render-docx-reference`` finding names the reference. Every field
    points at a bookmark the plan makes, the names are sound, and the same tree gives the same plan."""
    tree = result.tree
    plan, diagnostics = plan_docx(tree)
    assert plan_docx(tree) == (plan, diagnostics)
    assert_names_are_sound(plan)
    nodes = {u.unit: u for u in units(tree)}
    counters = {node.unit: counter for node, counter in numbered(tree)}
    marked = {n for u in plan.units.values() for n in u.bookmarks}
    reported = [d for d in diagnostics if d.rule == "render-docx-reference"]
    texts = {i.target_unit: i.text for i in iter_tree_inlines(tree) if isinstance(i, CrossRef) and i.target_unit}
    assert set(plan.refs) == set(texts)
    findings = 0
    for target, text in texts.items():
        reference = plan.refs[target]
        assert all(s.bookmark in marked for s in reference.segments if isinstance(s, FieldSeg))
        assert reference.exact == (cached(reference.segments) == text)
        for segment in reference.segments:
            if isinstance(segment, FieldSeg) and segment.number and not segment.suppress_text:
                assert segment.cached == plan.units[next(u for u, p in plan.units.items()
                                                        if segment.bookmark in p.bookmarks)].word_ref
        mentioned = any(d.message.startswith(f"The cross-reference '{text}' ") for d in reported)
        findings += mentioned
        if reference.exact:
            assert mentioned <= any(not plan.refs[t].exact for t in texts if texts[t] == text), (target, text)
            continue
        chain, unit = [], target
        while unit is not None:
            chain.append(unit)
            unit = nodes[unit].parent_unit
        differs = False
        for unit in chain:
            mine, node = plan.units[unit], nodes[unit]
            if node.number is not None and mine.count is not None:
                differs |= mine.count != node.number or (
                    unit in counters and word_counter(node.number, NUM_FMT[counters[unit]])
                    != format_counter(node.number, counters[unit]))
        assert differs or mentioned, (target, text, cached(reference.segments), show(plan, reference.segments))
    assert len(reported) <= findings


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("name", ["features.lgd", "template.lgd"])
def test_the_test_documents_references_are_planned_as_resolved(name: str, variant: str) -> None:
    source = (DOCUMENTS / name).read_text(encoding="utf-8")
    assert_references_as_resolved(render(source, format="text", **VARIANTS[variant]))


@pytest.mark.conformance
@pytest.mark.skipif(SPEC is None, reason="LegalDown specification checkout not found")
@pytest.mark.parametrize("variant", VARIANTS)
def test_the_specifications_documents_references_are_planned_as_resolved(variant: str) -> None:
    for path in _spec_documents():
        try:
            result = render(path.read_text(encoding="utf-8"), format="text", **VARIANTS[variant])
        except DocumentError:
            continue
        try:
            assert_references_as_resolved(result)
        except AssertionError as error:
            raise AssertionError(f"{path.relative_to(SPEC)}: {error}") from error


@functools.cache
def _referenced_renders(variant: str) -> tuple:
    rng = random.Random(20261029)
    sources = [with_references(with_anchors(_random_document(rng))) for _ in range(30)]
    return tuple((source, render(FRONT + source, format="text", **VARIANTS[variant])) for source in sources)


@pytest.mark.parametrize("variant", VARIANTS)
def test_random_documents_references_are_planned_as_resolved(variant: str) -> None:
    for source, result in _referenced_renders(variant):
        try:
            assert_references_as_resolved(result)
        except AssertionError as error:
            raise AssertionError(f"{error}\n--- document ---\n{source}") from error


@pytest.mark.parametrize("variant", ["default", "continental", "paragraphs", "none"])
def test_random_alternatives_references_are_planned_as_resolved(variant: str) -> None:
    rng = random.Random(11)
    for _ in range(20):
        source = ALTERNATIVES + with_references(_random_alternatives(rng))
        assert_references_as_resolved(render(source, format="text", **VARIANTS[variant]))


def test_the_random_documents_reach_every_kind_of_cover() -> None:
    shapes: set[str] = set()
    inexact = switches = 0
    for variant in VARIANTS:
        for _, result in _referenced_renders(variant):
            plan, _ = plan_docx(result.tree)
            for reference in plan.refs.values():
                shapes.add("".join("L" if isinstance(s, Literal_) else "F" for s in reference.segments))
                inexact += not reference.exact
                switches += any(isinstance(s, FieldSeg) and s.suppress_text for s in reference.segments)
    assert {"F", "FF", "FFF", "FFFF", "FLF", "FLFL", "FLFLF"} <= shapes
    assert inexact > 0 and switches > 0
