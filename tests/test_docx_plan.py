"""The DOCX plan (docs/architecture.md, "The DOCX planner"): which abstractNums and nums a document needs,
which (numId, ilvl) each section, list item and numbered paragraph takes, and that Word, simulated, numbers every unit
as the resolver did, or a diagnostic says why not; and, from the numbering, the fields of every cross-reference, the
bookmarks and the table of contents. Everything is checked on the plan's data; no file is written (the LibreOffice
round trip is in test_docx_roundtrip.py)."""
from __future__ import annotations

import functools
import random
import re
import time
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from conftest import DOCUMENTS, FRONT, spec_root

from legaldown_render import DocumentError, render
from legaldown_render.errors import StyleError
from legaldown_render.resolve.docx_bookmarks import BookmarkNames
from legaldown_render.resolve.docx_cover import Field, Title, cover, suppressed, uncovered
from legaldown_render.resolve.docx_numbering import NUM_FMT, Count, Ghost, Lit, Step, word_counter
from legaldown_render.resolve.docx_plan import (
    DocxPlan,
    FieldSeg,
    Literal_,
    Segment,
    _components,
    _Planner,
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
    "title: T\nquestions:\n  forum:\n    type: choice\n    choices:\n      courts: Courts\n      arb: Arbitration\n"
    "      expert: Expert"))
#: The answers of the question of :data:`ALTERNATIVES`: any two exclude each other, so units that have the same
#: identifier and one answer each are alternatives (§15.4), up to three ways.
FORUMS = ("courts", "arb", "expert")

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


# -- Labels that need the section: on the heading list (§13.2) --------------------------------------------------

CONTINENTAL_ITEMS = "# A\n\n- x\n  - y\n  - z\n    - w\n- v\n\n## B\n\n- u\n"


def test_an_item_whose_label_needs_the_section_rides_the_heading_list_one_level_below_the_section() -> None:
    tree, plan, diagnostics = planned(CONTINENTAL_ITEMS, style="continental")
    x, y, z, w, v, u = items(tree)
    # the heading num, at the level after the section's (%1.%2 after "1."), one num for the list with an override
    assert shown(plan, x, v) == [(2, 1, "1.1"), (2, 1, "1.2")]
    assert plan.nums[1].abstract_id == plan.heading_num_id - 1 == 0 and plan.nums[1].start_overrides == ((1, 1),)
    assert [a.kind for a in plan.abstract_nums] == ["headings"]  # no abstractNum of the items' own is made
    assert plan.units[x.unit].cause == () and not plan.units[x.unit].alt
    assert [x.label, v.label, u.label] == ["1.1", "1.2", "1.1.1"]
    # an item under a level-2 section is on the level after that: %1.%2.%3
    assert shown(plan, u) == [(4, 2, "1.1.1")]
    assert plan.abstract_nums[0].levels[2].lvl_text == "%1.%2.%3"
    # the items are no headings: no heading style and a name of their own for their indent
    assert plan.units[x.unit].ind == (plan.abstract_nums[0].levels[1].left,) * 2


def test_the_heading_levels_an_item_stands_on_are_not_changed_by_it() -> None:
    _, plain, _ = planned("# A\n\n## B\n\n### C\n", style="continental")
    _, with_items, _ = planned(CONTINENTAL_ITEMS, style="continental")
    assert plain.abstract_nums[0].levels[:5] == with_items.abstract_nums[0].levels[:5]


def test_a_sub_heading_after_the_items_of_its_section_is_restarted_where_the_resolver_is() -> None:
    tree, plan, diagnostics = planned(CONTINENTAL_ITEMS, style="continental")
    b = tree.sections[1]
    # Word's counter of the level is shared by the sub-heading and the items: 1.3 where the resolver has 1.1 again
    assert (b.label, plan.units[b.unit].label, plan.units[b.unit].cause) == ("1.1", "1.1", ("resync",))
    assert plan.units[b.unit].explicit  # a num of its own: the paragraph has to carry it
    assert plan.units[b.unit].explicit  # a num of its own: the paragraph must carry it
    assert plan.nums[plan.units[b.unit].num_id - 1].start_overrides == ((1, 1),)
    [found] = [d for d in diagnostics if d.rule == "render-docx-shared-counter"]
    assert found.level == "warning" and found.line == b.line
    assert "every output format" in found.message and "designation 1.1" in found.message
    assert "render-docx-number-mismatch" not in rules(diagnostics) and "render-docx-section-label" not in rules(diagnostics)


def test_a_resync_is_reported_once_with_the_number_of_places_that_do_the_same() -> None:
    body = "".join(f"# S{i}\n\n- a\n\n## T{i}\n\n" for i in range(5))
    _, _, diagnostics = planned(body, style="continental")
    [found] = [d for d in diagnostics if d.rule == "render-docx-shared-counter"]
    assert found.message.endswith("4 more units do the same.")


def test_a_list_nested_in_an_item_on_the_heading_list_rides_the_levels_reserved_for_it() -> None:
    tree, plan, diagnostics = planned(CONTINENTAL_ITEMS, style="continental")
    x, y, z, w, v, u = items(tree)
    # depth 1 and 2: levels 6 and 7 of the heading abstractNum, the num of the parent and no override
    assert shown(plan, y, z, w) == [(2, 5, "a)"), (2, 5, "b)"), (2, 6, "i)")]
    assert [plan.units[i.unit].cause for i in (y, w)] == [("reserved-level",), ("reserved-level",)]
    levels = plan.abstract_nums[0].levels
    assert [(levels[i].num_fmt, levels[i].lvl_text) for i in (5, 6)] == [("lowerLetter", "%6)"), ("lowerRoman", "%7)")]
    assert [(levels[i].num_fmt, levels[i].lvl_text) for i in (7, 8)] == [("decimal", "%8"), ("decimal", "%9")]  # unused
    assert len(plan.nums) == 4 and plan.nums[1].start_overrides == ((1, 1),)
    # one step in from the text of the item it is nested in, as the items of any list are
    assert plan.units[y.unit].ind == (plan.units[x.unit].ind[0] + 567, 567)
    assert plan.units[w.unit].ind == (plan.units[x.unit].ind[0] + 1134, 567)
    # the item after the nested ones goes on, and a new item's list restarts by structure
    assert plan.units[v.unit].count == 2 and diagnostics[0].rule == "render-docx-shared-counter"


def test_a_nested_list_under_a_new_item_on_the_heading_list_restarts_by_structure() -> None:
    tree, plan, diagnostics = planned("# A\n\n- a\n  - x\n- b\n  - y\n  - z\n", style="continental")
    _, x, _, y, z = items(tree)
    assert shown(plan, x, y, z) == [(2, 5, "a)"), (2, 5, "a)"), (2, 5, "b)")] and diagnostics == []
    assert len(plan.nums) == 2 and plan.units[y.unit].cause == ("reserved-level",)


def test_siblings_nested_in_one_item_on_the_heading_list_go_on_counting() -> None:
    body = "# A\n\n- a\n  - x\n  - y\n\n  Between.\n\n  - z\n"
    tree, plan, diagnostics = planned(body, style="continental")
    assert [(plan.units[i.unit].label, plan.units[i.unit].num_id) for i in items(tree)[1:]] == [
        ("a)", 2), ("b)", 2), ("c)", 2)]
    assert len(plan.nums) == 2 and diagnostics == []


def test_a_reserved_level_is_defined_on_first_use_and_only_when_it_is_used() -> None:
    _, plain, _ = planned("# A\n\n- x\n", style="continental")
    assert [(level.lvl_text, level.num_fmt) for level in plain.abstract_nums[0].levels[5:]] == [
        (f"%{k}", "decimal") for k in range(6, 10)]
    _, nested, _ = planned("# A\n\n- x\n  - y\n    - z\n", style="continental")
    assert [level.lvl_text for level in nested.abstract_nums[0].levels[5:]] == ["%6)", "%7)", "%8", "%9"]


def test_a_reserved_level_is_shared_by_every_list_that_reads_as_it_does() -> None:
    tree, plan, diagnostics = planned("# A\n\n- x\n  - y\n\n# B\n\n- z\n  - w\n", style="continental")
    _, y, _, w = items(tree)
    assert (plan.units[y.unit].ilvl, plan.units[w.unit].ilvl) == (5, 5) and diagnostics == []
    assert [level.lvl_text for level in plan.abstract_nums[0].levels[5:7]] == ["%6)", "%7"]


def test_a_nested_label_that_shows_the_section_does_not_ride_the_reserved_levels() -> None:
    # depth 3 of the continental style is the first level's label again, {section}.{n}
    body = "# A\n\n- x\n  - y\n    - z\n      - w\n        - v\n"
    tree, plan, diagnostics = planned(body, style="continental")
    x, y, z, w, v = items(tree)
    assert [plan.units[i.unit].ilvl for i in (x, y, z)] == [1, 5, 6]
    assert plan.units[w.unit].ilvl == 3 and plan.units[w.unit].cause == ("restart-section", "label-fallback")
    assert plan.units[w.unit].label == "1" and w.label == "1.1"
    # it is one step in from the item it is nested in, on whatever list it is
    assert plan.units[w.unit].ind == (plan.units[z.unit].ind[0] + 567, 567)
    # the list after it is on the family's abstractNum too, under a parent that is not on the heading list
    assert [a.kind for a in plan.abstract_nums] == ["headings", "enumerated"]
    assert plan.units[v.unit].ilvl == 4 and plan.units[v.unit].cause == ()
    assert [(d.rule, d.level) for d in diagnostics] == [
        ("render-docx-static-restart", "info"), ("render-docx-section-label", "warning")]


FIVE_LEVELS = {"enumeration.levels": [
    {"counter": "decimal", "label": "{section}.{n}", "ref": ".{n}"},
    {"counter": "lower-alpha", "label": "{n})", "ref": "({n})"},
    {"counter": "lower-roman", "label": "{n})", "ref": "({n})"},
    {"counter": "upper-alpha", "label": "({n})", "ref": "({n})"},
    {"counter": "decimal", "label": "[{n}]", "ref": "[{n}]"},
    {"counter": "lower-alpha", "label": "{n}.", "ref": "{n}"}]}


def test_a_chain_of_four_lists_in_a_clause_rides_all_four_reserved_levels_and_the_fifth_falls_back() -> None:
    body = "# A\n\n" + "".join("  " * i + f"- level {i}\n" for i in range(6))
    tree, plan, diagnostics = planned(body, style="continental", overrides=FIVE_LEVELS)
    found = items(tree)
    assert [plan.units[i.unit].ilvl for i in found[:5]] == [1, 5, 6, 7, 8]
    assert [plan.units[i.unit].label for i in found[:5]] == ["1.1", "a)", "i)", "(A)", "[1]"]
    assert [i.label for i in found[:5]] == ["1.1", "a)", "i)", "(A)", "[1]"]
    assert len({plan.units[i.unit].num_id for i in found[:5]}) == 1  # the clause's num, no override of their own
    assert [level.lvl_text for level in plan.abstract_nums[0].levels[5:]] == ["%6)", "%7)", "(%8)", "[%9]"]
    assert [plan.units[i.unit].cause for i in found[1:5]] == [("reserved-level",)] * 4
    # depth 5 is past the reserved levels: the family's own abstractNum, restarted by a fixed setting
    assert plan.units[found[5].unit].cause == ("restart-section",) and plan.units[found[5].unit].ilvl == 5
    assert rules(diagnostics) == {"render-docx-static-restart"}


def test_a_nested_list_before_a_level_5_section_falls_back_without_a_section_label_finding() -> None:
    # The level after the fifth heading level is kept for the units of the level-5 section, so the nested list of an
    # earlier section cannot have it: it keeps the family's abstractNum, restarted by a fixed setting (information),
    # and its label, which has no section in it, is not reported.
    body = "# A\n\n- x\n  - y\n    - z\n\n## B\n\n### C\n\n#### D\n\n##### E\n\n- five\n"
    tree, plan, diagnostics = planned(body, style="continental")
    x, y, z, five = items(tree)
    assert plan.units[x.unit].ilvl == 1 and plan.units[five.unit].ilvl == 5
    assert plan.units[y.unit].cause == ("restart-section",) and plan.units[y.unit].ilvl == 1
    assert plan.units[z.unit].cause == () and plan.units[z.unit].ilvl == 2
    assert rules(diagnostics) == {"render-docx-static-restart", "render-docx-shared-counter"}  # B follows the clause
    assert plan.abstract_nums[0].levels[5].lvl_text == "%1.%2.%3.%4.%5.%6"
    # without the level-5 section the same lists ride the reserved levels
    tree, plan, diagnostics = planned("# A\n\n- x\n  - y\n    - z\n", style="continental")
    assert [plan.units[i.unit].ilvl for i in items(tree)] == [1, 5, 6] and diagnostics == []



def test_an_ordered_list_nested_in_an_item_on_the_heading_list_keeps_its_own_numbering() -> None:
    tree, plan, diagnostics = planned("# A\n\n- x\n  1. y\n  1. z\n", style="continental")
    x, y, z = items(tree)
    assert [a.kind for a in plan.abstract_nums] == ["headings", "renumber"]
    assert shown(plan, y, z) == [(3, 1, "1."), (3, 1, "2.")] and plan.units[y.unit].cause == ("restart-section",)
    assert plan.units[y.unit].ind == (plan.units[x.unit].ind[0] + 567, 567)
    [found] = diagnostics
    assert (found.rule, found.level) == ("render-docx-static-restart", "info") and "carries the section" in found.message


def test_the_level_after_the_fifth_is_kept_for_level_5_sections_when_the_document_has_one() -> None:
    body = "# A\n\n## B\n\n### C\n\n#### D\n\n##### E\n\n- five\n  - nested\n- six\n"
    tree, plan, diagnostics = planned(body, style="continental")
    five, nested, six = items(tree)
    assert shown(plan, five, six) == [(2, 5, "1.1.1.1.1.1"), (2, 5, "1.1.1.1.1.2")]
    assert plan.abstract_nums[0].levels[5].lvl_text == "%1.%2.%3.%4.%5.%6"
    # the nested list falls back: its slot is the level-5 section's
    assert plan.units[nested.unit].cause == ("restart-section",) and plan.units[nested.unit].ilvl == 1
    # a document without a level-5 section lets the nested list have it
    _, other, _ = planned("# A\n\n- x\n  - y\n", style="continental")
    assert other.abstract_nums[0].levels[5].lvl_text == "%6)"


def test_a_level_a_different_label_has_taken_is_not_shared() -> None:
    # the numbered paragraphs under a level-5 section need another text on the level after the section's than the
    # items: the second kind falls back, and says so
    body = "# A\n\n## B\n\n### C\n\n#### D\n\n##### E\n\n- five\n\nPara.\n"
    levels = {"paragraphs.numbered": True, "paragraphs.format": {"counter": "decimal", "label": "{section}-{n}",
                                                                 "ref": "-{n}"}}
    tree, plan, diagnostics = planned(body, style="continental", overrides=levels)
    [paragraph] = [u for u in units(tree) if isinstance(u, Paragraph)]
    assert plan.units[paragraph.unit].cause == ("label-fallback",) and plan.units[paragraph.unit].label == "-1"
    assert plan.units[items(tree)[0].unit].ilvl == 5
    assert rules(diagnostics) == {"render-docx-section-label"}


def test_a_label_the_heading_list_cannot_show_is_written_without_the_section_and_reported() -> None:
    levels = [{"counter": "decimal", "label": "{section}.{n}", "ref": ".{n}"},
              {"counter": "lower-alpha", "label": "{n})", "ref": "({n})"}]
    tree, plan, diagnostics = planned("# A\n\n- x\n  - y\n", style="outline", overrides={"enumeration.levels": levels})
    x, y = items(tree)
    assert (x.label, plan.units[x.unit].label, plan.units[x.unit].cause) == ("I.1", "1", ("label-fallback",))
    assert [a.kind for a in plan.abstract_nums] == ["headings", "enumerated"] and plan.units[x.unit].ind is None
    [found] = diagnostics
    assert found.rule == "render-docx-section-label" and found.level == "warning" and "'1'" in found.message
    assert found.line == x.line


def test_under_the_none_scheme_the_label_that_needs_the_section_is_written_without_it() -> None:
    tree, plan, diagnostics = planned("# A\n\n- x\n", style="continental", overrides=NONE)
    [x] = items(tree)
    assert (x.label, plan.units[x.unit].label, plan.units[x.unit].cause) == ("A.1", "1", ("label-fallback",))
    assert rules(diagnostics) == {"render-docx-section-label"}


def test_the_items_of_a_quote_do_not_ride_the_heading_list() -> None:
    tree, plan, diagnostics = planned("# A\n\n> - quoted\n\n- real\n", style="continental")
    quoted, real = items(tree)
    assert plan.units[quoted.unit].cause == ("label-fallback",) and plan.units[quoted.unit].label == "1"
    assert shown(plan, real)[0][1:] == (1, "1.1")
    assert rules(diagnostics) == {"render-docx-section-label"}


def test_numbered_paragraphs_whose_label_needs_the_section_ride_the_heading_list_too() -> None:
    tree, plan, diagnostics = planned("# A\n\nOne.\n\nTwo.\n\n## B\n\nThree.\n", overrides=PARAGRAPHS)
    one, two, three = [u for u in units(tree) if isinstance(u, Paragraph)]
    assert [(plan.units[p.unit].num_id, plan.units[p.unit].ilvl, plan.units[p.unit].label) for p in (one, two, three)] \
        == [(2, 1, "1.1"), (2, 1, "1.2"), (4, 2, "1.1.1")]
    assert [a.kind for a in plan.abstract_nums] == ["headings"]
    # B's own number goes on from Word's counter of the level unless restarted
    assert plan.units["s2"].cause == ("resync",) and rules(diagnostics) == {"render-docx-shared-counter"}


def test_items_in_the_middle_of_numbered_paragraphs_share_their_counter_and_the_paragraph_after_is_restarted() -> None:
    # continental's paragraphs are (1), a list of their own; made {section}.{n} they share the level with the items
    levels = {"paragraphs.numbered": True, "paragraphs.format": {"counter": "decimal", "label": "{section}.{n}",
                                                                 "ref": ".{n}"}}
    tree, plan, diagnostics = planned("# A\n\nOne.\n\n- x\n- y\n\nTwo.\n", style="continental", overrides=levels)
    one, x, y, two = units(tree)[1:]
    assert [(plan.units[u.unit].label, plan.units[u.unit].cause) for u in (one, x, y, two)] == [
        ("1.1", ()), ("1.1", ()), ("1.2", ()), ("1.2", ("resync",))]  # the renderer counts them apart
    assert plan.nums[plan.units[two.unit].num_id - 1].start_overrides == ((1, 2),)
    assert rules(diagnostics) == {"render-docx-shared-counter"}


def test_an_item_nested_under_a_foreign_parent_reports_a_section_label_once() -> None:
    levels = [{"counter": "lower-alpha", "label": "({n})", "ref": "({n})"},
              {"counter": "decimal", "label": "{path}", "ref": "({n})"}]
    tree, plan, diagnostics = planned("# A\n\n1. a\n   - x\n", overrides={"enumeration.levels": levels})
    _, nested = items(tree)
    assert plan.units[nested.unit].cause == ("restart-family", "label-fallback")
    assert [d.rule for d in diagnostics].count("render-docx-section-label") == 1


def test_a_quote_inside_a_preamble_item_with_a_path_label_is_reported_not_left_unexplained() -> None:
    levels = [{"counter": "lower-alpha", "label": "({n})", "ref": "({n})"},
              {"counter": "decimal", "label": "{path}", "ref": "({n})"}]
    tree, plan, diagnostics = planned("- a\n\n  > - quoted\n\n# A\n", overrides={"enumeration.levels": levels})
    _, quoted = items(tree)
    assert quoted.parent_unit is None
    assert plan.units[quoted.unit].cause == ("label-fallback",) and plan.units[quoted.unit].label == "(a)(1)"
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


# -- Alternatives that share a number (§15.8) -------------------------------------------------------------------

SECTIONS = ("# Intro\n\n# D {#d when=forum:courts}\n\n## Sub {#s1}\n\n## Sub two {#s2}\n\n# D {#d when=forum:arb}\n\n"
            "## Sub alt\n\n- q\n\n# D {#d when=forum:expert}\n\n## Sub exp\n\n## Sub exp two\n\n## Sub exp three\n\n"
            "# Last\n\n## L1\n")


def segments_of(plan: DocxPlan, unit: str) -> str:
    alt = plan.units[unit].alt
    assert alt is not None
    return show(plan, alt.segments)


def test_a_later_alternative_section_has_no_number_of_its_own_and_shows_the_holders_as_a_field() -> None:
    body = "# Disputes {#d when=forum:courts}\n\n# Disputes {#d when=forum:arb}\n\n# Last\n"
    tree, plan, diagnostics = planned(body, ALTERNATIVES)
    assert tree.sections[1].shares_number_with == "s1"
    first, second, last = (plan.units[s] for s in ("s1", "s2", "s3"))
    assert (second.num_id, second.ilvl, second.explicit, second.count, second.cause) == (0, 0, True, None, ("alternative",))
    assert (second.label, second.word_ref, second.ind) == ("1.", "1", (567, 567))
    assert (first.label, last.label, last.count) == ("1.", "2.", 2) and last.num_id == first.num_id
    assert segments_of(plan, "s2") == 'REF d + "."' and second.alt.suff == "tab"
    segment, period = second.alt.segments
    assert (segment.number, segment.hyperlink, segment.suppress_text, segment.cached) == (True, True, False, "1")
    assert period == Literal_(".") and segment.bookmark == plan.units["s1"].bookmarks[0]
    assert rules(diagnostics) == {"render-docx-alternative"}


def test_the_text_after_the_number_of_a_later_alternative_is_the_label_beyond_what_the_field_returns() -> None:
    body = "# D {#d when=forum:courts}\n\n# D {#d when=forum:arb}\n"
    _, plan, _ = planned(body, ALTERNATIVES, overrides={"numbering.scheme": "legal-outline"})
    assert (show(plan, plan.units["s2"].alt.segments), plan.units["s2"].label) == ('REF d + "."', "I.")
    # REF \n drops periods only: a label that ends otherwise is all the field's
    _, plan, _ = planned(body, ALTERNATIVES, overrides={"numbering.scheme": "mixed"})
    assert (show(plan, plan.units["s2"].alt.segments), plan.units["s2"].label) == ('REF d + "."', "1.")
    _, plan, _ = planned("# A\n\n- a {#i when=forum:courts}\n- b {#i when=forum:arb}\n", ALTERNATIVES)
    assert (show(plan, plan.units["u2"].alt.segments), plan.units["u2"].label) == ("REF i", "(a)")


def test_a_reference_to_the_shared_identifier_is_the_first_alternatives() -> None:
    body = "# D {#d when=forum:courts}\n\n# D {#d when=forum:arb}\n\nSee {{ref: d}}.\n"
    tree, plan, _ = planned(body, ALTERNATIVES)
    assert list(plan.refs) == ["s1"] and show(plan, plan.refs["s1"].segments) == "REF d"
    assert plan.units["s2"].bookmarks == ()  # the holder carries the bookmark, the alternative has none of its own


def test_the_holder_of_an_alternatives_number_has_a_bookmark_even_when_nothing_refers_to_it() -> None:
    _, plan, _ = planned("# D\n\n- a {#i when=forum:courts}\n- b {#i when=forum:arb}\n", ALTERNATIVES)
    [segment] = plan.units["u2"].alt.segments
    assert plan.units["u1"].bookmarks == (segment.bookmark,) and segment.bookmark.startswith("ld_")
    _, plan, _ = planned("# D\n\n- a {when=forum:courts}\n- b {when=forum:arb}\n", ALTERNATIVES)
    assert plan.units["u2"].alt is None  # no identifier: no alternative
    _, plan, _ = planned("# D {when=forum:courts}\n\n# D2 {when=forum:arb}\n", ALTERNATIVES)
    assert plan.units["s2"].alt is None


def test_the_sections_under_a_later_alternative_start_in_a_num_of_their_own() -> None:
    tree, plan, diagnostics = planned(SECTIONS, ALTERNATIVES)
    sub, sub_two, alt, sub_alt, alt_three, exp, exp_two, exp_three, last, l1 = (
        plan.units[s] for s in ("s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11", "s12"))
    assert [s.label for s in (sub, sub_two, sub_alt, exp, exp_two, exp_three, last, l1)] == [
        "2.1", "2.2", "2.1", "2.1", "2.2", "2.3", "3.", "3.1"]
    assert (alt.num_id, alt_three.num_id) == (0, 0) and alt.label == alt_three.label == "2."
    # both holders' numbers are the first alternative's, the field points at it
    assert segments_of(plan, "s5") == segments_of(plan, "s7") == 'REF d + "."'
    # the first alternative's children are on the headings' num; each later one's on a fresh num that starts at its level
    assert {sub.num_id, sub_two.num_id, last.num_id} == {1} and sub_alt.num_id != 1 and exp.num_id not in (1, sub_alt.num_id)
    assert (sub_alt.explicit, exp.explicit, sub.explicit) == (True, True, False)
    assert [plan.nums[n - 1].start_overrides for n in (sub_alt.num_id, exp.num_id)] == [((1, 1),), ((1, 1),)]
    assert exp.num_id == exp_two.num_id == exp_three.num_id
    # the section after the alternatives goes back to the headings' num: it goes on from the holder's counter
    assert (last.count, l1.count) == (3, 1) and l1.num_id == 1 and last.explicit is False
    assert all(d.level == "info" for d in diagnostics)


def test_an_alternative_that_is_not_the_first_to_be_numbered_takes_no_num_for_its_children_until_they_come() -> None:
    _, plan, _ = planned("# D {#d when=forum:courts}\n\n# D {#d when=forum:arb}\n\n# E\n", ALTERNATIVES)
    assert [n.id for n in plan.nums] == [1]  # no num made for sections that never come


def test_an_alternative_below_a_later_alternative_whose_number_is_live_there_is_numbered_by_word() -> None:
    body = ("# D {#d when=forum:courts}\n\n## E {#e when=forum:courts}\n\n### F {#f}\n\n"
            "# D {#d when=forum:arb}\n\n## E {#e when=forum:arb}\n\n### F2\n\n## G\n\n### H\n")
    tree, plan, _ = planned(body, ALTERNATIVES)
    assert [(plan.units[s.unit].label, plan.units[s.unit].num_id, bool(plan.units[s.unit].alt)) for s in tree.sections] == [
        ("1.", 1, False), ("1.1", 1, False), ("1.1.1", 1, False), ("1.", 0, True), ("1.1", 2, False),
        ("1.1.1", 2, False), ("1.2", 2, False), ("1.2.1", 2, False)]
    # the alternative's first section starts the fresh num with the number it shares: no field is needed for it
    assert plan.units["s5"].explicit and plan.nums[1].start_overrides == ((1, 1),)


def test_an_item_that_repeats_the_number_of_the_one_before_is_numbered_by_a_field() -> None:
    body = "# S\n\n- a {#i when=forum:courts}\n- b {#i when=forum:arb}\n- c\n"
    tree, plan, diagnostics = planned(body, ALTERNATIVES)
    a, b, c = items(tree)
    assert (b.shares_number_with, plan.units[b.unit].num_id, plan.units[b.unit].label) == (a.unit, 0, "(a)")
    assert (plan.units[b.unit].ilvl, plan.units[b.unit].ind, plan.units[b.unit].cause) == (0, (567, 567), ("alternative",))
    assert shown(plan, a, c) == [(2, 0, "(a)"), (2, 0, "(b)")] and plan.units[c.unit].count == 2
    assert segments_of(plan, b.unit) == "REF i" and rules(diagnostics) == {"render-docx-alternative"}


def test_the_children_of_a_later_alternative_item_get_a_num_of_their_own_and_it_is_reported_as_information() -> None:
    body = "# S\n\n- a {#i when=forum:courts}\n  - x\n- b {#i when=forum:arb}\n  - y\n  - z\n- c\n"
    tree, plan, diagnostics = planned(body, ALTERNATIVES)
    _, x, _, y, z, _ = items(tree)
    assert shown(plan, x, y, z) == [(2, 1, "(i)"), (3, 1, "(i)"), (3, 1, "(ii)")]
    assert plan.units[y.unit].cause == ("restart-alternative",) and plan.units[z.unit].cause == ()
    assert plan.nums[2].start_overrides == ((1, 1),)
    found = [d for d in diagnostics if d.rule == "render-docx-static-restart"][0]
    assert (found.rule, found.level, found.line) == ("render-docx-static-restart", "info", y.line)
    assert "alternative" in found.message and rules(diagnostics) == {"render-docx-static-restart", "render-docx-alternative"}


def test_a_paragraph_that_repeats_the_number_of_the_one_before_is_numbered_by_a_field() -> None:
    body = "# S\n\nOne. {#p when=forum:courts}\n\nOne arb. {#p when=forum:arb}\n\nTwo.\n"
    tree, plan, diagnostics = planned(body, ALTERNATIVES, overrides=PARAGRAPHS)
    one, arb, two = [u for u in units(tree) if isinstance(u, Paragraph)]
    assert [(plan.units[u.unit].num_id, plan.units[u.unit].label) for u in (one, arb, two)] == [
        (2, "1.1"), (0, "1.1"), (2, "1.2")]
    assert plan.units[arb.unit].ilvl == 1 and plan.units[two.unit].count == 2
    assert rules(diagnostics) == {"render-docx-alternative"}


def test_alternatives_on_the_heading_list_carry_the_indent_of_the_unit_not_of_the_heading() -> None:
    body = "# S\n\n- a {#i when=forum:courts}\n  - n\n- b {#i when=forum:arb}\n  - m\n"
    tree, plan, diagnostics = planned(body, ALTERNATIVES, style="continental")
    a, n, b, m = items(tree)
    heading_level = plan.abstract_nums[0].levels[1]
    assert plan.units[b.unit].num_id == 0 and plan.units[b.unit].ilvl == 1
    assert plan.units[b.unit].ind == (heading_level.left, heading_level.hanging) == plan.units[a.unit].ind
    # the children of the alternative restart on a fresh num of the heading list's reserved level
    assert (plan.units[m.unit].ilvl, plan.units[m.unit].cause) == (5, ("restart-alternative", "reserved-level"))
    assert plan.units[m.unit].num_id not in (plan.units[n.unit].num_id, 1)
    assert plan.nums[plan.units[m.unit].num_id - 1].start_overrides == ((5, 1),)


def test_a_designation_below_an_alternative_is_made_of_the_holders_counters() -> None:
    body = ("# D {#d when=forum:courts}\n\n- a {#a when=forum:courts}\n- b {#a when=forum:arb}\n  - deep {#deep}\n\n"
            "# D {#d when=forum:arb}\n\n- z {#z}\n\nSee {{ref: deep}} {{ref: z}}.\n")
    tree, plan, diagnostics = planned(body, ALTERNATIVES, style="continental")
    texts = {i.target_unit: i.text for i in iter_tree_inlines(tree) if isinstance(i, CrossRef)}
    assert [(texts[t], show(plan, plan.refs[t].segments)) for t in plan.refs] == [
        ("1.1(a)", 'REF a + "(" + REF deep'), ("1.1", "REF z")]
    assert all(r.exact and not r.fixed for r in plan.refs.values())
    assert "render-docx-reference" not in rules(diagnostics)


def test_a_unit_that_shares_a_number_but_finds_another_count_in_the_level_is_restarted_not_made_an_alternative() -> None:
    # Two clauses of the later alternative's sub-section counted the level of the heading list between: the number the
    # sub-sub-section shares is not the one the level holds, so Word's counter is restarted at it instead of a field
    # standing for a unit that is not where it is.
    body = ("# D {#d when=forum:courts}\n\n## Sub {#s}\n\n### Deeper {#t}\n\n# D {#d when=forum:arb}\n\n"
            "## Sub alt\n\n- clause one\n- clause two\n\n### Deeper alt\n")
    tree, plan, diagnostics = planned(body, ALTERNATIVES, style="continental")
    deeper = tree.sections[5]
    assert deeper.shares_number_with == "s3"
    mine = plan.units[deeper.unit]
    assert (mine.alt, mine.cause, mine.label, mine.count, mine.explicit) == (None, ("resync",), "1.1.1", 1, True)
    assert mine.num_id not in (0, None) and plan.nums[mine.num_id - 1].start_overrides == ((2, 1),)
    assert "render-docx-shared-counter" in rules(diagnostics)


def test_the_holder_is_the_unit_that_holds_the_level_now_not_always_the_first_alternative() -> None:
    # A clause on the same level of the heading list counted between the two paragraphs that share a number: the level
    # holds the clause's 1.1 now, and the later alternative shows that (the designations below it are made of the
    # counters the level holds), so it refers to the clause. Nothing is off: the text is the same.
    levels = {"paragraphs.numbered": True, "paragraphs.format": {"counter": "decimal", "label": "{section}.{n}",
                                                                 "ref": ".{n}"}}
    body = "# S\n\nPara. {#p when=forum:courts}\n\n- clause {#i}\n\nPara arb. {#p when=forum:arb}\n\nAfter.\n"
    tree, plan, diagnostics = planned(body, ALTERNATIVES, style="continental", overrides=levels)
    first, clause, later, after = [u for u in units(tree)][1:]
    assert plan.units[later.unit].num_id == 0 and plan.units[later.unit].label == "1.1"
    assert plan.units[later.unit].alt.segments[0].bookmark == plan.units[clause.unit].bookmarks[0]
    assert plan.units[first.unit].bookmarks == ("ld_p",) and plan.units[after.unit].label == "1.2"
    assert "render-docx-number-mismatch" not in rules(diagnostics)


def test_the_alternatives_of_a_document_are_one_information_naming_the_first() -> None:
    body = ("# D {#d when=forum:courts}\n\n# D {#d when=forum:arb}\n\n# E {#e when=forum:courts}\n\n"
            "# E {#e when=forum:arb}\n\n# E {#e when=forum:expert}\n")
    tree, plan, diagnostics = planned(body, ALTERNATIVES)
    [found] = [d for d in diagnostics if d.rule == "render-docx-alternative"]
    assert found.level == "info" and found.line == tree.sections[0].line
    assert "3 alternative(s)" in found.message and "'1.'" in found.message and "deleting that paragraph" in found.message
    assert f"line {tree.sections[0].line}" in found.message and "condition values" in found.message
    _, _, diagnostics = planned("# A\n\n# B\n", ALTERNATIVES)
    assert "render-docx-alternative" not in rules(diagnostics)


def test_the_message_about_a_restarted_unit_says_what_an_edit_does_to_it() -> None:
    _, _, diagnostics = planned(CONTINENTAL_ITEMS, style="continental")
    [heading] = [d for d in diagnostics if d.rule == "render-docx-shared-counter"]
    assert "if it is deleted in Word the headings after it read higher" in heading.message
    levels = {"paragraphs.numbered": True, "paragraphs.format": {"counter": "decimal", "label": "{section}.{n}",
                                                                 "ref": ".{n}"}}
    _, _, diagnostics = planned("# A\n\nOne.\n\n- x\n- y\n\nTwo.\n", style="continental", overrides=levels)
    [paragraph] = [d for d in diagnostics if d.rule == "render-docx-shared-counter"]
    assert "inserted or deleted before it in Word does not move it" in paragraph.message


def test_the_contents_show_the_number_the_alternative_shares() -> None:
    body = "# D {#d when=forum:courts}\n\n## S\n\n# D {#d when=forum:arb}\n\n## S2\n\n# Last\n"
    _, plan, _ = planned(body, ALTERNATIVES, overrides={"contents.enabled": True, "contents.depth": 2})
    assert [(e.unit, e.number, e.sep) for e in plan.toc] == [
        ("s1", "1.", "\t"), ("s2", "1.1", "\t"), ("s3", "1.", "\t"), ("s4", "1.1", "\t"), ("s5", "2.", "\t")]
    assert plan.units["s3"].toc_bookmark == plan.toc[2].bookmark


def test_the_items_on_the_heading_list_are_not_in_the_contents() -> None:
    body = "# A\n\n- x\n  - y\n\n## B\n\n- z\n"
    tree, plan, _ = planned(body, style="continental", overrides={"contents.enabled": True, "contents.depth": 5})
    assert [e.unit for e in plan.toc] == ["s1", "s2"]
    assert all(plan.units[i.unit].toc_bookmark is None for i in items(tree))


def test_an_alternative_that_starts_a_section_with_its_own_shared_number_repeats_it_by_a_live_counter() -> None:
    # the children of a later alternative section repeat the numbers of the earlier one's: they are numbered by Word
    # (on the fresh num) and are not alternatives themselves
    tree, plan, _ = planned(SECTIONS, ALTERNATIVES)
    for unit in ("s6", "s8", "s9"):
        assert tree.sections[int(unit[1:]) - 1].shares_number_with in ("s3", "s4", None)
        assert plan.units[unit].count is not None and not plan.units[unit].alt


def test_drift_is_reported_once_for_a_run_of_units_counted_off_by_the_same_amount() -> None:
    # not a finding the planner makes for a document it can place (a unit Word would number differently is restarted
    # or made a field), but the catch-all that tells of a bug in the placement: it groups runs
    tree, _, _ = planned("# A\n\n" + "".join(f"- i{n}\n" for n in range(6)))
    planner = _Planner(tree, None)
    nodes = items(tree)
    for node in nodes:
        step = Step(node.number + 1, "", "", "", ())
        planner.check(node, "list item", step, "decimal", ("list", "u1"), (), (0, 0))
    for drift in planner.drifts:
        planner.report_drift(drift)
    [found] = planner.diagnostics
    assert found.rule == "render-docx-number-mismatch" and found.line == nodes[0].line
    assert "1 later than the renderer" in found.message and "the following 5" in found.message


def test_a_different_drift_is_a_finding_of_its_own() -> None:
    tree, _, _ = planned("# A\n\n" + "".join(f"- i{n}\n" for n in range(4)))
    planner = _Planner(tree, None)
    for node, delta in zip(items(tree), (1, 1, 2, 2), strict=True):
        planner.check(node, "list item", Step(node.number + delta, "", "", "", ()), "decimal", ("list", "u1"), (), (0, 0))
    for drift in planner.drifts:
        planner.report_drift(drift)
    assert len(planner.diagnostics) == 2 and "1 later" in planner.diagnostics[0].message
    assert "2 later" in planner.diagnostics[1].message


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


@functools.cache
def planned_run(source: str, variant: str) -> tuple:
    """*source* rendered under *variant* and planned: (result, plan, diagnostics). The property tests of the numbering
    and of the references look at the same documents, so each is rendered and planned once."""
    result = render(source, format="text", **VARIANTS[variant])
    plan, diagnostics = plan_docx(result.tree)
    return result, plan, diagnostics


def assert_planned_as_resolved(result, plan, diagnostics) -> None:  # noqa: ANN001
    """The property, unit by unit: Word's count is the resolver's number, and Word's label is the resolver's label,
    unless that unit's own counter reads differently in Word (a letter past 26, a Roman numeral from 4000: its label
    and those of its descendants may differ), its label cannot show what the resolver's does (the plan says so on the
    unit), or its depth is past Word's. A later alternative that shares a number has no counter of its own (``numId`` 0,
    its number the holder's, as fields) and reads as the resolver's label. There is no other exception, and a
    diagnostic is reported exactly where there is one."""
    tree = result.tree
    found = rules(diagnostics)
    # A document that skips a heading level (a Core Error, which the renderer still draws) is the one exception: Word
    # is expected to read the level that was skipped as 0 where the renderer and LibreOffice count it as 1, and the
    # catch-all ``render-docx-number-mismatch`` may then report units Word counts off.
    skips = any("skipped-level" in unit.cause for unit in plan.units.values())
    overflows: dict[str, str] = {}  # unit -> the rule its counter reads differently under (itself or an ancestor)
    parents = {unit.unit: unit.parent_unit for unit in units(tree)}
    own_rules: set[str] = set()
    for node, counter in numbered(tree):
        mine = plan.units[node.unit]
        clamped = "clamped" in mine.cause
        alternative = node.shares_number_with is not None and mine.num_id == 0
        if alternative:
            assert mine.count is None and "alternative" in mine.cause and mine.explicit and mine.ind is not None
            assert mine.ilvl is not None and mine.label.rstrip(".") == mine.word_ref
        elif not skips:
            assert mine.count == node.number, (node.unit, mine.count, node.number)
        own = ""
        if not clamped and word_counter(node.number, NUM_FMT[counter]) != format_counter(node.number, counter):
            own = "render-docx-roman-overflow" if counter.endswith("roman") else "render-docx-letter-overflow"
        inherited = overflows.get(parents[node.unit] or "", "") or ""
        overflows[node.unit] = own or inherited
        if not (own or inherited or clamped or "label-fallback" in mine.cause):
            assert mine.label == node.label, (node.unit, mine.label, node.label)
        assert mine.cause.count("label-fallback") <= 1, (node.unit, mine.cause)
        for cause, rule in (("label-fallback", "render-docx-section-label"), ("clamped", "render-docx-list-depth"),
                            ("resync", "render-docx-shared-counter")):
            if cause in mine.cause:
                assert rule in found, (node.unit, cause)
        own_rules.update([own] if own else [])
    assert found & {"render-docx-roman-overflow", "render-docx-letter-overflow"} == own_rules
    assert skips or "render-docx-number-mismatch" not in found, "a placement is wrong: " + "; ".join(
        d.message for d in diagnostics if d.rule == "render-docx-number-mismatch")
    assert ("render-docx-alternative" in found) == any(u.alt is not None for u in plan.units.values())
    assert all(d.rule.startswith("render-docx-") for d in diagnostics)
    assert {u.unit for u in units(tree)} == set(plan.units)
    # Every num with an override is used, by a unit whose level the override names.
    first: dict[int, str] = {}
    for unit in units(tree):
        mine = plan.units[unit.unit]
        if mine.num_id:
            first.setdefault(mine.num_id, unit.unit)
    for num in plan.nums:
        assert num.id in first, f"num {num.id} is never used"
        if num.start_overrides:
            assert plan.units[first[num.id]].ilvl == num.start_overrides[0][0]
    # A unit carries an indent of its own where its level's serves another (a unit that is no heading on the heading
    # list), where it has no level to take it from (a later alternative), and where it is nested in a unit that does
    # (one step in from it, whatever list it is on).
    headings = next((a.id for a in plan.abstract_nums if a.kind == "headings"), None)
    nums = {n.id: n.abstract_id for n in plan.nums}
    by_bookmark = {name: u.unit for u in plan.units.values() for name in u.bookmarks}
    indented: set[str] = set()  # the units that stand where an indent of their own is the one their text starts at
    for unit in units(tree):
        mine = plan.units[unit.unit]
        on_headings = mine.num_id is not None and nums.get(mine.num_id) == headings and not isinstance(unit, Section)
        if mine.alt is not None:  # a later alternative stands where the unit it repeats does
            on_headings = by_bookmark[mine.alt.segments[0].bookmark] in indented
        if not isinstance(unit, Section) and (on_headings or unit.parent_unit in indented):
            indented.add(unit.unit)
        assert (mine.ind is not None) == (unit.unit in indented or mine.num_id == 0), (unit.unit, mine.ind, mine.num_id)
        assert (mine.alt is not None) == (mine.num_id == 0)


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("name", ["features.lgd", "template.lgd"])
def test_the_test_documents_are_planned_as_resolved(name: str, variant: str) -> None:
    source = (DOCUMENTS / name).read_text(encoding="utf-8")
    assert_planned_as_resolved(*planned_run(source, variant))


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
            run = planned_run(path.read_text(encoding="utf-8"), variant)
        except DocumentError:
            continue
        try:
            assert_planned_as_resolved(*run)
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


def _alternative_list(rng: random.Random, index: int, indent: str, depth: int) -> list[str]:
    """A list whose first item has two or three alternatives, some with lists of their own (children that differ in
    number between the alternatives), among other items."""
    lines: list[str] = []
    for way in range(rng.choice([2, 3])):
        lines.append(f"{indent}- item {way} {{#i{index}d{depth} when=forum:{FORUMS[way]}}}")
        if depth < 3 and rng.random() < 0.6:
            lines.extend(_random_list(rng, depth + 1, indent + "  ", 2))
        if depth < 2 and rng.random() < 0.3:
            lines.extend(_alternative_list(rng, index, indent + "  ", depth + 1))
    lines.append(f"{indent}- after")
    if depth < 3 and rng.random() < 0.5:
        lines.extend(_random_list(rng, depth + 1, indent + "  ", 2))
    return lines + [""]


def _random_alternatives(rng: random.Random) -> str:
    """Sections, items and paragraphs that share a number, in twos and threes, with children of their own and with
    other units around them."""
    out: list[str] = []
    for index in range(rng.randint(2, 6)):
        out.append(f"# Section {index} {{#h{index}}}\n")
        if rng.random() < 0.5:
            ways = rng.choice([2, 3])
            out.append("\n\n".join(f"Para {way}. {{#p{index} when=forum:{FORUMS[way]}}}" for way in range(ways))
                       + "\n\nAfter.\n")
        if rng.random() < 0.6:
            out.append("\n".join(_alternative_list(rng, index, "", 0)))
        if rng.random() < 0.5:
            for way in range(rng.choice([2, 3])):
                out.append(f"# Disputes {{#d{index} when=forum:{FORUMS[way]}}}\n")
                for sub in range(rng.randint(0, 3)):
                    out.append(f"## Sub {way}.{sub} {{#s{index}w{way}n{sub}}}\n")
                    if rng.random() < 0.4:
                        out.extend(_random_list(rng, 0, "", 2))
                    if rng.random() < 0.3:
                        out.append(f"### Deeper {{#t{index}w{way}n{sub}}}\n")
                if rng.random() < 0.4:
                    out.append("Text.\n\n- an item\n")
    return "\n".join(out)


#: The random documents: one set for the numbering and the references. Each has an anchor on every unit and a
#: paragraph that refers to up to 40 of them (a different part of them in each document, so that every unit is
#: referred to by some document), and each variant plans its own sample of them, once.
RANDOM_DOCUMENTS = 36
ALTERNATIVE_DOCUMENTS = 24
PER_VARIANT = 9


@functools.cache
def _random_sources() -> tuple[str, ...]:
    rng = random.Random(20261028)
    return tuple(with_references(with_anchors(_random_document(rng)), shift=index) for index in range(RANDOM_DOCUMENTS))


@functools.cache
def _alternative_sources() -> tuple[str, ...]:
    rng = random.Random(7)
    return tuple(ALTERNATIVES + with_references(_random_alternatives(rng), shift=index)
                 for index in range(ALTERNATIVE_DOCUMENTS))


def sample(variant: str, count: int, sources: tuple[str, ...]) -> list[str]:
    """*count* of *sources*, a different choice for each variant."""
    return [sources[i] for i in sorted(random.Random(variant).sample(range(len(sources)), count))]


@functools.cache
def random_runs(variant: str) -> tuple:
    """(source, result, plan, diagnostics) of the random documents planned under *variant*."""
    return tuple((source, *planned_run(FRONT + source, variant))
                 for source in sample(variant, PER_VARIANT, _random_sources()))


@functools.cache
def alternative_runs(variant: str) -> tuple:
    return tuple((source, *planned_run(source, variant))
                 for source in sample(variant, PER_VARIANT, _alternative_sources()))


@pytest.mark.parametrize("variant", VARIANTS)
def test_random_documents_are_planned_as_resolved(variant: str) -> None:
    for source, result, plan, diagnostics in random_runs(variant):
        try:
            assert_planned_as_resolved(result, plan, diagnostics)
        except AssertionError as error:
            raise AssertionError(f"{error}\n--- document ---\n{source}") from error


@pytest.mark.parametrize("variant", VARIANTS)
def test_random_alternatives_are_planned_as_resolved(variant: str) -> None:
    for source, result, plan, diagnostics in alternative_runs(variant):
        try:
            assert_planned_as_resolved(result, plan, diagnostics)
        except AssertionError as error:
            raise AssertionError(f"{error}\n--- document ---\n{source}") from error


def test_planning_twice_gives_the_same_plan() -> None:
    for variant in ("default", "continental", "none"):
        for _, result, plan, diagnostics in random_runs(variant)[:4]:
            assert plan_docx(result.tree) == (plan, diagnostics)


def test_the_random_documents_reach_every_case_the_property_is_about() -> None:
    seen_rules: set[str] = set()
    seen_causes: set[str] = set()
    depth = 0
    for variant in VARIANTS:
        for _, result, plan, diagnostics in random_runs(variant):
            seen_rules |= rules(diagnostics)
            seen_causes |= {cause for unit in plan.units.values() for cause in unit.cause}
            depth = max(depth, max((lst.depth for lst in lists(result.tree)), default=0))
    for variant in VARIANTS:
        for _, _, plan, diagnostics in alternative_runs(variant):
            seen_rules |= rules(diagnostics)
            seen_causes |= {cause for unit in plan.units.values() for cause in unit.cause}
    assert {"render-docx-letter-overflow", "render-docx-section-label", "render-docx-static-restart",
            "render-docx-list-depth", "render-docx-shared-counter"} <= seen_rules
    assert {"restart-family", "restart-section", "restart-alternative", "resync", "label-fallback", "skipped-level",
            "clamped", "alternative", "reserved-level"} <= seen_causes
    assert depth >= 10


def test_documents_of_one_size_refer_to_different_units_so_that_more_of_them_are_referred_to() -> None:
    groups: dict[int, list[set[str]]] = {}
    for source in _random_sources():
        stride = -(-len(re.findall(r"\{#([a-z][a-z0-9-]*)", source)) // 40) or 1
        groups.setdefault(stride, []).append(set(re.findall(r"\{\{ref: ([a-z][a-z0-9-]*)", source)))
    spread = [members for stride, members in groups.items() if stride > 1 and len(members) > 1]
    assert spread
    for members in spread:
        assert len(set().union(*members)) > max(len(refs) for refs in members)


@pytest.mark.parametrize("variant", ["default", "continental", "paragraphs", "mixed", "article"])
def test_a_reference_is_fixed_text_only_where_a_heading_level_is_skipped_or_the_depth_is_past_word_s(variant: str) -> None:
    """The cover of every reference exists for a document without errors and a list within Word's nine levels: what
    cannot be covered are the references below a skipped heading level (a Core Error: the number of the level the
    heading skips has no unit to be a field of) and below a list nested past nine levels."""
    for source, result, plan, _ in (*random_runs(variant), *alternative_runs(variant)):
        skipped = any("skipped-level" in unit.cause for unit in plan.units.values())
        if not skipped and max((lst.depth for lst in lists(result.tree)), default=0) <= 8:
            assert not [t for t, r in plan.refs.items() if r.fixed], source


def test_each_variant_plans_its_own_sample_of_the_random_documents() -> None:
    samples = {tuple(sample(variant, PER_VARIANT, _random_sources())) for variant in VARIANTS}
    assert len(samples) == len(VARIANTS)


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
CL_PERIOD = {"numbering.levels": [
    {"counter": "decimal", "label": "Čl. {n}", "ref": "{n}"}, {"counter": "decimal", "label": "{path}", "ref": ".{n}"}],
    "references.format": "Čl. {designation}"}
SECTION_WORD = {"numbering.levels": [
    {"counter": "upper-roman", "label": "Article {n}", "ref": "{n}"},
    {"counter": "decimal", "label": "Section {path}", "ref": ".{n}"}], "references.format": "Section {designation}"}


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
    # continental: an item rides the heading list's next level (%1.%2), so one field is its whole number; an item
    # nested in an item is written "b)" and referred to as "(b)"
    ("continental", {"style": "continental"}, "i1", "1.1", "REF i1"),
    ("continental", {"style": "continental"}, "n2", "1.2(b)", 'REF i2 + "(" + REF n2'),
    ("continental", {"style": "continental"}, "d1", "1.2(b)(i)", 'REF i2 + "(" + REF n2 + "(" + REF d1'),
    ("continental", {"style": "continental"}, "p1", "1(1)", "REF alpha + REF p1"),
    ("continental", {"style": "continental"}, "gamma", "1.1.1", "REF gamma"),
    # the none scheme: the text of the heading, then the pieces after a space
    ("none", {"overrides": {"numbering.scheme": "none"}}, "alpha", "Alpha", "REF alpha \\h"),
    ("none", {"overrides": {"numbering.scheme": "none"}}, "d1", "Alpha (b) (ii) (A)",
     'REF alpha \\h + " " + REF i2 + " " + REF n2 + " " + REF d1'),
    ("none", {"overrides": {"numbering.scheme": "none"}}, "o2", "Gamma (2)", 'REF gamma \\h + " (" + REF o2 + ")"'),
    # words and symbols in a label that the reference leaves out: \t drops them, the format puts them back
    ("article", {"overrides": {**ARTICLE, "references.format": "Article {designation}"}}, "alpha", "Article I", "REF alpha"),
    ("article", {"overrides": {**ARTICLE, "references.format": "Article {designation}"}}, "i1", "Article I(a)",
     "REF alpha + REF i1"),
    ("article", {"overrides": {**ARTICLE, "references.format": "Article {designation}"}}, "beta", "Article I.1",
     '"Article " + REF beta'),
    ("section-sign", {"overrides": SECTION_SIGN}, "d1", "§ 1(b)(ii)(A)", "REF alpha + REF i2 + REF n2 + REF d1"),
    ("section-sign", {"overrides": SECTION_SIGN}, "gamma", "§ 1.1.1", '"§ " + REF gamma'),
    # the words of the format are the number's own words: a field for the whole number stands for them too
    ("cl-period", {"overrides": CL_PERIOD}, "i1", "Čl. 1(a)", "REF alpha + REF i1"),
    ("cl-period", {"overrides": CL_PERIOD}, "beta", "Čl. 1.1", '"Čl. " + REF beta'),
    ("section-word", {"overrides": SECTION_WORD}, "beta", "Section I.1", "REF beta"),
    ("section-word", {"overrides": SECTION_WORD}, "gamma", "Section I.1.1", '"Section " + REF gamma'),
    # a label that says another word than the format: the words are left out of the number with \t and the format's
    # put back
    ("section-word", {"overrides": SECTION_WORD}, "alpha", "Section I", '"Section " + REF alpha \\t'),
    ("section-word", {"overrides": SECTION_WORD}, "i1", "Section I(a)", '"Section " + REF alpha \\t + REF i1'),
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
    # no field can stand for the missing level: the reference is the renderer's text, right now and not a field
    assert references_of(tree, plan) == [("I.A.1", '"I.A.1"', True)] and plan.refs["s2"].fixed
    [found] = [d for d in diagnostics if d.rule == "render-docx-reference"]
    assert found.level == "warning" and found.line == tree.sections[1].blocks[0].line
    assert found.message.startswith("The cross-reference 'I.A.1' cannot be built from Word's numbering fields: a "
                                    "heading level is skipped before it")
    assert "written as the text 'I.A.1'" in found.message and "not a field" in found.message
    # the decimal scheme writes the whole number in the heading's own field, so nothing is missing there
    tree, plan, diagnostics = planned(body)
    assert references_of(tree, plan) == [("1.1.1", "REF b", True)] and not diagnostics and not plan.refs["s2"].fixed


def test_text_around_a_counter_that_word_cannot_drop_leaves_the_reference_as_one_field() -> None:
    body = "# A\n\n- x {#x}\n\nSee {{ref: x}}.\n"
    for label, shown in (("({n})", "(1)"), ("{n}:", "1:"), ("No. {n}", "No. 1")):
        overrides = {"enumeration.levels": [{"counter": "decimal", "label": label, "ref": "{n}"}]}
        tree, plan, diagnostics = planned(body, overrides=overrides)
        [(text, cover_, exact)] = references_of(tree, plan)
        assert (text, cover_, exact) == ("11", '"11"', True) and plan.refs[next(iter(plan.refs))].fixed
        [found] = [d for d in diagnostics if d.rule == "render-docx-reference"]
        assert f"Word draws its number as '{shown}'" in found.message and "written as the text '11'" in found.message


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
    settings = {"overrides": {**ARTICLE, "references.format": "Art. {designation}"}}
    tree, plan, diagnostics = planned(body, **settings)
    first, second = (plan.refs[i.target_unit].segments for i in iter_tree_inlines(tree) if isinstance(i, CrossRef))
    assert [(s.suppress_text, s.cached, s.cached_lo) for s in first if isinstance(s, FieldSeg)] == [(True, "I", "Article I")]
    assert [(s.suppress_text, s.cached, s.cached_lo) for s in second if isinstance(s, FieldSeg)] == [(True, "II", "Article II")]
    [found] = [d for d in diagnostics if d.rule == "render-docx-reference-word-only"]
    assert found.level == "info" and found.line is None and "'Art. I'" in found.message
    assert "reads 'Art. Article I'" in found.message and "so do 1 more" in found.message
    assert all(plan.refs[i].exact for i in plan.refs)


def test_the_words_of_the_format_that_the_number_holds_too_need_no_switch() -> None:
    """``Article {designation}`` and a label ``Article {n}``: the plain ``REF`` returns ``Article I``, which is the
    whole reference, so there is no ``\\t`` and nothing for LibreOffice to read differently."""
    body = "# A {#a}\n\n# B {#b}\n\nSee {{ref: a}} {{ref: b}}.\n"
    tree, plan, diagnostics = planned(body, overrides={**ARTICLE, "references.format": "Article {designation}"})
    for reference in (plan.refs[i.target_unit] for i in iter_tree_inlines(tree) if isinstance(i, CrossRef)):
        [segment] = reference.segments
        assert isinstance(segment, FieldSeg) and not segment.suppress_text and segment.cached == segment.cached_lo
        assert reference.exact
    assert [segment.cached for i in iter_tree_inlines(tree) if isinstance(i, CrossRef)
            for segment in plan.refs[i.target_unit].segments] == ["Article I", "Article II"]
    assert diagnostics == []


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


# -- Bookmarks that never hold a field, and references that would -----------------------------------------------


def test_a_bookmark_is_a_point_unless_a_field_returns_the_text_it_holds() -> None:
    body = "# A {#a}\n\n- x {#x}\n\nSee {{ref: x}} and {{ref: a}}.\n"
    _, plan, _ = planned(body)
    assert plan.units["s1"].collapsed and plan.units["u1"].collapsed  # numbers only: \n fields
    _, plan, _ = planned(body, overrides=NONE)
    # the heading's text is what the field for it returns: its bookmark holds it; the item's is a point
    assert not plan.units["s1"].collapsed and plan.units["u1"].collapsed


def test_a_heading_that_refers_to_itself_or_in_a_circle_has_those_references_written_as_text() -> None:
    body = ("# A {{ref: b}} {#a}\n\n# B {{ref: a}} {#b}\n\n# C {{ref: c}} {#c}\n\n# D {{ref: e}} {#d}\n\n"
            "# E {#e}\n\nSee {{ref: a}}.\n")
    tree, plan, diagnostics = planned(body, overrides=NONE)
    assert [(u, plan.units[u].literal_refs) for u in ("s1", "s2", "s3", "s4", "s5")] == [
        ("s1", ("s2",)), ("s2", ("s1",)), ("s3", ("s3",)), ("s4", ()), ("s5", ())]  # D's reference to E is no cycle
    found = [d for d in diagnostics if d.rule == "render-docx-reference-cycle"]
    assert [(d.level, d.line) for d in found] == [("warning", tree.sections[i].line) for i in range(3)]
    assert "written as text" in found[0].message and "will not follow edits" in found[0].message
    # outside a heading, and where the number is what is referred to, nothing is a cycle
    _, plan, diagnostics = planned(body)
    assert not any(u.literal_refs for u in plan.units.values())
    assert "render-docx-reference-cycle" not in rules(diagnostics)


def test_a_reference_in_a_heading_to_a_unit_inside_another_heading_is_still_a_field() -> None:
    body = "# A {{ref: x}} {#a}\n\n# B {#b}\n\n- x {#x}\n\n# C {{ref: b}} {#c}\n"
    _, plan, diagnostics = planned(body, overrides=NONE)
    assert not any(u.literal_refs for u in plan.units.values())
    assert "render-docx-reference-cycle" not in rules(diagnostics)


def test_two_cyclic_headings_with_the_same_text_are_both_reported() -> None:
    body = "# Same {{ref: a}} {#a}\n\n# Same {{ref: b}} {#b}\n\n# Same {{ref: c}} {#c}\n"
    tree, plan, diagnostics = planned(body, overrides=NONE)
    found = [d for d in diagnostics if d.rule == "render-docx-reference-cycle"]
    assert [d.line for d in found] == [section.line for section in tree.sections]
    assert all(f"line {d.line}" in d.message for d in found)


def test_the_headings_that_lead_to_each_other_are_found_by_components_not_by_walking_from_each() -> None:
    # a ring of three with a tail into it and a chain out of it: only the ring is cyclic, whatever the order
    successors = {"a": ["b"], "b": ["c"], "c": ["a", "d"], "t": ["a"], "d": ["e"], "e": []}
    component, order = _components(successors)
    assert component["a"] == component["b"] == component["c"]
    assert len({component[n] for n in "abctde"}) == 4
    assert [sorted(members) for members in order if len(members) > 1] == [["a", "b", "c"]]
    # each component comes after those it leads to
    assert order.index(["e"]) < order.index(["d"]) < order.index(next(m for m in order if "a" in m))
    assert order.index(["t"]) == len(order) - 1


def test_the_components_of_a_graph_agree_with_what_each_node_can_reach() -> None:
    rng = random.Random(3)
    for _ in range(200):
        size = rng.randint(1, 9)
        names = [f"n{i}" for i in range(size)]
        graph = {n: [m for m in names if rng.random() < 0.25] for n in names}
        component, _ = _components(graph)

        def reaches(start: str, goal: str, graph: dict[str, list[str]] = graph) -> bool:
            seen, pending = set(), list(graph[start])
            while pending:
                node = pending.pop()
                if node == goal:
                    return True
                if node not in seen:
                    seen.add(node)
                    pending += graph[node]
            return False

        for a in names:
            for b in names:
                assert (component[a] == component[b]) == (a == b or (reaches(a, b) and reaches(b, a)))


def test_a_very_long_chain_of_components_does_not_hit_the_recursion_limit() -> None:
    chain = {f"n{i}": [f"n{i + 1}"] for i in range(50_000)}
    component, order = _components(chain)
    assert len(order) == 50_001 and len(set(component.values())) == 50_001
    ring = {f"n{i}": [f"n{(i + 1) % 50_000}"] for i in range(50_000)}
    _, order = _components(ring)
    assert len(order) == 1 and len(order[0]) == 50_000


def _chain(count: int, close: bool) -> str:
    """Headings that each refer to the one before (and the first to the last, with *close*): under the none scheme
    each reference is a field for the heading's text."""
    parts = [f"# H0 {{{{ref: h{count - 1}}}}} {{#h0}}\n" if close else "# H0 {#h0}\n"]
    parts += [f"# H{i} see {{{{ref: h{i - 1}}}}} {{#h{i}}}\n" for i in range(1, count)]
    return "\n".join(parts)


@pytest.mark.parametrize("close", [False, True])
def test_the_headings_of_a_long_chain_of_references_are_planned_in_linear_time(close: bool) -> None:
    def seconds(count: int) -> float:
        tree = render(FRONT + _chain(count, close), format="text", overrides=NONE).tree
        best = float("inf")
        for _ in range(2):
            started = time.perf_counter()
            plan_docx(tree)
            best = min(best, time.perf_counter() - started)
        return best

    small, large = seconds(500), seconds(4000)
    assert large < 30 * max(small, 0.01), (small, large)  # eight times the headings: a walk from each would be sixty-four
    assert large < 2.0


def test_a_ring_of_references_in_titles_is_all_text_and_a_chain_all_fields() -> None:
    _, ring, _ = planned(_chain(30, True), overrides=NONE)
    assert all(ring.units[f"s{i}"].literal_refs for i in range(1, 31))
    _, chain, _ = planned(_chain(30, False), overrides=NONE)
    assert not any(unit.literal_refs for unit in chain.units.values())


def test_the_text_a_heading_field_returns_is_made_of_the_fields_in_the_title_it_stands_for() -> None:
    # A's title refers to the item under it (written as text: the item's designation starts with A's own text); B's
    # refers to A, a field for A's text, which includes the text A's title has for that reference.
    body = "# A {{ref: i}} {#a}\n\n- x {#i}\n\n# B {{ref: a}} {#b}\n\nRefs {{ref: a}} {{ref: b}} {{ref: i}}\n"
    tree, plan, _ = planned(body, overrides=NONE)
    a, b = tree.sections
    assert plan.units[a.unit].literal_refs == ("u1",) and plan.units[b.unit].literal_refs == ()
    texts = {t: cached(plan.refs[t].segments) for t in plan.refs}
    title_of_a = next(i.text for i in iter_tree_inlines(tree) if isinstance(i, CrossRef) and i.target_unit == "u1")
    # B's heading text is "B " and what the field for A gives, which is A's text: "A " and the reference as text
    assert texts[a.unit] == "A " + title_of_a
    assert texts[b.unit] == "B " + texts[a.unit]


# -- What the bookmark returns is what the runs show ---------------------------------------------------------------


def test_a_heading_field_returns_the_text_of_the_runs_not_the_renderers_collapsed_designation() -> None:
    tree, plan, diagnostics = planned("# The {{def: foo}} clause {#c}\n\nSee {{ref: c}}.\n", overrides=NONE)
    [reference] = [i for i in iter_tree_inlines(tree) if isinstance(i, CrossRef)]
    assert reference.text == "The clause"  # the renderer collapses the white space of a designation
    [segment] = plan.refs[reference.target_unit].segments
    assert segment.cached == "The  clause" and not plan.refs[reference.target_unit].exact  # what the runs show
    [found] = [d for d in diagnostics if d.rule == "render-docx-reference"]
    assert "'The clause'" in found.message and "'The  clause'" in found.message


def test_the_text_of_the_runs_has_a_blanks_prompt_and_a_hard_break() -> None:
    from legaldown_render.resolve.docx_plan import _shown
    from legaldown_render.tree import Blank, Emphasis, HardBreak, SoftBreak, Text

    assert _shown((Text("a"), SoftBreak(), Emphasis((Text("b"),)), HardBreak(), Blank("x", "[_]", "How much"),
                   Blank("y", "[_]"))) == "a b\n[_] (How much)[_]"


# -- One finding for references that fail alike ----------------------------------------------------------------------


def test_references_that_fail_in_the_same_way_are_one_finding_naming_the_first_and_how_many_more() -> None:
    body = "# A {#a}\n\n### B {#b}\n\n### C {#c}\n\n# D {#d}\n\n### E {#e}\n\n{{ref: b}} {{ref: c}} {{ref: e}}\n"
    tree, plan, diagnostics = planned(body, style="outline")
    found = [d for d in diagnostics if d.rule == "render-docx-reference"]
    assert len(found) == 1 and found[0].message.startswith("The cross-reference 'I.A.1' cannot be built")
    assert found[0].message.endswith(" 2 more cross-references fail in the same way.")
    assert all(plan.refs[t].fixed for t in ("s2", "s3", "s5"))
    assert found[0].line == tree.sections[-1].blocks[-1].line  # the lowest line of the three: all in one paragraph


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


def with_references(source: str, shift: int = 0) -> str:
    """*source* with a paragraph at its end that refers to the anchors in it (at most 40, evenly spread, starting at
    the *shift*-th: resolving a reference costs more than numbering a unit, and documents of different shifts refer
    to different units)."""
    anchors = list(dict.fromkeys(re.findall(r"\{#([a-z][a-z0-9-]*)", source)))
    stride = -(-len(anchors) // 40) or 1
    anchors = anchors[shift % stride::stride]
    return source + "\n\nRefs: " + " ".join(f"{{{{ref: {anchor}}}}}" for anchor in anchors) + "\n"


def literal_text_of_a_cover(tree: RenderTree, segments: Sequence[Segment]) -> str:
    """The literal text of *segments* without the text of ``references.format`` around the designation. A field may
    stand for that text (a number that holds the same words: ``Article I`` for ``Article {designation}``): it is then
    in the cached text of the fields."""
    text = "".join(s.text for s in segments if isinstance(s, Literal_))
    fields = "".join(s.cached for s in segments if isinstance(s, FieldSeg))
    for part in tree.formats.reference.split("{designation}"):
        if part and part not in text:
            assert part in fields, (part, text, fields)
            continue
        text = text.replace(part, "", 1)
    return text


def assert_references_as_resolved(result, plan, diagnostics) -> None:  # noqa: ANN001
    """For every reference: it is a cover of fields and delimiters, with no counter written as text; and it reads as
    the renderer's text (``exact``, and the cached results say so), or the plan says why not: a unit on the way is
    numbered differently in Word (a diagnostic of the numbering says so, as the property of the numbering above has
    it), or a ``render-docx-reference`` finding names it. A reference that has no cover is the renderer's text, marked
    ``fixed`` and reported as such. Every field points at a bookmark the plan makes, and the names are sound."""
    tree = result.tree
    assert_names_are_sound(plan)
    nodes = {u.unit: u for u in units(tree)}
    counters = {node.unit: counter for node, counter in numbered(tree)}
    marked = {n for u in plan.units.values() for n in u.bookmarks}
    reported = [d for d in diagnostics if d.rule == "render-docx-reference"]
    texts = {i.target_unit: i.text for i in iter_tree_inlines(tree) if isinstance(i, CrossRef) and i.target_unit}
    assert set(plan.refs) == set(texts)
    # The ``ref`` templates write delimiters only: any letter or digit that is literal text in a cover is a counter
    # that should have been a field.
    templates = [f.ref for f in (*tree.formats.headings, *tree.formats.enumerated, tree.formats.renumber,
                                 tree.formats.paragraphs) if f is not None]
    delimiters_only = not any(c.isalnum() for t in templates for c in re.sub(r"\{(n|path|section)\}", "", t))
    needing_a_finding = 0
    fixed = 0
    for target, text in texts.items():
        reference = plan.refs[target]
        assert all(s.bookmark in marked for s in reference.segments if isinstance(s, FieldSeg))
        assert reference.exact == (cached(reference.segments) == text)
        for segment in reference.segments:
            if isinstance(segment, FieldSeg) and segment.number and not segment.suppress_text:
                assert segment.cached == plan.units[next(u for u, p in plan.units.items()
                                                        if segment.bookmark in p.bookmarks)].word_ref
        if reference.fixed:
            fixed += 1
            assert reference.segments == ((Literal_(text),) if text else ()) and reference.exact
            continue
        if delimiters_only:
            assert not any(c.isalnum() for c in literal_text_of_a_cover(tree, reference.segments)), (
                target, text, show(plan, reference.segments))
        assert any(isinstance(s, FieldSeg) for s in reference.segments) or not text, (target, text)
        if reference.exact:
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
        needing_a_finding += not differs
    # Every fixed text and every other difference is in a finding, named or counted among "N more".
    counted = 0
    for finding in reported:
        more = re.search(r" (\d+) more cross-references fail in the same way\.$", finding.message)
        counted += 1 + (int(more.group(1)) if more else 0)
    assert needing_a_finding + fixed <= counted <= len(texts)
    if fixed:
        assert any("not a field" in d.message for d in reported)


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("name", ["features.lgd", "template.lgd"])
def test_the_test_documents_references_are_planned_as_resolved(name: str, variant: str) -> None:
    source = (DOCUMENTS / name).read_text(encoding="utf-8")
    assert_references_as_resolved(*planned_run(source, variant))


@pytest.mark.conformance
@pytest.mark.skipif(SPEC is None, reason="LegalDown specification checkout not found")
@pytest.mark.parametrize("variant", VARIANTS)
def test_the_specifications_documents_references_are_planned_as_resolved(variant: str) -> None:
    for path in _spec_documents():
        try:
            run = planned_run(path.read_text(encoding="utf-8"), variant)
        except DocumentError:
            continue
        try:
            assert_references_as_resolved(*run)
        except AssertionError as error:
            raise AssertionError(f"{path.relative_to(SPEC)}: {error}") from error


@pytest.mark.parametrize("variant", VARIANTS)
def test_random_documents_references_are_planned_as_resolved(variant: str) -> None:
    for source, result, plan, diagnostics in random_runs(variant):
        try:
            assert_references_as_resolved(result, plan, diagnostics)
        except AssertionError as error:
            raise AssertionError(f"{error}\n--- document ---\n{source}") from error


@pytest.mark.parametrize("variant", VARIANTS)
def test_random_alternatives_references_are_planned_as_resolved(variant: str) -> None:
    for source, result, plan, diagnostics in alternative_runs(variant):
        try:
            assert_references_as_resolved(result, plan, diagnostics)
        except AssertionError as error:
            raise AssertionError(f"{error}\n--- document ---\n{source}") from error


def test_the_random_documents_reach_every_kind_of_cover() -> None:
    shapes: set[str] = set()
    inexact = switches = 0
    for variant in VARIANTS:
        for _, _, plan, _ in random_runs(variant):
            for reference in plan.refs.values():
                shapes.add("".join("L" if isinstance(s, Literal_) else "F" for s in reference.segments))
                inexact += not reference.exact
                switches += any(isinstance(s, FieldSeg) and s.suppress_text for s in reference.segments)
    assert {"F", "FF", "FFF", "FFFF", "FLF", "FLFL", "FLFLF"} <= shapes
    assert inexact > 0 and switches > 0
