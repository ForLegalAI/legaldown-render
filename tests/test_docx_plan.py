"""The DOCX numbering plan (docs/architecture.md, "The DOCX planner"): which abstractNums and nums a document needs,
which (numId, ilvl) each section, list item and numbered paragraph takes, and that Word, simulated, numbers every unit
as the resolver did, or a diagnostic says why not. Everything is checked on the plan's data; no file is written."""
from __future__ import annotations

import random
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import DOCUMENTS, FRONT, spec_root

from legaldown_render import DocumentError, render
from legaldown_render.errors import StyleError
from legaldown_render.resolve.docx_numbering import NUM_FMT
from legaldown_render.resolve.docx_plan import (
    DocxPlan,
    _formats_of,
    docx_style_errors,
    plan_docx,
)
from legaldown_render.style import load_style
from legaldown_render.tree import DraftingNote, List, ListItem, Paragraph, Quote, RenderTree, Section

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
}


def planned(body: str, front: str = FRONT, **settings: object):  # noqa: ANN201
    result = render(front + body, format="text", **settings)
    plan, diagnostics = plan_docx(result.tree, result.style)
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


def test_a_counter_without_a_word_format_is_written_as_decimal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(NUM_FMT, "lower-alpha")
    tree, plan, diagnostics = planned("# A\n\n- x\n- y\n")
    assert plan.abstract_nums[1].levels[0].num_fmt == "decimal"
    assert [plan.units[i.unit].label for i in items(tree)] == ["(1)", "(2)"]
    assert [(d.rule, d.level) for d in diagnostics if d.rule == "render-docx-counter"] == [
        ("render-docx-counter", "warning")]


# -- Headings ------------------------------------------------------------------------


def test_headings_take_the_style_num_at_their_level() -> None:
    tree, plan, _ = planned("# A\n\n## B\n\n### C\n\n# D\n")
    assert [(u.num_id, u.ilvl, u.explicit, u.label, u.word_ref, u.count) for u in map(plan.units.get, ("s1", "s2", "s3", "s4"))] == [
        (1, 0, False, "1.", "1", 1), (1, 1, False, "1.1", "1.1", 1), (1, 2, False, "1.1.1", "1.1.1", 1),
        (1, 0, False, "2.", "2", 2)]


def test_a_skipped_heading_level_is_numbered_as_the_resolver_does_and_reported() -> None:
    tree, plan, diagnostics = planned("# A\n\n### B\n\n## C\n")
    assert [plan.units[s].label for s in ("s1", "s2", "s3")] == ["1.", "1.1.1", "1.2"]
    assert plan.units["s2"].cause == ("skipped-level",) and plan.units["s3"].cause == ()
    [found] = [d for d in diagnostics if d.rule == "render-docx-heading-skip"]
    assert found.level == "warning" and found.line == tree.sections[1].line and "'B'" in found.message
    assert rules(diagnostics) == {"render-docx-heading-skip"}


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
    assert plan.nums[1].start_overrides == tuple((i, 1) for i in range(9))
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
    # starts at its number, 3. Static, and reported as information only.
    last = plan.units[ordered[-1].unit]
    assert last.num_id != plan.units[ordered[0].unit].num_id and last.cause == ("restart-family",)
    assert plan.nums[last.num_id - 1].start_overrides[0] == (1, 3)
    assert {d.level for d in diagnostics} == {"info"}


def test_a_nested_list_of_another_kind_has_a_num_of_its_own_and_is_reported_as_information() -> None:
    tree, plan, diagnostics = planned("# A\n\n- a\n  1. x\n  1. y\n- b\n  1. z\n")
    _, x, y, _, z = items(tree)
    assert shown(plan, x, y, z) == [(3, 1, "1."), (3, 1, "2."), (4, 1, "1.")]
    assert plan.units[x.unit].cause == ("restart-family",)
    assert plan.nums[2].start_overrides[0] == (1, 1) and plan.nums[2].abstract_id == 2
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
    assert plan.nums[3].start_overrides[0] == (1, 2) and plan.units[y.unit].cause == ("resync",)
    assert [(d.rule, d.level) for d in diagnostics] == [("render-docx-static-restart", "info")]


def test_bullets_are_numbered_paragraphs_with_a_bullet_of_their_own() -> None:
    tree, plan, diagnostics = planned("# A\n\n- a\n  - b\n", overrides={"enumeration.enabled": False})
    assert [a.kind for a in plan.abstract_nums] == ["headings", "bullet"]
    first, second = items(tree)
    assert shown(plan, first, second) == [(2, 0, ""), (2, 1, "")] and plan.units[first.unit].count is None
    assert [level.lvl_text for level in plan.abstract_nums[1].levels[:4]] == ["•", "–", "◦", "•"]
    assert diagnostics == []


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
    assert [(d.rule, d.line) for d in diagnostics] == [
        ("render-docx-number-mismatch", tree.sections[1].line), ("render-docx-number-mismatch", tree.sections[2].line)]
    assert "'1.'" in diagnostics[0].message and "'2.'" in diagnostics[0].message


# -- Diagnostics: lines, files, order, repeats --------------------------------------------


def test_diagnostics_name_the_file_and_the_line_and_come_in_line_order() -> None:
    result = render(FRONT + "# A\n\n### B\n\n- x\n  1. y\n", format="text")
    plan, diagnostics = plan_docx(result.tree, result.style, file="deal.lgd")
    assert [(d.rule, d.file) for d in diagnostics] == [
        ("render-docx-heading-skip", "deal.lgd"), ("render-docx-static-restart", "deal.lgd")]
    lines = [d.line for d in diagnostics]
    assert lines == sorted(lines) and None not in lines
    _, anonymous = plan_docx(result.tree, result.style)
    assert {d.file for d in anonymous} == {""}


def test_the_same_finding_is_reported_once() -> None:
    _, _, diagnostics = planned("# A\n\n- a\n  1. x\n- b\n  1. y\n- c\n  1. z\n")
    assert [d.rule for d in diagnostics] == ["render-docx-static-restart"]


def test_untrusted_text_in_a_message_is_escaped() -> None:
    _, _, diagnostics = planned("# A\n\n### B\u0007x\n")
    [found] = diagnostics
    assert "\u0007" not in found.message


# -- Determinism and the plan's data -------------------------------------------------------


def test_the_same_tree_gives_an_equal_plan_and_the_plan_is_frozen() -> None:
    result = render(FRONT + NESTED, format="text", style="continental")
    first = plan_docx(result.tree, result.style)
    again = plan_docx(result.tree, result.style)
    assert first[0] == again[0] and first[1] == again[1]
    with pytest.raises(AttributeError):
        first[0].heading_num_id = 5  # type: ignore[misc]
    assert list(first[0].units) == [u.unit for u in units(result.tree)]
    assert first[0].refs == {} and first[0].anchors == {} and first[0].toc == ()


def test_unit_plans_carry_the_whole_plan_for_a_unit() -> None:
    _, plan, _ = planned("# A\n\n- x\n")
    unit = plan.units["u1"]
    assert (unit.kind, unit.ind, unit.alt, unit.bookmarks, unit.toc_bookmark) == ("item", None, None, (), None)
    assert plan.units["s1"].kind == "section"


def test_a_tree_and_a_style_that_do_not_belong_together_are_refused() -> None:
    from legaldown_render.errors import InternalError
    result = render(FRONT + "# A\n", format="text")
    with pytest.raises(InternalError):
        plan_docx(result.tree, load_style("continental"))


# -- Style checks (ADR 0006) ----------------------------------------------------------------


def test_formats_of_a_style_are_the_formats_in_the_tree() -> None:
    for settings in VARIANTS.values():
        result = render(FRONT + "# A\n", format="text", **settings)
        assert _formats_of(result.style) == result.tree.formats


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
    with pytest.raises(StyleError):
        plan_docx(render(FRONT + "# A\n", format="text", overrides=overrides).tree, style)


def test_a_percent_that_cannot_be_mistaken_is_fine() -> None:
    levels = [{"counter": "decimal", "label": "{n}% of", "ref": "{n}"}]
    assert docx_style_errors(load_style(overrides={"numbering.levels": levels})) == []
    tree, plan, _ = planned("# A\n", overrides={"numbering.levels": levels})
    assert plan.abstract_nums[0].levels[0].lvl_text == "%1% of"


def test_settings_that_are_off_are_not_checked() -> None:
    overrides = {"enumeration.enabled": False, "enumeration.levels": [
        {"counter": "decimal", "label": "%1{n}", "ref": "{n}"}]}
    assert docx_style_errors(load_style(overrides=overrides)) == []
    heads = {"numbering.scheme": "none"}
    assert docx_style_errors(load_style(overrides=heads)) == []


def test_a_level_text_longer_than_word_allows_is_a_style_error() -> None:
    long = "x" * 260
    style = load_style(overrides={"numbering.levels": [{"counter": "decimal", "label": long + "{n}", "ref": "{n}"}]})
    [problem] = docx_style_errors(style)
    assert "headings" in problem and "255" in problem


# -- Word numbers every unit as the resolver did, or a diagnostic says why not ---------------------

EXPLAINING = {
    "label-fallback": "render-docx-section-label",
    "clamped": "render-docx-list-depth",
    "skipped-level": "render-docx-heading-skip",
}
MISMATCH = {"render-docx-number-mismatch", "render-docx-letter-overflow", "render-docx-roman-overflow"}


def assert_planned_as_resolved(result) -> None:  # noqa: ANN001
    """The property: for every numbered unit, Word's label and count are the resolver's, or a diagnostic of the plan
    explains the exception; and nothing is reported that did not happen."""
    plan, diagnostics = plan_docx(result.tree, result.style)
    found = rules(diagnostics)
    differs = False
    for unit in units(result.tree):
        mine = plan.units[unit.unit]
        if unit.number is None:
            assert mine.count is None and mine.label == "" or isinstance(unit, Section)
            continue
        for cause, rule in EXPLAINING.items():
            if cause in mine.cause:
                assert rule in found, (unit.unit, cause)
        if mine.count != unit.number or (mine.label != unit.label and not {"label-fallback", "clamped"} & set(mine.cause)):
            differs = True
        if mine.cause == () and mine.count == unit.number:
            assert mine.label == unit.label or found & MISMATCH, (unit.unit, mine.label, unit.label)
    assert (not differs) or found & MISMATCH, "a difference without a diagnostic"
    for rule in found & MISMATCH:
        assert differs, f"{rule} without a difference"
    assert all(d.rule.startswith("render-docx-") for d in diagnostics)
    assert {u.unit for u in units(result.tree)} == set(plan.units)
    uses = {n.id for n in plan.nums}
    for mine in plan.units.values():
        assert mine.num_id is None or mine.num_id in uses
    # Every num with overrides is used, and the first unit on it is on a level its overrides name.
    first: dict[int, str] = {}
    for unit in units(result.tree):
        mine = plan.units[unit.unit]
        if mine.num_id is not None:
            first.setdefault(mine.num_id, unit.unit)
    for num in plan.nums:
        if num.start_overrides:
            assert num.id in first, f"num {num.id} is never used"
            assert plan.units[first[num.id]].ilvl in {i for i, _ in num.start_overrides}


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


def _random_list(rng: random.Random, depth: int, indent: str) -> list[str]:
    ordered = rng.random() < 0.3
    marker = "1. " if ordered else "- "
    lines: list[str] = []
    for index in range(rng.choice([1, 2, 2, 3, 5, 30]) if depth == 0 else rng.randint(1, 3)):
        lines.append(f"{indent}{marker}item {depth}.{index}")
        inner = indent + " " * len(marker)
        if depth < 4 and rng.random() < 0.45:
            lines.extend(_random_list(rng, depth + 1, inner))
            if rng.random() < 0.3:  # a sibling list after text: it goes on from the first
                lines += ["", f"{inner}Between.", ""]
                lines.extend(_random_list(rng, depth + 1, inner))
        if depth < 3 and rng.random() < 0.12:
            lines += ["", f"{inner}> - quoted", f"{inner}> - quoted too", ""]
    return lines + [""]


def _random_document(rng: random.Random) -> str:
    out: list[str] = []
    level = 1
    for _ in range(rng.randint(1, 7)):
        move = rng.random()
        level = max(1, min(5, level + (1 if move < 0.4 else -1 if move < 0.55 else 2 if move < 0.6 else 0)))
        out.append("#" * level + " Heading\n")
        for _ in range(rng.randint(0, 3)):
            kind = rng.random()
            if kind < 0.45:
                out.extend(_random_list(rng, 0, ""))
            elif kind < 0.8:
                out.append("A paragraph.\n")
            else:
                out.append("> quoted\n")
    if rng.random() < 0.3:
        out.insert(0, "- preamble item\n  - nested\n")
    return "\n".join(out)


@pytest.mark.parametrize("variant", VARIANTS)
def test_random_documents_are_planned_as_resolved(variant: str) -> None:
    rng = random.Random(20261028)
    for _ in range(40):
        source = FRONT + _random_document(rng)
        try:
            assert_planned_as_resolved(render(source, format="text", **VARIANTS[variant]))
        except AssertionError as error:
            raise AssertionError(f"{error}\n--- document ---\n{source}") from error


def test_the_random_documents_reach_every_case_the_property_is_about() -> None:
    seen_rules: set[str] = set()
    seen_causes: set[str] = set()
    for settings in VARIANTS.values():
        rng = random.Random(20261028)
        for _ in range(40):
            result = render(FRONT + _random_document(rng), format="text", **settings)
            plan, diagnostics = plan_docx(result.tree, result.style)
            seen_rules |= rules(diagnostics)
            seen_causes |= {cause for unit in plan.units.values() for cause in unit.cause}
    assert {"render-docx-letter-overflow", "render-docx-section-label", "render-docx-heading-skip",
            "render-docx-static-restart"} <= seen_rules
    assert {"restart-family", "resync", "label-fallback", "skipped-level"} <= seen_causes
