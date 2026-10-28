"""The numbering structure the render tree carries (§13.1–§13.3, §15.4, §15.8): the formats in
effect, and for every section, list item and top-level paragraph its unit id, counter value,
parent and the alternative it shares its number with, so that a writer can build numbering
of its own without working out a rule of §13 again. Every value is checked against what the
resolver wrote as labels and designations: they must be derivable from the exposed
structure alone, under each built-in style, over the specification's corpus too."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import DOCUMENTS, FRONT, spec_root

from legaldown_render import DocumentError, render
from legaldown_render.resolve.numbering import extend, fill, format_counter
from legaldown_render.tree import (
    CrossRef,
    DraftingNote,
    FailureMarker,
    List,
    ListItem,
    Paragraph,
    Quote,
    RenderTree,
    Section,
    Value,
    iter_tree_inlines,
)

NONE = {"numbering.scheme": "none"}
ALTERNATIVES = FRONT.replace("title: T", (
    "title: T\nquestions:\n  forum:\n    type: choice\n    choices:\n      courts: Courts\n      arb: Arbitration"))
ATTACHED = FRONT.replace("title: T", "title: T\nattachments:\n  - id: att\n    title: Schedule\n    file: att.lgd")


def tree_of(body: str, front: str = FRONT, **settings: object) -> RenderTree:
    return render(front + body, format="text", **settings).tree


# -- A walk of the units, in document order ---------------------------------------


@dataclass(frozen=True)
class Found:
    node: Section | ListItem | Paragraph
    #: The section it is in; None in the preamble (and for sections themselves).
    section: Section | None
    #: The list an item is in.
    owner: List | None = None

    @property
    def kind(self) -> str:
        return {Section: "section", ListItem: "item", Paragraph: "paragraph"}[type(self.node)]


def _blocks(blocks: tuple, section: Section | None) -> Iterator[Found]:
    for block in blocks:
        match block:
            case Paragraph() if block.unit is not None:
                yield Found(block, section)
            case List():
                for item in block.items:
                    yield Found(item, section, block)
                    yield from _blocks(item.blocks, section)
            case Quote(blocks=inner) | DraftingNote(blocks=inner):
                yield from _blocks(inner, section)


def walk(tree: RenderTree) -> list[Found]:
    """Every unit: the preamble's, then each section and what it holds."""
    found = list(_blocks(tree.preamble, None))
    for section in tree.sections:
        found.append(Found(section, None))
        found.extend(_blocks(section.blocks, section))
    return found


def units(tree: RenderTree) -> dict[str, Found]:
    return {found.node.unit: found for found in walk(tree)}


def sections(tree: RenderTree) -> list[Section]:
    return list(tree.sections)


def items(tree: RenderTree) -> list[ListItem]:
    return [found.node for found in walk(tree) if isinstance(found.node, ListItem)]


def paragraphs(tree: RenderTree) -> list[Paragraph]:
    return [found.node for found in walk(tree) if isinstance(found.node, Paragraph)]


def lists(tree: RenderTree) -> list[List]:
    seen: list[List] = []
    for found in walk(tree):
        if found.owner is not None and found.owner not in seen:
            seen.append(found.owner)
    return seen


def refs(tree: RenderTree) -> list[CrossRef]:
    return [inline for inline in iter_tree_inlines(tree) if isinstance(inline, CrossRef)]


def structure(tree: RenderTree) -> list[tuple]:
    """What the tree says of each unit, for comparing two trees."""
    return [(f.kind, f.node.unit, f.node.number, f.node.parent_unit, f.node.shares_number_with) for f in walk(tree)]


# -- The formats in effect --------------------------------------------------------


def test_the_default_formats() -> None:
    formats = tree_of("# A\n").formats
    assert formats.scheme == "decimal"
    assert [(f.counter, f.label, f.ref) for f in formats.headings] == (
        [("decimal", "{path}.", "{n}")] + [("decimal", "{path}", ".{n}")] * 4)
    assert [(f.counter, f.label, f.ref) for f in formats.enumerated] == [
        ("lower-alpha", "({n})", "({n})"), ("lower-roman", "({n})", "({n})"), ("upper-alpha", "({n})", "({n})")]
    assert (formats.renumber.counter, formats.renumber.label, formats.renumber.ref) == ("decimal", "{n}.", "({n})")
    assert formats.paragraphs is None
    assert formats.reference == "{designation}"


def test_formats_follow_the_style_after_it_is_resolved() -> None:
    outline = tree_of("# A\n", style="outline").formats
    assert outline.scheme == "legal-outline"
    assert [f.counter for f in outline.headings] == [
        "upper-roman", "upper-alpha", "decimal", "lower-alpha", "lower-roman"]
    continental = tree_of("# A\n", style="continental").formats
    # The style names the first heading format only; the rest are the scheme's.
    assert [(f.label, f.ref) for f in continental.headings] == [("{n}.", "{n}")] + [("{path}", ".{n}")] * 4
    assert continental.enumerated[0].label == "{section}.{n}"
    assert continental.paragraphs is not None
    assert (continental.paragraphs.label, continental.paragraphs.ref) == ("({n})", "({n})")
    mixed = tree_of("# A\n", overrides={"numbering.scheme": "mixed"}).formats
    assert mixed.scheme == "mixed"
    assert [f.label for f in mixed.headings] == ["{n}.", "({n})", "({n})", "({n})", "({n})"]


def test_a_style_can_replace_heading_levels_and_references() -> None:
    formats = tree_of("# A\n", overrides={
        "numbering.levels": [{"counter": "upper-alpha", "label": "Article {n}", "ref": "{n}"}],
        "references.format": "Section {designation}"}).formats
    assert formats.headings[0].label == "Article {n}"
    assert formats.headings[1].ref == ".{n}"
    assert formats.reference == "Section {designation}"


def test_the_none_scheme_numbers_no_headings_but_still_formats_the_rest() -> None:
    formats = tree_of("# A\n", overrides=NONE).formats
    assert formats.scheme == "none"
    assert formats.headings == ()
    assert formats.enumerated and formats.renumber is not None


def test_ordered_lists_may_be_enumerated_instead_of_renumbered() -> None:
    formats = tree_of("# A\n", overrides={"enumeration.ordered": "enumerate"}).formats
    assert formats.renumber is None
    assert formats.enumerated


def test_formats_of_a_list_family_that_is_off_are_left_out() -> None:
    formats = tree_of("# A\n", overrides={"enumeration.enabled": False}).formats
    assert formats.enumerated == ()
    assert formats.renumber is not None
    both = tree_of("# A\n", overrides={"enumeration.enabled": False, "enumeration.ordered": "enumerate"}).formats
    assert both.enumerated  # ordered lists still use the levels


# -- Headings ----------------------------------------------------------------------


def test_sections_have_ids_in_document_order_and_their_own_counter() -> None:
    tree = tree_of("# A\n\n## B\n\n## C\n\n### D\n\n# E\n\n## F\n")
    assert [(s.unit, s.level, s.number, s.parent_unit, s.designation) for s in tree.sections] == [
        ("s1", 1, 1, None, "1"), ("s2", 2, 1, "s1", "1.1"), ("s3", 2, 2, "s1", "1.2"),
        ("s4", 3, 1, "s3", "1.2.1"), ("s5", 1, 2, None, "2"), ("s6", 2, 1, "s5", "2.1")]
    assert all(s.shares_number_with is None for s in tree.sections)


def test_outline_sections_count_in_their_own_styles() -> None:
    tree = tree_of("# A\n\n## B\n\n### C\n\n#### D\n\n##### E\n\n## F\n", style="outline")
    assert [(s.designation, s.number) for s in tree.sections] == [
        ("I", 1), ("I.A", 1), ("I.A.1", 1), ("I.A.1.a", 1), ("I.A.1.a(i)", 1), ("I.B", 2)]
    assert [s.parent_unit for s in tree.sections] == [None, "s1", "s2", "s3", "s4", "s1"]


def test_continental_headings_are_decimal_with_a_dotted_label() -> None:
    tree = tree_of("# A\n\n## B\n", style="continental")
    assert [(s.label, s.number) for s in tree.sections] == [("1.", 1), ("1.1", 1)]


def test_a_skipped_level_counts_as_one_and_is_no_section() -> None:
    # heading-skip is an Error, but the document still renders, numbered as the validator numbers it.
    tree = tree_of("# A\n\n### B\n\n## C\n\n#### D\n\n### E\n")
    assert [(s.unit, s.level, s.number, s.parent_unit, s.designation) for s in tree.sections] == [
        ("s1", 1, 1, None, "1"),
        ("s2", 3, 1, "s1", "1.1.1"),  # level 2 skipped: it is 1, and has no unit
        ("s3", 2, 2, "s1", "1.2"),
        ("s4", 4, 1, "s3", "1.2.1.1"),
        ("s5", 3, 2, "s3", "1.2.2")]


def test_a_document_that_starts_below_level_one_is_numbered_from_the_top() -> None:
    # As the validator numbers it: the first heading is the first section, whatever its marks.
    tree = tree_of("## A\n\n## B\n\n### C\n")
    assert [(s.level, s.number, s.parent_unit, s.designation) for s in tree.sections] == [
        (1, 1, None, "1"), (1, 2, None, "2"), (2, 1, "s2", "2.1")]


def test_sections_are_unnumbered_under_the_none_scheme_but_keep_their_structure() -> None:
    tree = tree_of("# A\n\n## B\n\n# C\n", overrides=NONE)
    assert [(s.unit, s.number, s.parent_unit, s.label, s.designation) for s in tree.sections] == [
        ("s1", None, None, None, "A"), ("s2", None, "s1", None, "B"), ("s3", None, None, None, "C")]


def test_alternative_sections_share_a_number_and_name_the_first() -> None:
    body = ("# Disputes {#d when=forum:courts}\n\n## Sub one\n\n## Sub two\n\n"
            "# Disputes {#d when=forum:arb}\n\n## Sub alt {#sub-alt}\n\n# Last\n")
    tree = tree_of(body, ALTERNATIVES)
    assert [(s.unit, s.designation, s.number, s.parent_unit, s.shares_number_with) for s in tree.sections] == [
        ("s1", "1", 1, None, None),
        ("s2", "1.1", 1, "s1", None),
        ("s3", "1.2", 2, "s1", None),
        ("s4", "1", 1, None, "s1"),
        ("s5", "1.1", 1, "s4", "s2"),  # the number is shared, not the parent: it is the later alternative's child
        ("s6", "2", 2, None, None)]


def test_alternative_sections_under_the_none_scheme_share_nothing() -> None:
    body = "# Disputes {#d when=forum:courts}\n\n# Disputes {#d when=forum:arb}\n"
    tree = tree_of(body, ALTERNATIVES, overrides=NONE)
    assert [s.shares_number_with for s in tree.sections] == [None, None]


def test_unit_ids_do_not_depend_on_the_anchor() -> None:
    body = "# A {#custom}\n\n# B\n\n# C {#custom-two}\n"
    tree = tree_of(body)
    assert [s.unit for s in tree.sections] == ["s1", "s2", "s3"]
    assert [s.anchor for s in tree.sections] == ["custom", "b", "custom-two"]


# -- List items --------------------------------------------------------------------

NESTED = """# A

- one {#one}
- two
  - nested a {#na}
  - nested b
    - deep
- three

1. first
1. second {#second}
"""


def test_nested_enumerated_items_carry_number_parent_and_family() -> None:
    tree = tree_of(NESTED)
    assert [(i.unit, i.label, i.number, i.parent_unit) for i in items(tree)] == [
        ("u1", "(a)", 1, "s1"),
        ("u2", "(b)", 2, "s1"),
        ("u3", "(i)", 1, "u2"),
        ("u4", "(ii)", 2, "u2"),
        ("u5", "(A)", 1, "u4"),
        ("u6", "(c)", 3, "s1"),
        ("u7", "1.", 1, "s1"),
        ("u8", "2.", 2, "s1")]
    formats = tree.formats
    assert [(f.family, f.depth, f.enumerated) for f in lists(tree)] == [
        ("enumerated", 0, True), ("enumerated", 1, True), ("enumerated", 2, True), ("renumber", 0, True)]
    assert [f.format for f in lists(tree)] == [*formats.enumerated, formats.renumber]


def test_a_list_nested_deeper_than_the_formats_goes_back_to_the_first() -> None:
    body = "# A\n\n- 1\n  - 2\n    - 3\n      - 4\n        - 5\n"
    tree = tree_of(body)
    assert [(f.depth, f.format) for f in lists(tree)] == [
        (0, tree.formats.enumerated[0]), (1, tree.formats.enumerated[1]), (2, tree.formats.enumerated[2]),
        (3, tree.formats.enumerated[0]), (4, tree.formats.enumerated[1])]
    assert [i.label for i in items(tree)] == ["(a)", "(i)", "(A)", "(a)", "(i)"]


def test_an_ordered_list_nested_in_an_unordered_one_is_renumbered_from_its_parent() -> None:
    tree = tree_of("# A\n\n- one\n  1. x\n  1. y\n")
    assert [(f.family, f.depth) for f in lists(tree)] == [("enumerated", 0), ("renumber", 1)]
    assert [(i.label, i.number, i.parent_unit) for i in items(tree)] == [
        ("(a)", 1, "s1"), ("1.", 1, "u1"), ("2.", 2, "u1")]


def test_ordered_lists_enumerated_by_depth_when_the_style_says_so() -> None:
    tree = tree_of("# A\n\n1. x\n   1. y\n", overrides={"enumeration.ordered": "enumerate"})
    assert [(f.family, f.format) for f in lists(tree)] == [
        ("enumerated", tree.formats.enumerated[0]), ("enumerated", tree.formats.enumerated[1])]
    assert [i.label for i in items(tree)] == ["(a)", "(i)"]


def test_bullets_are_units_without_numbers() -> None:
    tree = tree_of("# A\n\n- x {#x}\n- y\n  - z\n\n1. n\n", overrides={"enumeration.enabled": False})
    assert [(f.family, f.format, f.enumerated) for f in lists(tree)] == [
        ("bullet", None, False), ("bullet", None, False), ("renumber", tree.formats.renumber, True)]
    assert [(i.unit, i.number, i.parent_unit, i.shares_number_with, i.label) for i in items(tree)] == [
        ("u1", None, "s1", None, None), ("u2", None, "s1", None, None), ("u3", None, "u2", None, None),
        ("u4", 1, "s1", None, "1.")]


def test_a_list_without_enumeration_levels_is_bullets() -> None:
    tree = tree_of("# A\n\n- x\n", overrides={"enumeration.levels": []})
    assert [(f.family, f.format) for f in lists(tree)] == [("bullet", None)]
    assert items(tree)[0].number is None


def test_section_qualified_items_are_built_on_their_section() -> None:
    tree = tree_of("# A\n\n- x\n  - y\n- z\n\n# B\n\n- w\n", style="continental")
    assert [(i.label, i.number, i.parent_unit) for i in items(tree)] == [
        ("1.1", 1, "s1"), ("a)", 1, "u1"), ("1.2", 2, "s1"), ("2.1", 1, "s2")]
    top = lists(tree)[0]
    assert top.format is not None
    assert "{section}" in top.format.label and top.format.ref == ".{n}"
    assert "{section}" not in tree.formats.enumerated[1].label


def test_items_of_a_list_in_a_quote_are_built_on_the_section() -> None:
    tree = tree_of("# A\n\n> - q1\n> - q2\n>   - q3\n\n- real\n")
    assert [(i.unit, i.label, i.parent_unit) for i in items(tree)] == [
        ("u1", "(a)", "s1"), ("u2", "(b)", "s1"), ("u3", "(i)", "u2"), ("u4", "(a)", "s1")]


def test_the_preamble_has_items_without_a_parent() -> None:
    tree = tree_of("- p1\n- p2\n  - p3\n\n# A\n\n- x\n")
    assert [(i.unit, i.label, i.parent_unit) for i in items(tree)] == [
        ("u1", "(a)", None), ("u2", "(b)", None), ("u3", "(i)", "u2"), ("u4", "(a)", "s1")]


def test_alternative_items_share_a_number() -> None:
    body = ("# A\n\n- first\n- by courts {#i when=forum:courts}\n  - inner\n"
            "- by arbitration {#i when=forum:arb}\n  - inner alt\n- last\n")
    tree = tree_of(body, ALTERNATIVES)
    assert [(i.unit, i.number, i.parent_unit, i.shares_number_with) for i in items(tree)] == [
        ("u1", 1, "s1", None),
        ("u2", 2, "s1", None),
        ("u3", 1, "u2", None),
        ("u4", 2, "s1", "u2"),
        ("u5", 1, "u4", None),  # the alternative's own children are its own: they count from 1
        ("u6", 3, "s1", None)]


def test_three_alternatives_all_name_the_first() -> None:
    front = FRONT.replace("title: T", "title: T\nquestions:\n  q:\n    type: choice\n    choices:\n"
                          "      a: A\n      b: B\n      c: C")
    tree = tree_of("# A\n\n- x\n- one {#i when=q:a}\n- two {#i when=q:b}\n- three {#i when=q:c}\n- y\n", front)
    assert [(i.number, i.shares_number_with) for i in items(tree)] == [
        (1, None), (2, None), (2, "u2"), (2, "u2"), (3, None)]


def test_units_that_can_appear_together_do_not_share_a_number() -> None:
    body = "# A\n\n- one {#i when=forum:courts}\n- two {#i when=forum:courts}\n"
    assert [(i.number, i.shares_number_with) for i in items(tree_of(body, ALTERNATIVES))] == [(1, None), (2, None)]


def test_only_adjacent_alternatives_share_a_number_and_a_list_never_shares_with_the_one_before() -> None:
    body = "# A\n\n- a {#i when=forum:courts}\n\ntext\n\n- b {#i when=forum:arb}\n"
    assert [(i.number, i.shares_number_with) for i in items(tree_of(body, ALTERNATIVES))] == [(1, None), (1, None)]


# -- Paragraphs --------------------------------------------------------------------


def test_numbered_paragraphs_carry_number_and_section() -> None:
    body = "Preamble.\n\n# A\n\nOne.\n\nTwo. {#two}\n\n- item\n\n  Continued.\n\n# B\n\nThree.\n"
    tree = tree_of(body, overrides={"paragraphs.numbered": True})
    assert [(p.unit, p.label, p.number, p.parent_unit) for p in paragraphs(tree)] == [
        ("u1", "1.1", 1, "s1"), ("u2", "1.2", 2, "s1"), ("u4", "2.1", 1, "s2")]
    # The preamble's paragraph and the item's continuation are not units.
    assert tree.preamble[0].unit is None and tree.preamble[0].number is None
    inner = items(tree)[0].blocks[1]
    assert isinstance(inner, Paragraph)
    assert (inner.unit, inner.number, inner.parent_unit) == (None, None, None)
    assert tree.formats.paragraphs is not None
    assert tree.formats.paragraphs.label == "{section}.{n}"


def test_paragraphs_are_units_without_numbers_when_the_style_does_not_number_them() -> None:
    tree = tree_of("# A\n\nOne. {#one}\n\nTwo.\n")
    assert [(p.unit, p.number, p.parent_unit, p.label) for p in paragraphs(tree)] == [
        ("u1", None, "s1", None), ("u2", None, "s1", None)]
    assert tree.formats.paragraphs is None


def test_alternative_paragraphs_share_a_number() -> None:
    body = "# A\n\nIntro.\n\nCourts. {#p when=forum:courts}\n\nArbitration. {#p when=forum:arb}\n\nAfter.\n"
    tree = tree_of(body, ALTERNATIVES, overrides={"paragraphs.numbered": True})
    assert [(p.unit, p.label, p.number, p.shares_number_with) for p in paragraphs(tree)] == [
        ("u1", "1.1", 1, None), ("u2", "1.2", 2, None), ("u3", "1.2", 2, "u2"), ("u4", "1.3", 3, None)]


def test_alternative_paragraphs_share_nothing_where_none_are_numbered() -> None:
    body = "# A\n\nCourts. {#p when=forum:courts}\n\nArbitration. {#p when=forum:arb}\n"
    assert [p.shares_number_with for p in paragraphs(tree_of(body, ALTERNATIVES))] == [None, None]


def test_paragraph_numbers_under_the_none_scheme() -> None:
    tree = tree_of("# Heading\n\nOne.\n\nTwo.\n", overrides={**NONE, "paragraphs.numbered": True})
    assert [(p.label, p.number, p.parent_unit) for p in paragraphs(tree)] == [("Heading.1", 1, "s1"), ("Heading.2", 2, "s1")]


def test_paragraph_numbering_style_continental() -> None:
    tree = tree_of("# A\n\nOne.\n\nTwo.\n", style="continental")
    assert [(p.label, p.number) for p in paragraphs(tree)] == [("(1)", 1), ("(2)", 2)]


# -- Cross-references --------------------------------------------------------------

REFERENCES = """# Services {#svc}

Intro. {#intro}

- alpha {#alpha}
  - beta {#beta}

# Other {#other}

See {{ref: svc}}, {{ref: alpha}}, {{ref: beta}}, {{ref: intro}}, {{ref: other}}.
"""


def test_a_reference_names_its_target_and_its_section() -> None:
    tree = tree_of(REFERENCES, overrides={"paragraphs.numbered": True})
    assert [(r.text, r.target, r.target_unit, r.section_unit) for r in refs(tree)] == [
        ("1", "svc", "s1", "s1"),
        ("1(a)", "alpha", "u2", "s1"),
        ("1(a)(i)", "beta", "u3", "s1"),
        ("1.1", "intro", "u1", "s1"),
        ("2", "other", "s2", "s2")]
    found = units(tree)
    for ref in refs(tree):
        assert found[ref.target_unit].node.anchor == ref.target


def test_a_reference_to_a_unit_the_style_does_not_number_names_the_unit_and_reads_as_its_section() -> None:
    result = render(FRONT + REFERENCES, format="text")
    assert "ref-not-enumerated" in {d.rule for d in result.diagnostics}
    found = units(result.tree)
    by_target = {r.target: r for r in refs(result.tree)}
    intro = by_target["intro"]
    assert (intro.text, intro.target_unit, intro.section_unit) == ("1", "u1", "s1")
    assert found["u1"].node.number is None
    bullets = render(FRONT + REFERENCES, format="text", overrides={"enumeration.enabled": False})
    beta = {r.target: r for r in refs(bullets.tree)}["beta"]
    assert (beta.text, beta.target_unit, beta.section_unit) == ("1", "u3", "s1")


def test_a_reference_to_an_anchor_shared_by_alternatives_names_the_first() -> None:
    body = ("# A\n\n- plain\n- by courts {#i when=forum:courts}\n- by arbitration {#i when=forum:arb}\n\n"
            "Courts. {#p when=forum:courts}\n\nArb. {#p when=forum:arb}\n\n"
            "# D {#d when=forum:courts}\n\n# D {#d when=forum:arb}\n\n"
            "See {{ref: i}}, {{ref: p}}, {{ref: d}}.\n")
    tree = tree_of(body, ALTERNATIVES, overrides={"paragraphs.numbered": True})
    assert [(r.text, r.target, r.target_unit, r.section_unit) for r in refs(tree)] == [
        ("1(b)", "i", "u2", "s1"), ("1.1", "p", "u4", "s1"), ("2", "d", "s2", "s2")]
    shared = [f.node for f in walk(tree) if f.node.shares_number_with]
    assert [n.unit for n in shared] == ["u3", "u5", "s3"]
    assert {n.shares_number_with for n in shared} == {"u2", "u4", "s2"}


def test_a_reference_in_an_alternative_to_its_own_shared_number() -> None:
    body = "# A\n\n- x {#i when=forum:courts}\n- y {#i when=forum:arb}\n\nSee {{ref: i}}.\n"
    tree = tree_of(body, ALTERNATIVES)
    assert [(r.target_unit, r.section_unit) for r in refs(tree)] == [("u1", "s1")]


def test_references_under_the_none_scheme_name_the_same_units() -> None:
    tree = tree_of(REFERENCES, overrides={**NONE, "paragraphs.numbered": True})
    assert [(r.text, r.target_unit, r.section_unit) for r in refs(tree)] == [
        ("Services", "s1", "s1"),
        ("Services (a)", "u2", "s1"),
        ("Services (a) (i)", "u3", "s1"),
        ("Services 1", "u1", "s1"),
        ("Other", "s2", "s2")]
    assert all(tree.sections[0].number is None for _ in refs(tree))


def test_references_that_do_not_resolve_are_failure_markers_not_cross_references() -> None:
    body = "# A\n\nSee {{ref: missing}}, {{ref: att}}, {{ref: }}, {{attach: att}}.\n"
    tree = tree_of(body, ATTACHED)
    assert refs(tree) == []
    markers = [i.text for i in iter_tree_inlines(tree) if isinstance(i, FailureMarker)]
    assert markers[:2] == ["[BROKEN REF: missing]", "[BROKEN REF: att]"]


def test_an_attachment_is_reached_by_attach_whose_link_is_the_placeholders_anchor() -> None:
    tree = tree_of("# A\n\nSee {{attach: att}}.\n", ATTACHED)
    value = next(i for i in iter_tree_inlines(tree) if isinstance(i, Value) and i.kind == "attach")
    assert value.href == "#" + tree.attachments[0].anchor
    assert refs(tree) == []


def test_a_copy_that_no_longer_links_names_no_unit() -> None:
    body = "# See {{ref: b}} {#a}\n\n# Target {#b}\n"
    tree = tree_of(body, overrides={"contents.enabled": True})
    in_heading = [i for i in tree.sections[0].title if isinstance(i, CrossRef)]
    assert [(r.target_unit, r.section_unit) for r in in_heading] == [("s2", "s2")]
    contents = [i for entry in tree.contents for i in entry.title if isinstance(i, CrossRef)]
    assert [(r.text, r.target, r.target_unit, r.section_unit) for r in contents] == [("2", "", None, None)]


# -- Stability ---------------------------------------------------------------------


def test_the_same_document_gets_the_same_structure_every_time() -> None:
    first = tree_of(NESTED + "\n# B\n\nText.\n", style="continental")
    second = tree_of(NESTED + "\n# B\n\nText.\n", style="continental")
    assert first == second
    assert structure(first) == structure(second)


def test_unit_ids_are_unique_and_follow_document_order() -> None:
    tree = tree_of("- pre\n\n" + NESTED + "\n## B\n\nText.\n\n> - quoted\n\n# C\n\n- x\n",
                   overrides={"paragraphs.numbered": True})
    ids = [f.node.unit for f in walk(tree)]
    assert len(ids) == len(set(ids))
    numeric = [int(unit[1:]) for unit in ids if unit.startswith("u")]
    assert numeric == sorted(numeric) == list(range(1, len(numeric) + 1))
    assert [u for u in ids if u.startswith("s")] == ["s1", "s2", "s3"]


def test_unit_ids_are_the_same_when_designations_are_worked_out_again() -> None:
    """Under the none scheme a heading that holds a reference is designated by it, and the sections,
    items and paragraphs are then numbered a second time (docs/architecture.md): the ids, numbers
    and parents of that second pass are those a document without the reference gets."""
    items_and_paragraphs = "- x {#it}\n  - y\n\nOne. {#para}\n\n- z\n"
    with_reference = (f"# About {{{{ref: ov}}}} {{#h}}\n\n{items_and_paragraphs}\n# Overview {{#ov}}\n\n"
                      f"{items_and_paragraphs}\nSee {{{{ref: it}}}}, {{{{ref: para}}}}, {{{{ref: h}}}}.\n")
    without = with_reference.replace("About {{ref: ov}}", "About Overview")
    overrides = {**NONE, "paragraphs.numbered": True}
    again = tree_of(with_reference, overrides=overrides)
    once = tree_of(without, overrides=overrides)
    assert again.sections[0].designation == "About Overview"  # it was designated a second time
    assert structure(again) == structure(once)
    found = units(again)
    assert all(r.target_unit in found and r.section_unit in found for r in refs(again))
    # The first "it" and "para" are the section About's (u1 u2, u3 and u4 are its item, nested item,
    # paragraph and item), and the last section's are not the targets: the identifiers are the first's.
    assert {r.target: r.target_unit for r in refs(again)} == {"ov": "s2", "it": "u1", "para": "u3", "h": "s1"}


# -- The structure is enough to write every label and reference again --------------


class Recomputed:
    """Labels and designations worked out again from what the tree exposes: the unit ids, numbers,
    parents, list formats and ``RenderTree.formats``."""

    def __init__(self, tree: RenderTree) -> None:
        self.tree = tree
        self.found = units(tree)
        self.none = tree.formats.scheme == "none"
        self.memo: dict[str, str] = {}

    def parts(self, unit: str) -> list[int]:
        """The number of a section, part by part: a skipped level counts as 1."""
        chain: list[Section] = []
        current: str | None = unit
        while current is not None:
            section = self.found[current].node
            assert isinstance(section, Section)
            chain.append(section)
            current = section.parent_unit
        out: list[int] = []
        previous = 0
        for section in reversed(chain):
            assert section.number is not None
            out += [1] * (section.level - previous - 1) + [section.number]
            previous = section.level
        return out

    def section_designation(self, section: Section) -> str:
        if self.none:
            return section.designation or ""  # the heading's text: the tree's own
        designation = ""
        levels = self.tree.formats.headings
        for position, number in enumerate(self.parts(section.unit)):
            fmt = levels[min(position, len(levels) - 1)]
            designation = extend(designation, fill(fmt.ref, n=format_counter(number, fmt.counter)), textual=False)
        return designation

    def section_label(self, section: Section) -> str | None:
        if self.none:
            return None
        parts = self.parts(section.unit)
        levels = self.tree.formats.headings
        fmt = levels[min(len(parts), len(levels)) - 1]
        return fill(fmt.label, n=format_counter(parts[-1], fmt.counter), path=self.section_designation(section))

    def designation(self, unit: str | None) -> str:
        """What a reference to *unit* builds on: its designation, which an unnumbered unit has as its parent's."""
        if unit is None:
            return ""
        if unit not in self.memo:
            self.memo[unit] = self._designation(unit)
        return self.memo[unit]

    def _format(self, found: Found):
        if isinstance(found.node, Paragraph):
            return self.tree.formats.paragraphs
        assert found.owner is not None
        return found.owner.format

    def _designation(self, unit: str) -> str:
        found = self.found[unit]
        node = found.node
        if isinstance(node, Section):
            return self.section_designation(node)
        if node.number is None:
            return self.designation(node.parent_unit)
        fmt = self._format(found)
        base = found.section.designation if found.section else ""
        return extend(self.designation(node.parent_unit),
                      fill(fmt.ref, n=format_counter(node.number, fmt.counter), section=base or ""),
                      textual=self.none)

    def label(self, unit: str) -> str | None:
        found = self.found[unit]
        node = found.node
        if isinstance(node, Section):
            return self.section_label(node)
        if node.number is None:
            return None
        fmt = self._format(found)
        base = found.section.designation if found.section else ""
        return fill(fmt.label, n=format_counter(node.number, fmt.counter), section=base or "",
                    path=self.designation(unit))

    def reference(self, ref: CrossRef) -> str:
        assert ref.target_unit is not None and ref.section_unit is not None
        target = self.found[ref.target_unit].node
        shown = self.designation(ref.target_unit if target.number is not None else ref.section_unit)
        return self.tree.formats.reference.replace("{designation}", shown)


def check(tree: RenderTree) -> None:
    """Every invariant of the tree's numbering structure."""
    order = walk(tree)
    ids = [f.node.unit for f in order]
    assert None not in ids
    assert len(ids) == len(set(ids)), "unit ids are unique"
    position = {unit: index for index, unit in enumerate(ids)}
    recompute = Recomputed(tree)
    formats = tree.formats
    for found in order:
        node = found.node
        unit = node.unit
        assert unit is not None
        if node.parent_unit is not None:
            assert node.parent_unit in position and position[node.parent_unit] < position[unit]
        if node.shares_number_with is not None:
            first = recompute.found[node.shares_number_with].node
            assert position[node.shares_number_with] < position[unit]
            assert node.number is not None and first.number == node.number and first.shares_number_with is None
        if isinstance(node, Section):
            assert node.label == recompute.label(unit), unit
            assert node.designation == recompute.designation(unit), unit
            assert (node.number is None) == (formats.scheme == "none")
            assert node.parent_unit is None or recompute.found[node.parent_unit].node.level < node.level
            continue
        assert node.label == recompute.label(unit), unit
        if isinstance(node, ListItem):
            assert found.owner is not None
            assert (node.number is None) == (found.owner.format is None) == (not found.owner.enumerated)
        else:
            assert (node.number is None) == (formats.paragraphs is None)
            assert node.parent_unit == (found.section.unit if found.section else None)
    for block in lists(tree):
        expected = (formats.renumber if block.family == "renumber"
                    else formats.enumerated[block.depth % len(formats.enumerated)] if block.family == "enumerated"
                    else None)
        assert block.format == expected
        assert (block.family == "bullet") == (not block.enumerated)
    for ref in refs(tree):
        if ref.target_unit is None:
            assert ref.section_unit is None and ref.target == ""  # a copy that no longer links
            continue
        assert ref.target_unit in position and ref.section_unit in position
        assert recompute.found[ref.target_unit].node.anchor == ref.target or ref.target == ""
        section = ref.section_unit
        target_found = recompute.found[ref.target_unit]
        assert section == (target_found.node.unit if isinstance(target_found.node, Section)
                           else target_found.section.unit)
        assert ref.text == recompute.reference(ref), (ref.text, ref.target)


STYLES = [("default", {}), ("outline", {}), ("continental", {}),
          ("default", NONE), ("continental", {**NONE, "paragraphs.numbered": True}),
          ("default", {"paragraphs.numbered": True, "enumeration.ordered": "enumerate"}),
          ("default", {"enumeration.enabled": False}),
          ("outline", {"references.format": "Section {designation}"})]
STYLE_IDS = ["default", "outline", "continental", "none", "continental-none", "enumerate", "bullets", "reference-format"]


@pytest.mark.parametrize(("style", "overrides"), STYLES, ids=STYLE_IDS)
def test_the_corpus_of_the_tests_is_numbered_as_its_structure_says(style: str, overrides: dict) -> None:
    for name in ("features.lgd", "template.lgd"):
        check(render((DOCUMENTS / name).read_text(encoding="utf-8"), format="text", style=style,
                     overrides=overrides).tree)


@pytest.mark.parametrize(("body", "overrides"), [
    (NESTED, {}), (REFERENCES, {}), (REFERENCES, {"paragraphs.numbered": True}), (REFERENCES, NONE),
    ("# A\n\n### B\n\n## C\n\n#### D\n\n### E\n\n- x {#x}\n\nSee {{ref: x}}, {{ref: e}}.\n", {}),
    ("# A\n\n### B\n\n## C\n\n#### D\n\n### E\n\n- x {#x}\n\nSee {{ref: x}}.\n", {"numbering.scheme": "mixed"}),
    ("# A\n\n> - q\n\n- x\n  > - y\n", {}),
    ("- p\n\n# A\n", {}),
], ids=["nested", "refs", "refs-paragraphs", "refs-none", "skips", "skips-mixed", "quotes", "preamble"])
@pytest.mark.parametrize("style", ["default", "outline", "continental"])
def test_small_documents_are_numbered_as_their_structure_says(body: str, overrides: dict, style: str) -> None:
    check(tree_of(body, style=style, overrides=overrides))


def test_alternatives_are_numbered_as_their_structure_says() -> None:
    body = ("# Disputes {#d when=forum:courts}\n\n## Sub one\n\n- a {#i when=forum:courts}\n- b {#i when=forum:arb}\n\n"
            "# Disputes {#d when=forum:arb}\n\n## Sub alt {#sub-alt}\n\nOne. {#p when=forum:arb}\n\n"
            "Two. {#p when=forum:courts}\n\nSee {{ref: d}}, {{ref: i}}, {{ref: p}}, {{ref: sub-alt}}.\n")
    for style, overrides in STYLES:
        check(tree_of(body, ALTERNATIVES, style=style, overrides={**overrides, "paragraphs.numbered": True}))


# -- The specification's corpus ----------------------------------------------------

SPEC = spec_root()
CORPUS: list[Path] = sorted([*(SPEC / "examples").rglob("*.lgd"), *(SPEC / "fixtures").rglob("*.lgd")]) if SPEC else []


@pytest.mark.conformance
@pytest.mark.skipif(SPEC is None, reason="LegalDown specification checkout not found")
@pytest.mark.parametrize(("style", "overrides"), STYLES[:5], ids=STYLE_IDS[:5])
def test_the_specifications_corpus_is_numbered_as_its_structure_says(style: str, overrides: dict) -> None:
    assert CORPUS, "no documents found in the specification checkout"
    rendered = 0
    for path in CORPUS:
        try:
            result = render(path.read_text(encoding="utf-8"), format="text", style=style, overrides=overrides)
        except DocumentError:
            continue
        try:
            check(result.tree)
        except AssertionError as error:  # name the document
            raise AssertionError(f"{path.relative_to(SPEC)}: {error}") from error
        rendered += 1
    assert rendered > 100
