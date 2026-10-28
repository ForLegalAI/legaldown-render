"""The body of the DOCX, written as a hand-made :class:`DocxPlan` says: numbering definitions, the
numbering of each paragraph (``w:numPr``, alternatives, indents), bookmarks and ``REF`` fields with
their cached results, hyperlinks, every inline and every block, escaping, validity against the
schemas (marker ``ooxml_schema``) and what LibreOffice shows once fields are updated (marker
``libreoffice``). The plans are written by hand, so these tests need no planner; the writer adds no
rule of its own, and what a plan does not say it does not write."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence
from typing import Any

import pytest
from docx_support import W_NS, libreoffice_export, parts, require_libreoffice, validation_errors

from legaldown_render import render
from legaldown_render.errors import InternalError
from legaldown_render.resolve.docx_plan import (
    AbstractNum,
    AltNumber,
    DocxPlan,
    FieldSeg,
    LevelDef,
    Literal_,
    Num,
    RefPlan,
    UnitPlan,
)
from legaldown_render.style import load_style
from legaldown_render.tree import (
    Blank,
    Block,
    Choice,
    Code,
    CodeBlock,
    CrossRef,
    DefinedTerm,
    DraftingNote,
    Emphasis,
    FailureMarker,
    HardBreak,
    Image,
    Inline,
    Link,
    List,
    ListItem,
    Paragraph,
    Quote,
    RenderTree,
    Rule,
    Section,
    SoftBreak,
    Strong,
    Table,
    TermRef,
    Text,
    Value,
)
from legaldown_render.writers import DocxWriter

W = f"{{{W_NS}}}"


# -- Building trees and plans by hand --------------------------------------------------------------------------


def tree(*blocks: Block, sections: Sequence[Section] = (), **settings: Any) -> RenderTree:
    return RenderTree(title=(Text("T"),), subtitle=(), language="en", preamble=tuple(blocks), sections=tuple(sections),
                      **settings)


def para(*inlines: Inline | str, **fields: Any) -> Paragraph:
    return Paragraph(tuple(Text(i) if isinstance(i, str) else i for i in inlines), **fields)


def item(*blocks: Block | str, unit: str | None = None, **fields: Any) -> ListItem:
    return ListItem(tuple(para(b) if isinstance(b, str) else b for b in blocks), unit=unit, **fields)


def bullets(*items: ListItem, depth: int = 0) -> List:
    return List(False, tuple(items), enumerated=True, family="enumerated", depth=depth)


def section(title: str, *blocks: Block, unit: str | None = None, level: int = 1, **fields: Any) -> Section:
    return Section(level=level, title=(Text(title),), identifier="", condition="", blocks=tuple(blocks), unit=unit,
                   **fields)


def unit(unit_id: str, kind: str = "paragraph", *, num_id: int | None = None, ilvl: int | None = None,
         explicit: bool = False, ind: tuple[int, int] | None = None, alt: AltNumber | None = None,
         bookmarks: tuple[str, ...] = (), toc: str | None = None) -> UnitPlan:
    return UnitPlan(unit=unit_id, kind=kind, num_id=num_id, ilvl=ilvl, explicit=explicit, ind=ind, alt=alt,  # type: ignore[arg-type]
                    label="", word_ref="", count=None, cause=(), bookmarks=bookmarks, toc_bookmark=toc)


def level(ilvl: int, fmt: str = "decimal", text: str | None = None, **fields: Any) -> LevelDef:
    return LevelDef(ilvl, fmt, text if text is not None else f"%{ilvl + 1}.", **fields)


def abstract(identifier: int, kind: str = "enumerated", levels: Sequence[LevelDef] = ()) -> AbstractNum:
    full = [*levels, *(level(i) for i in range(len(levels), 9))]
    return AbstractNum(identifier, kind, f"{0x4C440000 + identifier:08X}", "hybridMultilevel", tuple(full))  # type: ignore[arg-type]


def field(bookmark: str, cached: str = "1", *, number: bool = True, suppress: bool = False,
          hyperlink: bool = True) -> FieldSeg:
    return FieldSeg(bookmark, number, suppress, hyperlink, cached, cached)


def plan(*units: UnitPlan, abstracts: Sequence[AbstractNum] = (), nums: Sequence[Num] = (),
         heading: int | None = None, anchors: dict[str, str] | None = None,
         refs: dict[str, tuple[Any, ...]] | None = None) -> DocxPlan:
    return DocxPlan(abstract_nums=tuple(abstracts), nums=tuple(nums), heading_num_id=heading,
                    units={u.unit: u for u in units}, anchors=anchors or {},
                    refs={key: RefPlan(tuple(segments), True) for key, segments in (refs or {}).items()})


def build(document: RenderTree, the_plan: DocxPlan | None = None, **settings: Any) -> dict[str, bytes]:
    data = DocxWriter(load_style("default", overrides=settings.get("overrides", {})), generator="test").write(
        document, the_plan)
    return parts(data)


def body(document: RenderTree, the_plan: DocxPlan | None = None, **settings: Any) -> list[ET.Element]:
    """The children of ``w:body`` but the closing ``w:sectPr``, after the title paragraph."""
    root = ET.fromstring(build(document, the_plan, **settings)["word/document.xml"])
    children = list(root.find(f"{W}body"))  # type: ignore[arg-type]
    assert children[-1].tag == f"{W}sectPr"
    assert children[0].find(f"{W}pPr/{W}pStyle").get(f"{W}val") == "Title"  # type: ignore[union-attr]
    return children[1:-1]


# -- Reading paragraphs ------------------------------------------------------------------------------------------


def local(element: ET.Element) -> str:
    return element.tag.removeprefix(W)


def pstyle(paragraph: ET.Element) -> str | None:
    found = paragraph.find(f"{W}pPr/{W}pStyle")
    return None if found is None else found.get(f"{W}val")


def numbering(paragraph: ET.Element) -> tuple[int | None, int] | None:
    """``(ilvl, numId)`` of the paragraph's own ``w:numPr``."""
    found = paragraph.find(f"{W}pPr/{W}numPr")
    if found is None:
        return None
    ilvl = found.find(f"{W}ilvl")
    return (None if ilvl is None else int(ilvl.get(f"{W}val")), int(found.find(f"{W}numId").get(f"{W}val")))  # type: ignore[arg-type, union-attr]


def indent(paragraph: ET.Element) -> dict[str, int] | None:
    found = paragraph.find(f"{W}pPr/{W}ind")
    return None if found is None else {key.removeprefix(W): int(value) for key, value in found.attrib.items()}


def flow(paragraph: ET.Element) -> str:
    """The paragraph's content in one line: text, ``→`` for a tab and ``↵`` for a break, a run's character style
    and emphasis after it (``text@Style+b+i``), a field as ``{instruction|result}``, a bookmark as ``<name>…</name>``
    and a hyperlink as ``(link target: …)``."""
    names: dict[str, str] = {}
    out: list[str] = []

    def run(node: ET.Element) -> None:
        pieces = []
        for child in node:
            match local(child):
                case "t":
                    pieces.append(child.text or "")
                case "tab":
                    pieces.append("→")
                case "br":
                    pieces.append("↵")
                case "fldChar":
                    out.append({"begin": "{", "separate": "|", "end": "}"}[child.get(f"{W}fldCharType")])  # type: ignore[index]
                case "instrText":
                    out.append((child.text or "").strip())
        if pieces:
            suffix = ""
            style = node.find(f"{W}rPr/{W}rStyle")
            if style is not None:
                suffix += "@" + style.get(f"{W}val")  # type: ignore[operator]
            for flag in ("b", "i"):
                if node.find(f"{W}rPr/{W}{flag}") is not None:
                    suffix += f"+{flag}"
            out.append("".join(pieces) + suffix)

    def walk(node: ET.Element) -> None:
        for child in node:
            match local(child):
                case "r":
                    run(child)
                case "bookmarkStart":
                    names[child.get(f"{W}id")] = child.get(f"{W}name")  # type: ignore[assignment, index]
                    out.append(f"<{child.get(f'{W}name')}>")
                case "bookmarkEnd":
                    out.append(f"</{names[child.get(f'{W}id')]}>")  # type: ignore[index]
                case "hyperlink":
                    target = child.get(f"{W}anchor") or child.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                    out.append(f"(link {target}: ")
                    walk(child)
                    out.append(")")

    walk(paragraph)
    return "".join(out)


# -- numbering.xml -----------------------------------------------------------------------------------------------


def test_the_numbering_part_holds_each_abstract_numbering_and_each_instance_in_the_plans_order() -> None:
    headings = abstract(0, "headings", [
        level(0, text="%1.", p_style="Heading1", left=567, hanging=567),
        level(1, text="%1.%2", p_style="Heading2", left=680, hanging=680, suff="space"),
        level(2, "lowerLetter", "(%3)", start=3)])
    bullet = abstract(1, "bullet", [LevelDef(0, "bullet", "•", left=567, hanging=360)])
    document = tree(para("x"))
    the_plan = plan(abstracts=[headings, bullet], nums=[Num(1, 0), Num(2, 1, ((0, 1), (1, 1))), Num(3, 1, ((0, 4),))],
                    heading=1)
    root = ET.fromstring(build(document, the_plan)["word/numbering.xml"])
    assert [local(child) for child in root] == ["abstractNum", "abstractNum", "num", "num", "num"]
    first = root[0]
    assert first.get(f"{W}abstractNumId") == "0"
    assert [local(child) for child in first][:3] == ["nsid", "multiLevelType", "lvl"]
    assert first.find(f"{W}nsid").get(f"{W}val") == "4C440000"  # type: ignore[union-attr]
    assert first.find(f"{W}multiLevelType").get(f"{W}val") == "hybridMultilevel"  # type: ignore[union-attr]
    assert len(first.findall(f"{W}lvl")) == 9
    lvl0, lvl1, lvl2 = first.findall(f"{W}lvl")[:3]
    assert [local(child) for child in lvl0] == ["start", "numFmt", "pStyle", "lvlText", "lvlJc", "pPr"]
    assert lvl0.get(f"{W}ilvl") == "0"
    assert lvl0.find(f"{W}pStyle").get(f"{W}val") == "Heading1"  # type: ignore[union-attr]
    assert lvl0.find(f"{W}lvlText").get(f"{W}val") == "%1."  # type: ignore[union-attr]
    assert dict(lvl0.find(f"{W}pPr/{W}ind").attrib) == {f"{W}left": "567", f"{W}hanging": "567"}  # type: ignore[union-attr]
    assert [local(child) for child in lvl1] == ["start", "numFmt", "pStyle", "suff", "lvlText", "lvlJc", "pPr"]
    assert lvl1.find(f"{W}suff").get(f"{W}val") == "space"  # type: ignore[union-attr]
    assert lvl2.find(f"{W}numFmt").get(f"{W}val") == "lowerLetter"
    assert lvl2.find(f"{W}start").get(f"{W}val") == "3"  # type: ignore[union-attr]
    assert lvl2.find(f"{W}pPr") is None  # a level with no indent writes none
    assert root[1].find(f"{W}lvl/{W}numFmt").get(f"{W}val") == "bullet"  # type: ignore[union-attr]
    assert root[1].find(f"{W}lvl/{W}lvlText").get(f"{W}val") == "•"  # type: ignore[union-attr]
    nums = root.findall(f"{W}num")
    assert [(n.get(f"{W}numId"), n.find(f"{W}abstractNumId").get(f"{W}val")) for n in nums] == [  # type: ignore[union-attr]
        ("1", "0"), ("2", "1"), ("3", "1")]
    assert [local(child) for child in nums[1]] == ["abstractNumId", "lvlOverride", "lvlOverride"]
    assert [(o.get(f"{W}ilvl"), o.find(f"{W}startOverride").get(f"{W}val")) for o in nums[1].findall(f"{W}lvlOverride")  # type: ignore[union-attr]
            ] == [("0", "1"), ("1", "1")]
    assert nums[0].find(f"{W}lvlOverride") is None


def test_the_headings_styles_are_linked_to_the_headings_num() -> None:
    document = tree(sections=[section("A", unit="s1")])
    styles = ET.fromstring(build(document, plan(abstracts=[abstract(0, "headings")], nums=[Num(1, 0)], heading=1)
                                 )["word/styles.xml"])
    by_id = {s.get(f"{W}styleId"): s for s in styles.findall(f"{W}style")}
    for number in range(1, 6):
        numpr = by_id[f"Heading{number}"].find(f"{W}pPr/{W}numPr")
        assert (numpr.find(f"{W}ilvl").get(f"{W}val"), numpr.find(f"{W}numId").get(f"{W}val")) == (str(number - 1), "1")  # type: ignore[union-attr]
    plain = ET.fromstring(build(document, plan())["word/styles.xml"])
    assert plain.find(f".//{W}style[@{W}styleId='Heading1']/{W}pPr/{W}numPr") is None


def test_an_empty_plan_writes_an_empty_numbering_part() -> None:
    assert list(ET.fromstring(build(tree(para("x")), plan())["word/numbering.xml"])) == []


# -- The numbering of a paragraph --------------------------------------------------------------------------------


def test_a_unit_writes_numpr_only_when_the_plan_says_it_is_explicit() -> None:
    document = tree(bullets(item("a", unit="u1"), item("b", unit="u2")),
                    sections=[section("Live", unit="s1", level=2), section("Own", unit="s2")])
    the_plan = plan(unit("s1", "section", num_id=1, ilvl=1), unit("s2", "section", num_id=3, ilvl=0, explicit=True),
                    unit("u1", "item", num_id=2, ilvl=0, explicit=True), unit("u2", "item", num_id=2, ilvl=0),
                    abstracts=[abstract(0, "headings"), abstract(1)],
                    nums=[Num(1, 0), Num(2, 1), Num(3, 0, ((0, 5),))], heading=1)
    children = body(document, the_plan)
    items = [c for c in children if pstyle(c) is None]
    heading_live, heading_own = (c for c in children if pstyle(c) and pstyle(c).startswith("Heading"))  # type: ignore[union-attr]
    assert (pstyle(heading_live), numbering(heading_live)) == ("Heading2", None)
    assert (pstyle(heading_own), numbering(heading_own)) == ("Heading1", (0, 3))
    assert [numbering(i) for i in items] == [(0, 2), None]


def test_numpr_is_in_schema_order_among_the_other_properties() -> None:
    document = tree(bullets(item("a", unit="u1")))
    the_plan = plan(unit("u1", "item", num_id=1, ilvl=2, explicit=True, ind=(1134, 567)), abstracts=[abstract(0)],
                    nums=[Num(1, 0)])
    (paragraph,) = body(document, the_plan)
    assert [local(child) for child in paragraph.find(f"{W}pPr")] == ["numPr", "ind"]  # type: ignore[union-attr]
    assert [local(child) for child in paragraph.find(f"{W}pPr/{W}numPr")] == ["ilvl", "numId"]  # type: ignore[union-attr]
    assert indent(paragraph) == {"left": 1134, "hanging": 567}


def test_a_unit_without_a_level_the_plan_names_writes_just_the_num() -> None:
    (paragraph,) = body(tree(bullets(item("a", unit="u1"))), plan(unit("u1", "item", num_id=1, explicit=True),
                                                                  abstracts=[abstract(0)], nums=[Num(1, 0)]))
    assert numbering(paragraph) == (None, 1)


def test_an_explicit_unit_with_no_num_is_a_bug_in_the_plan() -> None:
    with pytest.raises(InternalError, match="plan"):
        body(tree(bullets(item("a", unit="u1"))), plan(unit("u1", "item", explicit=True)))


def test_an_indent_is_written_where_the_plan_sets_one_and_a_hanging_of_zero_is_left_out() -> None:
    document = tree(bullets(item("a", unit="u1"), item("b", unit="u2"), item("c", unit="u3")))
    the_plan = plan(unit("u1", "item", ind=(900, 0)), unit("u2", "item", ind=(900, 300)), unit("u3", "item"))
    first, second, third = body(document, the_plan)
    assert indent(first) == {"left": 900}
    assert indent(second) == {"left": 900, "hanging": 300}
    assert indent(third) is None


def test_the_level_indent_is_not_repeated_on_a_paragraph_that_lives_on_its_level() -> None:
    document = tree(bullets(item("a", unit="u1")))
    the_plan = plan(unit("u1", "item", num_id=1, ilvl=1, explicit=True),
                    abstracts=[abstract(0, levels=[level(0), level(1, left=1134, hanging=567)])], nums=[Num(1, 0)])
    (paragraph,) = body(document, the_plan)
    assert indent(paragraph) is None


def test_a_later_alternative_has_no_numbering_its_number_as_fields_and_a_tab() -> None:
    alt = AltNumber((field("ld_first", "2"), Literal_(".")), "tab")
    document = tree(sections=[section("Courts", unit="s1"), section("Arbitration", unit="s2")])
    the_plan = plan(unit("s1", "section", bookmarks=("ld_first",)),
                    unit("s2", "section", num_id=0, ilvl=0, explicit=True, ind=(567, 567), alt=alt,
                         bookmarks=("ld_second",)))
    first, second = body(document, the_plan)
    assert numbering(first) is None
    assert numbering(second) == (0, 0)
    assert indent(second) == {"left": 567, "hanging": 567}
    assert flow(second) == "{REF ld_first \\n \\h|2}.→<ld_second>Arbitration</ld_second>"


def test_an_alternative_whose_plan_has_no_num_still_gets_num_zero() -> None:
    alt = AltNumber((field("a", "1"),), "space")
    document = tree(bullets(item("x", unit="u1")), para("a", unit="u2"))
    the_plan = plan(unit("u1", bookmarks=("a",)), unit("u2", explicit=True, alt=alt))
    first, second = body(document, the_plan)
    assert numbering(second) == (None, 0)
    assert flow(second) == "{REF a \\n \\h|1} a"


# -- Bookmarks ----------------------------------------------------------------------------------------------


def test_bookmarks_wrap_the_content_nested_with_the_first_outermost_and_the_toc_bookmark_innermost() -> None:
    document = tree(sections=[section("Heading", unit="s1")])
    (heading,) = body(document, plan(unit("s1", "section", bookmarks=("ld_h", "_Ld1"), toc="_Toc100000001")))
    assert flow(heading) == "<ld_h><_Ld1><_Toc100000001>Heading</_Toc100000001></_Ld1></ld_h>"


def test_bookmark_ids_are_unique_and_pair_each_start_with_its_end() -> None:
    document = tree(bullets(item("a", unit="u1"), item("b", unit="u2")), sections=[section("H", unit="s1")])
    the_plan = plan(unit("s1", "section", bookmarks=("a1", "a2")), unit("u1", bookmarks=("b1",)),
                    unit("u2", bookmarks=("b2",), toc="_Toc1"))
    root = ET.fromstring(build(document, the_plan)["word/document.xml"])
    starts = [(s.get(f"{W}id"), s.get(f"{W}name")) for s in root.iter(f"{W}bookmarkStart")]
    ends = [e.get(f"{W}id") for e in root.iter(f"{W}bookmarkEnd")]
    assert len({i for i, _ in starts}) == len(starts) == 5
    assert sorted(i for i, _ in starts) == sorted(ends)
    assert [name for _, name in starts] == ["b1", "b2", "_Toc1", "a1", "a2"]


def test_condition_labels_and_an_alternatives_number_stay_outside_the_bookmarks() -> None:
    alt = AltNumber((field("x", "1"),), "tab")
    document = tree(para("Text", unit="u1", condition_label="if A"), bullets(item("held", unit="u2")),
                    sections=[section("Title", unit="s1", condition_label="if B")])
    the_plan = plan(unit("u1", bookmarks=("p",), alt=alt, explicit=True), unit("s1", "section", bookmarks=("h",)),
                    unit("u2", bookmarks=("x",)))
    paragraph, _, heading = body(document, the_plan)
    assert flow(paragraph) == "{REF x \\n \\h|1}→if A@ConditionLabel <p>Text</p>"
    assert flow(heading) == "<h>Title</h> if B@ConditionLabel"


def test_an_item_that_opens_with_a_list_is_an_empty_numbered_paragraph_with_the_bookmarks_then_the_list() -> None:
    nested = bullets(item("inner", unit="u2"), depth=1)
    document = tree(bullets(item(nested, unit="u1", condition_label="if C"), item("plain", unit="u3")))
    the_plan = plan(unit("u1", "item", num_id=1, ilvl=0, explicit=True, bookmarks=("ld_one",)),
                    unit("u2", "item", num_id=1, ilvl=1, explicit=True, bookmarks=("ld_inner",)),
                    unit("u3", "item", num_id=1, ilvl=0, explicit=True),
                    abstracts=[abstract(0, levels=[level(0, left=567, hanging=567), level(1, left=1134, hanging=567)])],
                    nums=[Num(1, 0)])
    first, inner, last = body(document, the_plan)
    assert (numbering(first), flow(first)) == ((0, 1), "if C@ConditionLabel<ld_one></ld_one>")
    assert (numbering(inner), flow(inner)) == ((1, 1), "<ld_inner>inner</ld_inner>")
    assert (numbering(last), flow(last)) == ((0, 1), "plain")


@pytest.mark.parametrize("blocks", [(), (Quote((para("q"),)),), (CodeBlock("c"),)])
def test_an_item_without_a_first_paragraph_is_an_empty_numbered_paragraph(blocks: tuple[Block, ...]) -> None:
    document = tree(bullets(ListItem(blocks, unit="u1")))
    the_plan = plan(unit("u1", "item", num_id=1, ilvl=0, explicit=True, bookmarks=("ld_x",)), abstracts=[abstract(0)],
                    nums=[Num(1, 0)])
    first, *rest = body(document, the_plan)
    assert (numbering(first), flow(first)) == ((0, 1), "<ld_x></ld_x>")
    assert len(rest) == len(blocks)


def test_the_blocks_after_an_items_first_paragraph_continue_at_its_indent_unnumbered() -> None:
    document = tree(bullets(item("first", para("second"), CodeBlock("code"), bullets(item("n", unit="u2"), depth=1),
                                unit="u1")))
    the_plan = plan(unit("u1", "item", num_id=1, ilvl=0, explicit=True),
                    unit("u2", "item", num_id=1, ilvl=1, explicit=True),
                    abstracts=[abstract(0, levels=[level(0, left=567, hanging=567), level(1, left=1134, hanging=567)])],
                    nums=[Num(1, 0)])
    first, second, code, nested = body(document, the_plan)
    assert numbering(second) is None and indent(second) == {"left": 567} and flow(second) == "second"
    assert pstyle(code) == "Code" and indent(code) == {"left": 567 + 113}
    assert numbering(nested) == (1, 1)


def test_continuation_follows_an_indent_the_plan_sets_over_the_levels() -> None:
    document = tree(bullets(item("first", "more", unit="u1")))
    first, more = body(document, plan(unit("u1", "item", ind=(2000, 400))))
    assert indent(first) == {"left": 2000, "hanging": 400}
    assert indent(more) == {"left": 2000}


def test_a_unit_the_plan_does_not_know_is_a_plain_paragraph() -> None:
    document = tree(para("a", unit="u9"), bullets(item("b", unit="u8")), sections=[section("H", unit="s9")])
    paragraph, listed, heading = body(document, plan())
    assert [pstyle(paragraph), numbering(paragraph), flow(paragraph)] == [None, None, "a"]
    assert [pstyle(listed), numbering(listed), flow(listed)] == [None, None, "b"]
    assert [pstyle(heading), numbering(heading)] == ["Heading1", None]


# -- Cross-references and fields ---------------------------------------------------------------------------------


def test_a_cross_reference_is_its_literal_segments_and_ref_fields_with_the_cached_results() -> None:
    reference = CrossRef("Clause 1.2(b)", "x", "u9", "s2")
    document = tree(para("See ", reference, "."), sections=[section("H", unit="s1")])
    refs = {"u9": (Literal_("Clause "), field("ld_s", "1.2"), Literal_("("), field("_Ld1", "b", suppress=True),
                   Literal_(")"))}
    the_plan = plan(unit("s1", "section", bookmarks=("ld_s", "_Ld1")), refs=refs)
    paragraph, _ = body(document, the_plan)
    assert flow(paragraph) == "See Clause {REF ld_s \\n \\h|1.2}({REF _Ld1 \\n \\t \\h|b})."


def test_the_switches_follow_the_segment_in_a_fixed_order() -> None:
    cases = [(field("a"), "REF a \\n \\h"), (field("a", hyperlink=False), "REF a \\n"),
             (field("a", number=False), "REF a \\h"), (field("a", number=False, hyperlink=False), "REF a"),
             (field("a", suppress=True, hyperlink=False), "REF a \\n \\t"),
             (field("a", suppress=True), "REF a \\n \\t \\h")]
    for segment, instruction in cases:
        document = tree(para(CrossRef("x", "t", "u9")), bullets(item("y", unit="u1")))
        paragraph = body(document, plan(unit("u1", bookmarks=("a",)), refs={"u9": (segment,)}))[0]
        assert flow(paragraph) == "{" + instruction + "|1}"


def test_a_field_is_five_runs_the_cached_result_between_separate_and_end() -> None:
    document = tree(para(CrossRef("x", "t", "u9")), bullets(item("y", unit="u1")))
    paragraph = body(document, plan(unit("u1", bookmarks=("a",)), refs={"u9": (field("a", "7"),)}))[0]
    runs = paragraph.findall(f"{W}r")
    kinds = [(r.find(f"{W}fldChar").get(f"{W}fldCharType") if r.find(f"{W}fldChar") is not None
              else local(r[0])) for r in runs]
    assert kinds == ["begin", "instrText", "separate", "t", "end"]


def test_a_cached_result_that_differs_from_the_renderers_text_is_written_as_the_plan_gives_it() -> None:
    reference = CrossRef("1.1(bb)", "x", "u9")
    document = tree(para(reference), bullets(item("y", unit="u1")))
    refs = {"u9": (field("a", "1.1(aa)"),)}
    plan_ = DocxPlan((), (), None, {"u1": unit("u1", bookmarks=("a",))}, {}, {"u9": RefPlan(tuple(refs["u9"]), False)})
    assert flow(body(document, plan_)[0]) == "{REF a \\n \\h|1.1(aa)}"


def test_a_cross_reference_without_fields_shows_its_text() -> None:
    document = tree(para("see ", CrossRef("1.2", "x", "u9"), " and ", CrossRef("3", "", None, None), " and ",
                         CrossRef("4", "x", "u7")))
    (paragraph,) = body(document, plan(refs={}))
    assert flow(paragraph) == "see 1.2 and 3 and 4"


def test_a_field_to_a_bookmark_the_document_does_not_have_is_a_bug_in_the_plan() -> None:
    document = tree(para(CrossRef("x", "t", "u9")))
    with pytest.raises(InternalError, match="'nowhere'"):
        body(document, plan(refs={"u9": (field("nowhere"),)}))


def test_a_reference_inside_a_link_stays_a_field_and_text_stays_one_link() -> None:
    link = Link("https://example.com/", (Text("see "), CrossRef("1", "x", "u9"), TermRef("t", "def:t", "bold")))
    document = tree(para(link), bullets(item("y", unit="u1")))
    the_plan = plan(unit("u1", bookmarks=("a",)), anchors={"def:t": "lddef_t"}, refs={"u9": (field("a", "1"),)})
    (paragraph,) = body(document, the_plan)[:1]
    assert flow(paragraph) == "(link rId5: see @Hyperlink{REF a \\n \\h|1@Hyperlink}t@TermReference)"


# -- Terms, definitions, attachments, markers -----------------------------------------------------------------


def defined(anchor: str | None = "def:t", *, defining: bool = True) -> DefinedTerm:
    return DefinedTerm((Text("Term"),), anchor, "bold", defining)


def test_a_defined_term_is_a_bookmark_around_text_in_the_defined_term_style() -> None:
    document = tree(para("A ", defined(), " is"))
    (paragraph,) = body(document, plan(anchors={"def:t": "lddef_t"}))
    assert flow(paragraph) == "A <lddef_t>Term@DefinedTerm</lddef_t> is"


@pytest.mark.parametrize(("term", "anchors"), [(defined(None), {}), (defined(defining=False), {"def:t": "lddef_t"}),
                                              (defined(), {})])
def test_a_term_that_defines_nothing_here_or_has_no_bookmark_is_styled_text(term: DefinedTerm,
                                                                           anchors: dict[str, str]) -> None:
    (paragraph,) = body(tree(para(term)), plan(anchors=anchors))
    assert flow(paragraph) == "Term@DefinedTerm"


def test_a_term_reference_is_an_internal_hyperlink_to_the_definition_with_the_term_as_text() -> None:
    document = tree(para("the ", TermRef("terms", "def:t", "bold"), " apply"), para(defined()))
    first, _ = body(document, plan(anchors={"def:t": "lddef_t"}))
    assert flow(first) == "the (link lddef_t: terms@TermReference) apply"
    link = first.find(f"{W}hyperlink")
    assert link.get(f"{W}anchor") == "lddef_t" and link.get(f"{W}history") == "1"  # type: ignore[union-attr]


@pytest.mark.parametrize("term", [TermRef("a", None, "bold"), TermRef("a", "def:other", "bold"),
                                  TermRef("a", "def:t", "bold")])
def test_a_term_reference_with_no_definition_in_the_document_is_styled_text(term: TermRef) -> None:
    (paragraph,) = body(tree(para(term)), plan(anchors={"def:t": "lddef_t"}))
    assert flow(paragraph) == "a@TermReference"


def attachment_tree(value: Value, titles: Sequence[str] = ("Schedule A",)) -> RenderTree:
    from legaldown_render.tree import AttachmentPart

    attachments = tuple(AttachmentPart(f"a{i}", (Text(title),), "f.lgd", f"att-{i}") for i, title in enumerate(titles))
    return tree(para("see ", value), attachments=attachments)


def test_an_attachment_value_is_a_field_when_it_is_the_title_else_a_link_with_its_text_and_text_when_unplaced() -> None:
    def paragraph(value: Value, *, placed: bool = True) -> str:
        # The attachments section is not written yet: a unit stands in for the bookmark it will place.
        the_plan = plan(unit("u1", bookmarks=("ldatt_att_0",)) if placed else unit("u1"),
                        anchors={"att-0": "ldatt_att_0"})
        document = tree(para("see ", value), bullets(item("x", unit="u1")),
                        attachments=attachment_tree(value).attachments)
        return flow(body(document, the_plan)[0])

    assert paragraph(Value("attach", "Schedule A", "#att-0")) == "see {REF ldatt_att_0 \\h|Schedule A}"
    assert paragraph(Value("attach", "the schedule", "#att-0")) == "see (link ldatt_att_0: the schedule@Hyperlink)"
    assert paragraph(Value("attach", "Schedule A", "#att-0"), placed=False) == "see Schedule A"
    assert paragraph(Value("attach", "x", "#att-9")) == "see x"
    assert paragraph(Value("attach", "x", None)) == "see x"


def test_a_value_that_links_to_a_file_is_an_external_hyperlink_when_the_document_may_link_there() -> None:
    document = tree(para(Value("attach", "price list", "https://example.com/p.pdf"), " ",
                         Value("attach", "other", "files/p.pdf"), " ", Value("date", "1 May", None)))
    (paragraph,) = body(document, plan())
    assert flow(paragraph) == "(link rId5: price list@Hyperlink) other 1 May"


def test_failure_blank_and_choice_are_runs_in_their_styles() -> None:
    document = tree(para(FailureMarker("[BROKEN REF: x]"), " ", Blank("b", "____", "your name"), " ", Blank("c", "[TBD]"),
                         " ", Choice("q", ("one", "two"), " | ")))
    (paragraph,) = body(document, plan())
    assert flow(paragraph) == "[BROKEN REF: x]@FailureMarker ____@Blank (your name)+i [TBD]@Blank [one | two]@Choice"


# -- Inlines ----------------------------------------------------------------------------------------------------


def test_emphasis_strong_and_code_are_runs_and_nest() -> None:
    document = tree(para("a ", Emphasis((Text("e"), Strong((Text("both"),)))), Strong((Text("s"),)), Code("c"),
                         Emphasis((Code("ic"),)), SoftBreak(), "z", HardBreak(), "y"))
    (paragraph,) = body(document, plan())
    assert flow(paragraph) == "a e+i" "both+b+i" "s+b" "c@InlineCode" "ic@InlineCode+i" " z↵y"


def test_run_properties_are_in_schema_order() -> None:
    document = tree(para(Strong((Emphasis((Code("x"),)),))))
    run = body(document, plan())[0].find(f"{W}r")
    assert [local(child) for child in run.find(f"{W}rPr")] == ["rStyle", "b", "bCs", "i", "iCs"]  # type: ignore[union-attr]


def test_an_external_link_is_a_hyperlink_by_relationship_and_a_refused_one_is_its_text() -> None:
    link = Link("https://example.com/a b", (Text("site"),), (Text("tip"),))
    refused = Link("javascript:alert(1)", (Text("bad"),))
    document = tree(para(link, " ", refused, " ", Link("https://example.com/a b", (Text("again"),))))
    contents = build(document, plan())
    paragraph = [c for c in ET.fromstring(contents["word/document.xml"]).find(f"{W}body") if pstyle(c) is None][0]  # type: ignore[union-attr]
    assert flow(paragraph) == "(link rId5: site@Hyperlink) bad (link rId5: again@Hyperlink)"
    rels = ET.fromstring(contents["word/_rels/document.xml.rels"])
    external = [r for r in rels if r.get("TargetMode") == "External"]
    assert [(r.get("Id"), r.get("Target")) for r in external] == [("rId5", "https://example.com/a%20b")]
    assert paragraph.find(f"{W}hyperlink").get(f"{W}tooltip") == "tip"  # type: ignore[union-attr]


def test_a_link_with_no_text_shows_its_address_and_emphasis_inside_it_keeps_the_link_style() -> None:
    document = tree(para(Link("mailto:a@example.com", ()), " ", Link("https://e.com/", (Emphasis((Text("em"),)),))))
    (paragraph,) = body(document, plan())
    assert flow(paragraph) == "(link rId5: mailto:a@example.com@Hyperlink) (link rId6: em@Hyperlink+i)"


def test_an_internal_link_to_an_anchor_of_the_document_is_a_hyperlink_to_its_bookmark() -> None:
    document = tree(para(Link("#clause", (Text("here"),)), " ", Link("#missing", (Text("there"),))),
                    bullets(item("x", unit="u1")))
    first, _ = body(document, plan(unit("u1", bookmarks=("ld_clause",)), anchors={"clause": "ld_clause"}))
    assert flow(first) == "(link ld_clause: here@Hyperlink) there"


def test_an_image_is_its_alt_text() -> None:
    (paragraph,) = body(tree(para("a", Image("x.png", (Text("a chart"),)), Image("y.png", ()))), plan())
    assert flow(paragraph) == "aa chart"


def test_text_with_tabs_and_line_breaks_becomes_tab_and_break_elements() -> None:
    (paragraph,) = body(tree(para("a\tb\nc\r\nd")), plan())
    assert flow(paragraph) == "a→b↵c↵d"


def test_a_space_at_either_end_of_a_run_is_kept() -> None:
    (paragraph,) = body(tree(para(" a ")), plan())
    assert paragraph.find(f"{W}r/{W}t").get("{http://www.w3.org/XML/1998/namespace}space") == "preserve"  # type: ignore[union-attr]


# -- Blocks -----------------------------------------------------------------------------------------------------


def test_a_quote_is_paragraphs_in_the_quote_style_and_a_note_has_its_label_run() -> None:
    document = tree(Quote((para("q1"), para("q2"))), DraftingNote((para("n"),), "Drafting note"),
                    DraftingNote((para("m"),), None))
    q1, q2, label, note, bare = body(document, plan())
    assert [(pstyle(p), flow(p)) for p in (q1, q2, label, note, bare)] == [
        ("Quote", "q1"), ("Quote", "q2"), ("DraftingNote", "Drafting note@DraftingNoteLabel"),
        ("DraftingNote", "n"), ("DraftingNote", "m")]
    assert all(indent(p) is None for p in (q1, q2, label, note, bare))


def test_a_list_in_a_quote_adds_the_quotes_indent_to_its_own_and_its_continuation() -> None:
    inner = bullets(item("a", "more", unit="u1"))
    document = tree(Quote((para("lead"), inner)), DraftingNote((inner,), "N"))
    the_plan = plan(unit("u1", "item", num_id=1, ilvl=0, explicit=True), abstracts=[abstract(0, levels=[
        level(0, left=567, hanging=567)])], nums=[Num(1, 0)])
    lead, a, more, label, a2, more2 = body(document, the_plan)
    assert (pstyle(a), numbering(a), indent(a)) == ("Quote", (0, 1), {"left": 567 + 567, "hanging": 567})
    assert (pstyle(more), numbering(more), indent(more)) == ("Quote", None, {"left": 567 + 567})
    assert indent(a2) == {"left": 567 + 227, "hanging": 567} and indent(more2) == {"left": 567 + 227}
    assert pstyle(a2) == pstyle(more2) == "DraftingNote"


def test_a_quote_inside_a_note_carries_both_indents() -> None:
    document = tree(DraftingNote((Quote((para("q"),)), Rule()), None), Quote((DraftingNote((para("n"),), None),)))
    q, rule, n = body(document, plan())
    assert (pstyle(q), indent(q)) == ("Quote", {"left": 227 + 567})
    assert (pstyle(rule), indent(rule)) == ("DraftingNote", None)
    assert (pstyle(n), indent(n)) == ("DraftingNote", {"left": 567 + 227})


def test_a_code_block_is_one_paragraph_per_line_in_the_code_style() -> None:
    document = tree(CodeBlock("a\n  b\n\n\tc\n"), CodeBlock(""))
    *lines, empty = body(document, plan())
    assert [(pstyle(p), flow(p)) for p in lines] == [("Code", "a"), ("Code", "  b"), ("Code", ""), ("Code", "→c")]
    assert pstyle(empty) == "Code"


def test_a_rule_is_a_bottom_border() -> None:
    (rule,) = body(tree(Rule()), plan())
    border = rule.find(f"{W}pPr/{W}pBdr/{W}bottom")
    assert border is not None and border.get(f"{W}val") == "single"
    assert flow(rule) == ""


def table(*rows: Sequence[str], header: Sequence[str] = ("H1", "H2"), align: tuple[str, ...] = ()) -> Table:
    def cells(row: Sequence[str]) -> tuple[tuple[Inline, ...], ...]:
        return tuple((Text(cell),) for cell in row)

    return Table(cells(header), tuple(cells(row) for row in rows), align)


def test_a_table_is_a_grid_with_a_repeated_header_row_and_aligned_columns() -> None:
    document = tree(para("before"), table(("a", "b"), ("c", "d"), align=("right", "center")), para("after"))
    before, tbl, after = body(document, plan())
    assert local(tbl) == "tbl" and flow(before) == "before" and flow(after) == "after"
    assert [local(child) for child in tbl] == ["tblPr", "tblGrid", "tr", "tr", "tr"]
    props = tbl.find(f"{W}tblPr")
    assert props.find(f"{W}tblStyle").get(f"{W}val") == "TableGrid"  # type: ignore[union-attr]
    assert [local(child) for child in props] == ["tblStyle", "tblW", "tblCellMar", "tblLook"]  # type: ignore[arg-type]
    widths = [int(g.get(f"{W}w")) for g in tbl.findall(f"{W}tblGrid/{W}gridCol")]  # type: ignore[arg-type]
    assert len(widths) == 2 and abs(widths[0] - widths[1]) <= 1
    assert int(props.find(f"{W}tblW").get(f"{W}w")) == sum(widths)  # type: ignore[union-attr, arg-type]
    rows = tbl.findall(f"{W}tr")
    assert rows[0].find(f"{W}trPr/{W}tblHeader") is not None
    assert all(r.find(f"{W}trPr") is None for r in rows[1:])
    cells = [[(flow(c.find(f"{W}p")), c.find(f"{W}p/{W}pPr/{W}jc").get(f"{W}val")) for c in r.findall(f"{W}tc")]  # type: ignore[arg-type, union-attr]
             for r in rows]
    assert cells == [[("H1", "right"), ("H2", "center")], [("a", "right"), ("b", "center")],
                     [("c", "right"), ("d", "center")]]
    assert [int(c.find(f"{W}tcPr/{W}tcW").get(f"{W}w")) for c in rows[0].findall(f"{W}tc")] == widths  # type: ignore[arg-type, union-attr]


def test_a_cell_holds_inlines_and_a_short_row_is_completed_with_empty_cells() -> None:
    document = tree(Table(((Text("A"),), (Text("B"),), (Text("C"),)),
                          (((Strong((Text("x"),)),), (TermRef("t", None, "bold"),)),), ("", "left")))
    (tbl,) = [c for c in body(document, plan()) if local(c) == "tbl"]
    row = tbl.findall(f"{W}tr")[1]
    assert [flow(c.find(f"{W}p")) for c in row.findall(f"{W}tc")] == ["x+b", "t@TermReference", ""]
    assert len(tbl.findall(f"{W}tblGrid/{W}gridCol")) == 3
    assert [c.find(f"{W}p/{W}pPr/{W}jc").get(f"{W}val") for c in row.findall(f"{W}tc")] == ["left"] * 3  # type: ignore[union-attr]


def test_two_tables_in_a_row_and_a_table_at_the_end_get_an_empty_paragraph_after_or_between() -> None:
    first, between, second, last = body(tree(table(), table()), plan())
    assert [local(first), local(between), local(second), local(last)] == ["tbl", "p", "tbl", "p"]
    assert flow(between) == flow(last) == ""
    assert [local(c) for c in body(tree(para("x"), table()), plan())] == ["p", "tbl", "p"]
    assert [local(c) for c in body(tree(table(), para("x")), plan())] == ["tbl", "p"]


def test_a_table_in_a_list_item_or_quote_is_indented_and_narrower() -> None:
    document = tree(bullets(item("a", table(), unit="u1")), Quote((table(),)))
    the_plan = plan(unit("u1", "item", ind=(1000, 0)))
    _, in_item, in_quote = (c for c in body(document, the_plan) if local(c) in ("tbl", "p") and (local(c) == "tbl" or flow(c) == "a"))
    for tbl, left in ((in_item, 1000), (in_quote, 567)):
        props = tbl.find(f"{W}tblPr")
        assert int(props.find(f"{W}tblInd").get(f"{W}w")) == left  # type: ignore[union-attr, arg-type]
        full = int(ET.fromstring(build(tree(table()), plan())["word/document.xml"]).find(  # type: ignore[arg-type]
            f".//{W}tblW").get(f"{W}w"))
        assert int(props.find(f"{W}tblW").get(f"{W}w")) == full - left  # type: ignore[union-attr, arg-type]


def test_a_table_with_no_columns_is_left_out() -> None:
    assert body(tree(Table((), (), ()), para("x")), plan()) and len(body(tree(Table((), (), ())), plan())) == 0


# -- Headings and condition labels ------------------------------------------------------------------------------


def test_headings_use_the_heading_styles_and_are_clamped_to_five() -> None:
    document = tree(sections=[section("a", level=n, unit=f"s{n}") for n in (0, 1, 3, 5, 9)])
    assert [pstyle(h) for h in body(document, plan())] == ["Heading1", "Heading1", "Heading3", "Heading5", "Heading5"]


def test_a_condition_label_leads_a_paragraph_and_an_item_and_follows_a_heading_title() -> None:
    document = tree(para("p", condition_label="if A"), para("plain"),
                    bullets(item(para("first", condition_label="inner"), "second", unit="u1",
                                 condition_label="if B")), sections=[section("H", condition_label="if C", unit="s1")])
    the_plan = plan(unit("u1", "item", ind=(567, 0)))
    p, plain, item_paragraph, second, heading = body(document, the_plan)
    assert flow(p) == "if A@ConditionLabel p" and flow(plain) == "plain"
    assert flow(item_paragraph) == "if B@ConditionLabel inner@ConditionLabel first"
    assert flow(heading) == "H if C@ConditionLabel"


# -- Bookmarks the document does not write, indents and widths, links with no text ----------------------------


def test_a_bookmark_that_is_only_in_text_the_document_never_writes_is_not_a_target() -> None:
    """A defined term inside alt text or a link's title is plan, not document: a reference to it has no target."""
    term = DefinedTerm((Text("Term"),), "def:t", "bold")
    document = tree(para(Image("i.png", (Text("alt "), term)), TermRef("Term", "def:t", "bold"),
                         Link("https://e.com/", (Text("l"),), (Text("tip "), term)), TermRef("Term", "def:t", "bold")))
    (paragraph,) = body(document, plan(anchors={"def:t": "lddef_t"}))
    assert flow(paragraph) == "alt TermTerm@TermReference(link rId5: l@Hyperlink)Term@TermReference"
    assert not list(paragraph.iter(f"{W}bookmarkStart"))
    with pytest.raises(InternalError, match="lddef_t"):
        body(tree(para(Image("i.png", (term,)), CrossRef("x", "t", "u9"))),
             plan(anchors={"def:t": "lddef_t"}, refs={"u9": (field("lddef_t"),)}))


def test_a_term_reference_before_its_definition_still_links_and_a_reference_after_it_too() -> None:
    document = tree(para(TermRef("T", "def:t", "bold"), DefinedTerm((Text("Term"),), "def:t", "bold"),
                         CrossRef("x", "t", "u9")))
    (paragraph,) = body(document, plan(anchors={"def:t": "lddef_t"}, refs={"u9": (field("lddef_t", number=False),)}))
    assert flow(paragraph) == "(link lddef_t: T@TermReference)<lddef_t>Term@DefinedTerm</lddef_t>{REF lddef_t \\h|1}"


def test_a_field_may_not_name_what_is_not_a_bookmark() -> None:
    for name in ("bad name", "1x", "a" * 41, "a\\n"):
        with pytest.raises(InternalError, match="valid"):
            body(tree(para(CrossRef("x", "t", "u9"))), plan(refs={"u9": (field(name),)}))


def test_every_hyperlink_has_content_and_a_link_that_is_not_made_still_shows_what_html_shows() -> None:
    nothing = Link("https://e.com/", (Image("p.png", ()),))
    document = tree(para(Link("docs/a.pdf", ()), " ", Link("#nope", ()), " ", nothing, " ",
                         Link("javascript:x", (Text("bad"),)), Link("javascript:x", ())))
    (paragraph,) = body(document, plan())
    assert flow(paragraph) == "docs/a.pdf #nope (link rId5: https://e.com/@Hyperlink) bad"
    assert all(len(link) for link in paragraph.iter(f"{W}hyperlink"))


def test_a_tooltip_is_cut_to_255_characters() -> None:
    (paragraph,) = body(tree(para(Link("https://e.com/", (Text("l"),), (Text("t" * 300),)))), plan())
    assert len(paragraph.find(f"{W}hyperlink").get(f"{W}tooltip")) == 255  # type: ignore[union-attr, arg-type]


def test_runs_in_a_link_that_take_another_style_keep_the_links_colour_and_underline() -> None:
    link = Link("https://e.com/", (Code("c"), TermRef("t", None, "bold"), Blank("b", "__"), Text("x")))
    (paragraph,) = body(tree(para(link)), plan())
    runs = paragraph.find(f"{W}hyperlink").findall(f"{W}r")  # type: ignore[union-attr]
    styles = [r.find(f"{W}rPr/{W}rStyle").get(f"{W}val") for r in runs]  # type: ignore[union-attr]
    assert styles == ["InlineCode", "TermReference", "Blank", "Hyperlink"]
    for run in runs[:3]:
        assert run.find(f"{W}rPr/{W}u").get(f"{W}val") == "single"  # type: ignore[union-attr]
        assert run.find(f"{W}rPr/{W}color") is not None
    assert runs[3].find(f"{W}rPr/{W}u") is None  # the Hyperlink style has them


def test_an_alternative_is_never_numbered_by_a_heading_style() -> None:
    """The plan sets numId 0 for an alternative; a heading that names an alternative but is not marked explicit still
    gets it, or the heading style's own numbering would count it."""
    alt = AltNumber((field("a", "1"),), "tab")
    document = tree(bullets(item("x", unit="u1")), sections=[section("H", unit="s1")])
    the_plan = plan(unit("u1", bookmarks=("a",)), unit("s1", "section", alt=alt, ind=(567, 567)))
    _, heading = body(document, the_plan)
    assert numbering(heading) == (None, 0)


def test_an_inline_or_block_the_writer_does_not_know_is_a_bug() -> None:
    from legaldown_render.tree import DirectiveSource

    with pytest.raises(InternalError, match="DirectiveSource"):
        body(tree(para(DirectiveSource(None))), plan())  # type: ignore[arg-type]
    with pytest.raises(InternalError, match="int"):
        body(tree(42), plan())  # type: ignore[arg-type]


def test_code_and_rules_in_a_quote_or_a_note_keep_the_bar_of_the_container() -> None:
    document = tree(Quote((para("q"), CodeBlock("c"), Rule())), DraftingNote((CodeBlock("n"), Rule()), None), Rule())
    q, code, rule, note_code, note_rule, plain_rule = body(document, plan())
    assert code.find(f"{W}pPr/{W}pBdr/{W}left").get(f"{W}color") == "C8C8C8"  # type: ignore[union-attr]
    assert indent(code) == {"left": 567}
    assert [local(c) for c in rule.find(f"{W}pPr/{W}pBdr")] == ["left", "bottom"]  # type: ignore[arg-type, union-attr]
    assert note_code.find(f"{W}pPr/{W}pBdr/{W}left").get(f"{W}color") == "D9A400"  # type: ignore[union-attr]
    assert [local(c) for c in note_rule.find(f"{W}pPr/{W}pBdr")] == ["left", "bottom"]  # type: ignore[arg-type, union-attr]
    assert [local(c) for c in plain_rule.find(f"{W}pPr/{W}pBdr")] == ["bottom"]  # type: ignore[arg-type, union-attr]


def test_the_table_look_says_what_its_flags_say() -> None:
    (tbl, _) = body(tree(table()), plan())
    look = tbl.find(f"{W}tblPr/{W}tblLook")
    flags = {"firstRow": 0x20, "lastRow": 0x40, "firstColumn": 0x80, "noHBand": 0x200, "noVBand": 0x400}
    assert int(look.get(f"{W}val"), 16) == sum(bit for name, bit in flags.items() if look.get(f"{W}{name}") == "1")  # type: ignore[union-attr, arg-type]


TEXT_WIDTH = 9072  # the default style's page: A4 with 2.5 cm margins


def lefts(root: ET.Element) -> list[int]:
    return [int(e.get(f"{W}left")) for e in root.iter(f"{W}ind") if e.get(f"{W}left")]  # type: ignore[arg-type]


def test_a_wide_indent_in_the_plan_cannot_push_the_text_off_the_page_or_squeeze_a_table() -> None:
    document = tree(bullets(item("a", table(header=("x", "y", "z")), unit="u1"), item("b", "c", unit="u2")))
    the_plan = plan(unit("u1", "item", ind=(9000, 567)), unit("u2", "item", ind=(99999, 99999)))
    result = body(document, the_plan)
    root = ET.Element("x")
    root.extend(result)
    assert all(left <= TEXT_WIDTH - 2268 for left in lefts(root))
    assert max(lefts(root)) == TEXT_WIDTH - 2268
    assert all(int(h.get(f"{W}hanging")) <= int(h.get(f"{W}left")) for h in root.iter(f"{W}ind") if h.get(f"{W}hanging"))  # type: ignore[arg-type]
    tbl = next(c for c in result if local(c) == "tbl")
    assert tbl.find(f"{W}tblPr/{W}tblInd") is None  # three columns do not fit beside it: the indent goes
    widths = [int(g.get(f"{W}w")) for g in tbl.findall(f"{W}tblGrid/{W}gridCol")]  # type: ignore[arg-type]
    assert min(widths) >= 1134 or sum(widths) == TEXT_WIDTH


def test_a_table_beside_an_indent_keeps_at_least_a_column_width_per_column() -> None:
    wide = Table(tuple((Text(c),) for c in "abc"), (), ())
    for left in (0, 1000, 5000, 8000):
        document = tree(bullets(item("a", wide, unit="u1")))
        tbl = next(c for c in body(document, plan(unit("u1", "item", ind=(left, 0)))) if local(c) == "tbl")
        widths = [int(g.get(f"{W}w")) for g in tbl.findall(f"{W}tblGrid/{W}gridCol")]  # type: ignore[arg-type]
        assert min(widths) >= 1134 and sum(widths) <= TEXT_WIDTH


def test_a_deeply_indented_list_stays_on_the_page() -> None:
    inner: Block = bullets(item("deepest", unit="u70"), depth=70)
    for depth in range(69, -1, -1):
        inner = bullets(item(f"d{depth}", inner, unit=f"u{depth}"), depth=depth)
    document = tree(inner)
    indents = [unit(f"u{depth}", "item", ind=((depth + 1) * 567, 0)) for depth in range(71)]
    data = DocxWriter(load_style("default")).write(document, plan(*indents))
    root = ET.fromstring(parts(data)["word/document.xml"])
    assert max(lefts(root)) <= min(31680, TEXT_WIDTH - 2268)


# -- Whole documents --------------------------------------------------------------------------------------------


def test_the_title_and_the_subtitle_come_first_and_the_section_properties_last() -> None:
    document = RenderTree(title=(Text("My Title"),), subtitle=(Text("Sub"),), language="en", preamble=(para("x"),),
                          sections=())
    root = ET.fromstring(build(document, plan())["word/document.xml"])
    children = list(root.find(f"{W}body"))  # type: ignore[arg-type]
    assert [(pstyle(c), flow(c)) for c in children[:-1]] == [("Title", "My Title"), ("Subtitle", "Sub"), (None, "x")]
    assert local(children[-1]) == "sectPr"


def test_a_rendered_document_is_numbered_cross_referenced_and_bookmarked_as_the_planner_says() -> None:
    result = render("---\ntitle: T\n---\n\n# A\n\n- a {#x}\n  - b\n\nSee {{ref: x}}.\n", format="docx")
    contents = parts(result.binary)  # type: ignore[arg-type]
    assert [child.tag.removeprefix(W) for child in ET.fromstring(contents["word/numbering.xml"])] == [
        "abstractNum", "abstractNum", "num", "num"]
    root = ET.fromstring(contents["word/document.xml"])
    assert [flow(p) for p in root.iter(f"{W}p")] == [
        "T", "<ld_a>A</ld_a>", "<ld_x>a</ld_x>", "b", "See {REF ld_a \\n \\h|1}{REF ld_x \\n \\h|(a)}."]
    assert [indent(p) for p in root.iter(f"{W}p")] == [None] * 5


def test_the_same_plan_and_tree_are_the_same_bytes() -> None:
    document = tree(bullets(item("a", unit="u1")), para(CrossRef("x", "t", "u9")))
    the_plan = plan(unit("u1", "item", num_id=1, ilvl=0, explicit=True, bookmarks=("a",)), abstracts=[abstract(0)],
                    nums=[Num(1, 0)], refs={"u9": (field("a"),)})
    assert build(document, the_plan) == build(document, the_plan)


# -- Escaping ---------------------------------------------------------------------------------------------------


EVIL = "x\x00\x01\x08\x0b\x1f\x7f\x85\ud800\ufffe<&>]]>\"'y"
CLEAN = "x<&>]]>\"'y"


def test_hostile_text_in_every_inline_and_block_is_dropped_by_the_sanitiser_and_the_part_stays_well_formed() -> None:
    document = tree(
        para(EVIL, Emphasis((Text(EVIL),)), Code(EVIL), Link("https://e.com/" + EVIL, (Text(EVIL),), (Text(EVIL),)),
             Image("i", (Text(EVIL),)), CrossRef(EVIL, "t", "u9"), TermRef(EVIL, "def:t", "bold"),
             DefinedTerm((Text(EVIL),), "def:t", "bold"), Value("attach", EVIL, "#att"), Blank("b", EVIL, EVIL),
             Choice(EVIL, (EVIL, EVIL), EVIL), FailureMarker(EVIL), condition_label=EVIL, unit="u1"),
        Quote((para(EVIL),)), DraftingNote((para(EVIL),), EVIL), CodeBlock(EVIL), table((EVIL, EVIL), header=(EVIL, EVIL)),
        sections=[section(EVIL, unit="s1", condition_label=EVIL)])
    the_plan = plan(unit("u1", bookmarks=("a",)), anchors={"def:t": "lddef_t", "att": "a"},
                    refs={"u9": (Literal_(EVIL), field("a", EVIL))})
    contents = build(document, the_plan)
    for data in contents.values():
        ET.fromstring(data)
        data.decode("utf-8")
    text = contents["word/document.xml"].decode("utf-8")
    for dropped in ("\x00", "\x01", "\x08", "\x0b", "\x1f", "\x7f", "\x85", "\ufffe"):
        assert dropped not in text
    root = ET.fromstring(contents["word/document.xml"])
    assert CLEAN in "".join(t.text or "" for t in root.iter(f"{W}t"))
    # A field's instruction is the bookmark name and the switches, whatever the text around it says.
    assert {(i.text or "").strip() for i in root.iter(f"{W}instrText")} == {"REF a \\n \\h"}


# -- Validity ---------------------------------------------------------------------------------------------------

SOURCE = """---
legaldown: '0.2'
title: Body
---

# Alpha {#alpha}

Intro, see {{ref: nb}} and {{ref: beta}}.

## Beta {#beta}

- item one {#one}
  - nested a
  - nested b {#nb}

    Continued.
- item two
- - only nested
- item four

# Gamma

Last, see {{ref: alpha}}.
"""


def planned_document() -> tuple[RenderTree, DocxPlan]:
    """The tree of :data:`SOURCE` and a plan written by hand for it: numbered headings, a two-level list on its own
    numbering, and the references of the sentences as fields whose cached results are wrong on purpose."""
    document = render(SOURCE, format="text").tree
    units = {s.unit: s for s in document.sections}
    assert [s for s in units] == ["s1", "s2", "s3"]
    headings = abstract(0, "headings", [
        level(0, text="%1.", p_style="Heading1", left=567, hanging=567),
        level(1, text="%1.%2", p_style="Heading2", left=567, hanging=567)])
    items = abstract(1, "enumerated", [level(0, "lowerLetter", "(%1)", left=567, hanging=567),
                                       level(1, "lowerRoman", "(%2)", left=1134, hanging=567)])
    nums = [Num(1, 0), Num(2, 1, ((0, 1), (1, 1)))]
    list_units = {"u2": ("(a)", 0, ("ld_one",)), "u3": ("(i)", 1, ()), "u4": ("(ii)", 1, ("ld_nb",)),
                  "u5": ("(b)", 0, ()), "u6": ("(c)", 0, ()), "u7": ("(i)", 1, ()), "u8": ("(d)", 0, ())}
    planned = [unit("s1", "section", bookmarks=("ld_alpha",), toc="_Toc100000001"),
               unit("s2", "section", bookmarks=("ld_beta",), toc="_Toc100000002"),
               unit("s3", "section", toc="_Toc100000003"),
               unit("u1", "paragraph"), unit("u9", "paragraph")]
    planned += [unit(u, "item", num_id=2, ilvl=ilvl, explicit=True, bookmarks=marks)
                for u, (_, ilvl, marks) in list_units.items()]
    return document, plan(
        *planned, abstracts=[headings, items], nums=nums, heading=1,
        anchors={"alpha": "ld_alpha", "beta": "ld_beta", "one": "ld_one", "nb": "ld_nb"},
        refs={"u4": (field("ld_beta", "XXX"), field("ld_one", "XXX"), field("ld_nb", "XXX")),
              "s2": (field("ld_beta", "XXX"),), "s1": (Literal_("Section "), field("ld_alpha", "XXX"))})


def planned_data() -> bytes:
    document, the_plan = planned_document()
    return DocxWriter(load_style("default"), generator="test").write(document, the_plan)


@pytest.mark.ooxml_schema
def test_every_hand_planned_document_validates_against_the_schemas() -> None:
    from docx_support import require_schemas

    require_schemas()
    documents = [planned_data(), build_hostile(), build_everything()]
    for data in documents:
        assert validation_errors(data) == []


def build_hostile() -> bytes:
    document = tree(
        para(EVIL, Emphasis((Text(EVIL),)), Link("https://e.com/x", (Text(EVIL),), (Text(EVIL),)), Image("i", (Text(EVIL),)),
             FailureMarker(EVIL), Blank("b", EVIL, EVIL), condition_label=EVIL),
        table((EVIL, EVIL), header=(EVIL, EVIL)), sections=[section(EVIL, unit="s1")])
    return DocxWriter(load_style("default")).write(document, plan())


def build_everything() -> bytes:
    """Every block and inline kind, a table in a list in a quote, and every kind of unit the plan can describe."""
    alt = AltNumber((field("ld_a", "1"), Literal_(".")), "space")
    nested = bullets(item("inner", table(("a", "b")), unit="u3"), depth=1)
    document = tree(
        para("Intro ", defined(), " ", TermRef("term", "def:t", "bold"), " ", CrossRef("1", "a", "s1"), " ",
             Link("https://e.com/", (Emphasis((Strong((Text("l"),)),)),)), Image("i", (Text("alt"),)),
             Blank("b", "___", "p"), Choice("q", ("o", "p"), "/"), FailureMarker("[X]"), Code("c"), SoftBreak(),
             HardBreak(), Value("attach", "S", "#att-0"), condition_label="if A", unit="u1"),
        Quote((para("q"), bullets(item("x", nested, unit="u2")))),
        DraftingNote((para("n"), CodeBlock("a\nb"), Rule(), table(("1", "2"))), "Note"),
        table(("a", "b"), align=("left", "right")), table(("c", "d")),
        bullets(ListItem((nested,), unit="u4")),
        sections=[section("One", para("alt", unit="u5"), unit="s1"), section("Two", unit="s2", condition_label="if B")],
        attachments=attachment_tree(Value("attach", "S", "#att-0")).attachments)
    the_plan = plan(
        unit("s1", "section", bookmarks=("ld_a",), toc="_Toc1"),
        unit("s2", "section", num_id=0, ilvl=0, explicit=True, ind=(567, 567), alt=alt),
        unit("u1"), unit("u2", "item", num_id=1, ilvl=0, explicit=True, bookmarks=("ld_att",)),
        unit("u3", "item", num_id=1, ilvl=1, explicit=True), unit("u4", "item", num_id=1, ilvl=0, explicit=True),
        unit("u5", "paragraph", num_id=2, ilvl=0, explicit=True),
        abstracts=[abstract(0, "headings", [level(0, p_style="Heading1")]), abstract(1)], nums=[Num(1, 1), Num(2, 1, ((0, 1),))],
        heading=None, anchors={"def:t": "lddef_t", "att-0": "ld_att"}, refs={"s1": (field("ld_a", "1"),)})
    return DocxWriter(load_style("default")).write(document, the_plan)


# -- LibreOffice ------------------------------------------------------------------------------------------------


@pytest.mark.libreoffice
def test_libreoffice_numbers_the_hand_planned_document_and_updates_every_reference() -> None:
    """The cached results are wrong on purpose ('XXX'): what LibreOffice shows after updating fields is what the
    numbering definitions and the REF fields compute, which is what a lawyer sees after inserting a clause."""
    require_libreoffice()
    shown = libreoffice_export({"planned": planned_data()})["planned"]
    lines = [" ".join(line.split()) for line in shown.lines if line.strip()]
    assert lines == [
        "Body",
        "1. Alpha",
        "Intro, see 1.1(a)(ii) and 1.1.",
        "1.1 Beta",
        "(a) item one",
        "(i) nested a",
        "(ii) nested b",
        "Continued.",
        "(b) item two",
        "(c)",
        "(i) only nested",
        "(d) item four",
        "2. Gamma",
        "Last, see Section 1.",
    ]


def alternative_data() -> bytes:
    """A heading, a later alternative that shares its number (numId 0 and the number as a REF field, cached wrong), a
    paragraph with a hyperlink, a table and a closing paragraph."""
    link = Link("https://example.com/", (Text("site"),))
    document = tree(sections=[
        section("One", unit="s1"),
        section("Two", para("See ", link, "."), table(("a", "b"), ("c", "d")), para("End"), unit="s2")])
    the_plan = plan(
        unit("s1", "section", bookmarks=("ld_one",)),
        unit("s2", "section", num_id=0, ilvl=0, explicit=True, ind=(567, 567),
             alt=AltNumber((field("ld_one", "XXX"), Literal_(".")), "tab")),
        abstracts=[abstract(0, "headings", [level(0, text="%1.", p_style="Heading1", left=567, hanging=567)])],
        nums=[Num(1, 0)], heading=1)
    return DocxWriter(load_style("default"), generator="test").write(document, the_plan)


@pytest.mark.ooxml_schema
def test_the_alternative_document_validates() -> None:
    from docx_support import require_schemas

    require_schemas()
    assert validation_errors(alternative_data()) == []


@pytest.mark.libreoffice
def test_libreoffice_shows_an_alternatives_number_as_its_holders_a_table_and_a_hyperlinks_text() -> None:
    require_libreoffice()
    shown = libreoffice_export({"alt": alternative_data()})["alt"]
    lines = [" ".join(line.split()) for line in shown.lines if line.strip()]
    # "Two" is not counted: it shows the number of "One" as a field, not a counter of its own.
    assert lines == ["T", "1. One", "1. Two", "See site.", "H1", "H2", "a", "b", "c", "d", "End"]
