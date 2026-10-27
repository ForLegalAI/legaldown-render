"""Designations under the ``none`` scheme (§13.3): a section is designated by its heading as
rendered, references and terms resolved, and its items and paragraphs follow; what happens to
headings that refer to each other in a circle, too deeply, or at too great a length."""
from __future__ import annotations

import random
from pathlib import Path

import pytest
from conftest import FRONT, text

from legaldown_render import RenderResult, render
from legaldown_render.resolve.resolver import Resolver, _compose
from legaldown_render.tree import plain_text

NONE = {"numbering.scheme": "none"}


def lines(body: str, **settings: object) -> list[str]:
    return [line for line in text(body, overrides=NONE, **settings).splitlines() if line]


def rules(result: RenderResult) -> list[str]:
    return [d.rule for d in result.diagnostics if d.rule.startswith("render-")]


def run(body: str, **overrides: object) -> RenderResult:
    return render(FRONT + body, format="text", overrides={**NONE, **overrides})


def designations(body: str, **overrides: object) -> list[str]:
    return [s.designation for s in run(body, **overrides).tree.sections]


# -- What a heading designates ---------------------------------------------------

def test_a_reference_in_a_heading_shows_the_designation_of_its_target() -> None:
    out = lines("# About {{ref: ov}} here {#h}\n\n# Overview {#ov}\n\nSee {{ref: h}}.\n")
    assert out[1:4] == ["About Overview here", "Overview", "See About Overview here."]


def test_a_reference_to_a_later_heading_is_resolved_through_a_chain() -> None:
    body = "# A {{ref: b}} {#a}\n\n# B {{ref: c}} {#b}\n\n# C {#c}\n\nSee {{ref: a}}, {{ref: b}}.\n"
    assert designations(body) == ["A B C", "B C", "C"]
    assert "See A B C, B C." in text(body, overrides=NONE)


def test_a_reference_to_an_earlier_heading_too() -> None:
    body = "# C {#c}\n\n# B {{ref: c}} {#b}\n\n# A {{ref: b}} {#a}\n"
    assert designations(body) == ["C", "B C", "A B C"]


def test_a_reference_formats_the_designation_inside_the_heading() -> None:
    body = "# A {{ref: b}} {#a}\n\n# B {#b}\n\nSee {{ref: a}}.\n"
    assert designations(body, **{"references.format": "'{designation}'"}) == ["A 'B'", "B"]


def test_a_broken_reference_in_a_heading_shows_its_marker() -> None:
    assert designations("# A {{ref: nope}} {#a}\n") == ["A [BROKEN REF: nope]"]


def test_markup_is_dropped_and_white_space_is_collapsed() -> None:
    assert designations("# Pay  *by* **{{ref: b}}**   now {#a}\n\n# B {#b}\n") == ["Pay by B now", "B"]


def test_a_term_with_a_reference_in_a_heading() -> None:
    body = ('# About the {{term: fee}} {#fee-h}\n\n# Overview {#ov}\n\n"Fee per {{ref: ov}}" {{def: fee}}\n\n'
            "See {{ref: fee-h}}.\n")
    out = lines(body)
    assert "About the Fee per Overview" in out
    assert "See About the Fee per Overview." in out
    assert "Fee per Overview" in out  # the definition, as before


# -- Items and paragraphs follow their section -------------------------------------

def test_items_follow_the_final_designation_of_their_section() -> None:
    body = "# Termination {{ref: x}} {#t}\n\n- one {#it}\n  - nested {#nn}\n\n# X {#x}\n\nSee {{ref: it}} and {{ref: nn}}.\n"
    assert "See Termination X (a) and Termination X (a) (i)." in text(body, overrides=NONE)


def test_a_heading_may_refer_to_an_item_under_another_heading() -> None:
    body = "# Later {{ref: it}} {#l}\n\nSee {{ref: l}}.\n\n# Home {#h}\n\n- first {#it}\n  - nested {#nn}\n\n# X {{ref: nn}} {#x}\n"
    assert designations(body) == ["Later Home (a)", "Home", "X Home (a) (i)"]
    assert "See Later Home (a)." in text(body, overrides=NONE)


def test_paragraph_designations_follow_too() -> None:
    body = "# T {{ref: b}} {#a}\n\nOne. {#p}\n\n# B {#b}\n\nSee {{ref: p}}.\n"
    out = text(body, overrides={**NONE, "paragraphs.numbered": True,
                                "paragraphs.format": {"counter": "lower-alpha", "label": "({n})", "ref": "({n})"}})
    assert "See T B (a)." in out


def test_a_reference_to_an_unnumbered_item_shows_the_final_heading() -> None:
    result = run("# A {{ref: b}} {#a}\n\n- one {#i}\n\n# B {#b}\n\nSee {{ref: i}}.\n", **{"enumeration.enabled": False})
    assert "See A B." in result.output
    assert [d.rule for d in result.diagnostics if d.rule == "ref-not-enumerated"] == ["ref-not-enumerated"]


def test_an_empty_heading_designation_leaves_the_item_piece_alone() -> None:
    assert designations('# {{def: x}} {#e}\n\n- one {#i}\n\n# T {{ref: i}} {#t}\n') == ["", "T (a)"]


# -- render-ref-ambiguous reads the final designations ---------------------------------

def test_headings_that_differ_once_resolved_are_not_ambiguous() -> None:
    body = "# Part {{ref: a}} {#p1}\n\n# Part {{ref: b}} {#p2}\n\n# Alpha {#a}\n\n# Beta {#b}\n\nSee {{ref: p1}}.\n"
    result = run(body)
    assert "See Part Alpha." in result.output
    assert "render-ref-ambiguous" not in {d.rule for d in result.diagnostics}


def test_headings_that_are_the_same_once_resolved_are_ambiguous() -> None:
    result = run("# Fee {{ref: x}} {#f1}\n\n# Fee Xylo {#f2}\n\n# Xylo {#x}\n\nSee {{ref: f1}}.\n")
    assert "render-ref-ambiguous" in {d.rule for d in result.diagnostics}


def test_alternative_headings_keep_their_own_text_and_the_first_is_referenced() -> None:
    questions = FRONT.replace("---\n", "---\nquestions:\n  forum:\n    type: choice\n    choices:\n      courts: Courts\n"
                              "      arbitration: Arbitration\n", 1)
    body = ("# Disputes {{ref: z}} {#d when=forum:courts}\n\nSee {{ref: d}}.\n\n"
            "# Disputes {{ref: y}} {#d when=forum:arbitration}\n\n# Z {#z}\n\n# Y {#y}\n")
    result = render(questions + body, format="text", overrides=NONE)
    assert [s.designation for s in result.tree.sections] == ["Disputes Z", "Disputes Y", "Z", "Y"]
    assert "See Disputes Z." in result.output
    assert "render-ref-ambiguous" not in {d.rule for d in result.diagnostics}


# -- Anchors are given out once ------------------------------------------------------------

def test_a_rebuild_keeps_every_anchor() -> None:
    body = ('# *The "Buyer" {{def:}}* {{ref: b}} {#a}\n\n- one {#it}\n\nSee {{ref: it}}.\n\n# B {#b}\n')
    out = render(FRONT + body, format="html", standalone=False, overrides=NONE).output
    assert 'id="a"' in out and 'id="it"' in out and 'id="b"' in out
    assert 'id="def:buyer"' in out
    assert 'href="#it"' in out
    assert out.count('id="a"') == 1


# -- Circles, depth, length ----------------------------------------------------------------------

def test_headings_that_refer_to_each_other_are_cut_at_the_first_return() -> None:
    result = run("# A {{ref: b}} {#a}\n\n# B {{ref: a}} {#b}\n\nSee {{ref: a}}.\n")
    assert [s.designation for s in result.tree.sections] == ["A B A", "B A"]
    assert rules(result) == ["render-designation-cycle"]
    assert "'a'" in next(d.message for d in result.diagnostics if d.rule == "render-designation-cycle")


def test_a_heading_that_refers_to_itself_or_to_its_own_item() -> None:
    assert rules(run("# A {{ref: a}} {#a}\n")) == ["render-designation-cycle"]
    assert rules(run("# A {{ref: i}} {#a}\n\n- one {#i}\n")) == ["render-designation-cycle"]


def _doubling(n: int) -> str:
    return "".join(f"# H{i} {{{{ref: h{i + 1}}}}} {{{{ref: h{i + 1}}}}} {{#h{i}}}\n\n" for i in range(1, n)) + f"# H{n} {{#h{n}}}\n"


def test_a_doubling_chain_is_capped_and_reported_once() -> None:
    result = run(_doubling(200))
    assert max(len(s.designation) for s in result.tree.sections) <= 1000
    assert rules(result) == ["render-designation-limit"]
    assert len(result.output) < 200_000


def test_a_long_heading_without_references_is_not_cut_but_a_reference_to_it_is() -> None:
    heading = "word " * 300
    unreferenced = run(f"# {heading}{{#a}}\n\nText.\n")
    assert rules(unreferenced) == []
    assert len(unreferenced.tree.sections[0].designation) > 1000
    referenced = run(f"# {heading}{{#a}}\n\nSee {{{{ref: a}}}}.\n")
    assert rules(referenced) == ["render-designation-limit"]
    shown = next(line for line in referenced.output.splitlines() if line.startswith("See "))
    assert shown.endswith("\u2026.")
    assert 990 < len(shown) - len("See \u2026.") <= 1000


def test_thousands_of_references_to_a_long_heading_paste_a_bounded_text() -> None:
    result = run("# " + "x" * 5000 + " {#a}\n\n" + "{{ref: a}} " * 2000 + "\n")
    assert len(result.output) < 2000 * 1100


def _chain(n: int, *, forward: bool = True) -> str:
    ids = range(n) if forward else range(n - 1, -1, -1)
    return "".join(f"# S{i} {{{{ref: s{i + 1}}}}} {{#s{i}}}\n\n" for i in ids) + f"# S{n} {{#s{n}}}\n"


def test_a_long_forward_chain_is_cut_at_32_headings_and_reported() -> None:
    result = run(_chain(500))
    sections = result.tree.sections
    assert sections[0].designation == " ".join(f"S{i}" for i in range(33))
    assert sections[32].designation == " ".join(f"S{i}" for i in range(32, 65))
    assert rules(result) == ["render-designation-limit"]


def test_a_chain_inside_the_depth_limit_is_resolved_in_full() -> None:
    result = run(_chain(30))
    assert result.tree.sections[0].designation == " ".join(f"S{i}" for i in range(31))
    assert rules(result) == []


def _wrap(depth: int, inner: str) -> str:
    out = inner
    for i in range(depth):
        mark = ("*", "_", "**", "__")[i % 4]
        out = f"{mark}x {out} x{mark}"
    return out


def test_nested_formatting_does_not_exhaust_the_stack() -> None:
    # The builder allows 100 levels in a tree; headings read through each other multiply that.
    n = 60
    body = "".join(f"# {_wrap(90, f'S{i} {{{{ref: s{i + 1}}}}}')} {{#s{i}}}\n\n" for i in range(n)) + f"# S{n} {{#s{n}}}\n"
    result = run(body)
    assert "{{" not in result.output
    assert "render-designation-limit" in rules(result)


# -- Work -----------------------------------------------------------------------------------------

@pytest.fixture
def directives(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    count = {"n": 0}
    real = Resolver._directive

    def counting(self, directive):  # noqa: ANN001, ANN202
        count["n"] += 1
        return real(self, directive)

    monkeypatch.setattr(Resolver, "_directive", counting)

    def work(body: str) -> int:
        count["n"] = 0
        run(body)
        return count["n"]

    return work


@pytest.mark.parametrize("make", [_chain, lambda n: _chain(n, forward=False), _doubling])
def test_the_work_is_linear_in_the_headings_and_references(directives, make) -> None:  # noqa: ANN001
    small, large = directives(make(2500)), directives(make(5000))
    assert large <= 2.1 * small
    assert large <= 4 * 5000 + 100  # at most one directive per reference, in each of two passes


def test_a_chain_of_terms_in_a_heading_is_still_worked_out_once_per_term(directives) -> None:  # noqa: ANN001
    n = 20
    defs = [f'"T{i} {{{{term: t{i + 1}}}}} {{{{term: t{i + 1}}}}}" {{{{def: t{i}}}}}' for i in range(n)]
    defs.append(f'"T{n}" {{{{def: t{n}}}}}')
    body = "# About {{term: t0}}\n\nUsed: {{term: t0}}\n\n" + "\n\n".join(defs) + "\n"
    assert directives(body) <= 8 * n + 60


# -- Invariants on random documents --------------------------------------------------------------------

def _document(rng: random.Random) -> str:
    n = rng.randint(2, 6)
    ids = [f"h{i}" for i in range(n)]
    items = {i: [f"i{i}_{j}" for j in range(rng.randint(0, 3))] for i in range(n)}
    out = []
    for i in range(n):
        words = [f"W{i}"]
        later = [t for j in range(i + 1, n) for t in (ids[j], *items[j])]  # later ones only: no circle
        for _ in range(rng.randint(0, 2)):
            if later:
                words.append("{{ref: " + rng.choice(later) + "}}")
        out.append(f"# {' '.join(words)} {{#{ids[i]}}}\n")
        if items[i]:
            out.append("\n".join(f"- item {{#{x}}}" for x in items[i]) + "\n")
        everything = ids + [x for group in items.values() for x in group]
        out.append("See {{ref: " + rng.choice(everything) + "}}.\n")
    return "\n".join(out)


def _anchors(result) -> list[str | None]:  # noqa: ANN001
    found: list[str | None] = []

    def walk(blocks) -> None:  # noqa: ANN001
        for block in blocks:
            for item in getattr(block, "items", ()):
                found.append(item.anchor)
                walk(item.blocks)

    for section in result.tree.sections:
        found.append(section.anchor)
        walk(section.blocks)
    return found


@pytest.mark.parametrize("style", [None, "continental"])
def test_designations_equal_headings_as_rendered_and_anchors_do_not_move(
        monkeypatch: pytest.MonkeyPatch, style: str | None) -> None:
    seen: list[Resolver] = []
    real = Resolver.run
    monkeypatch.setattr(Resolver, "run", lambda self: (seen.append(self), real(self))[1])
    rng = random.Random(20261027)
    for _ in range(60):
        source = FRONT + _document(rng)
        result = render(source, format="text", style=style, overrides=NONE)
        for section in result.tree.sections:
            assert " ".join(plain_text(section.title).split()) == section.designation, source
        resolver = seen[-1]
        for target in resolver.targets.values():
            base = result.tree.sections[target.section_index].designation
            assert _compose(target.parts, base) == target.designation, source
        plain = render(source.replace("{{ref:", "{{party:"), format="text", style=style, overrides=NONE)
        assert _anchors(result) == _anchors(plain), source


@pytest.mark.parametrize("scheme", ["decimal", "none"])
@pytest.mark.parametrize("heading", [False, True])
def test_terms_with_nested_formatting_do_not_exhaust_the_stack(scheme: str, heading: bool) -> None:
    n = 40
    defs = "".join(f'"T{i} {_wrap(60, "{{term: t" + str(i + 1) + "}}")}" {{{{def: t{i}}}}}\n\n' for i in range(n))
    title = "A {{term: t0}}" if heading else "A"
    body = f"# {title}\n\nUsed: {{{{term: t0}}}}\n\n{defs}" + f'"End" {{{{def: t{n}}}}}\n'
    result = render(FRONT + body, format="text", overrides={"numbering.scheme": scheme})
    assert "{{" not in result.output
    assert "render-term-limit" in {d.rule for d in result.diagnostics}


# -- Limits that are reached inside terms --------------------------------------------------------


def test_a_term_cut_by_its_limits_while_designating_is_reported() -> None:
    # Two terms per hop: past 32 open terms a {{term:}} shows its id, which the walk must not do silently.
    n = 20
    defs = "".join(f'"A{i} {{{{term: b{i}}}}}" {{{{def: a{i}}}}}\n\n"B{i} {{{{ref: h{i + 1}}}}}" {{{{def: b{i}}}}}\n\n'
                   for i in range(n))
    heads = "".join(f"# H{i} {{{{term: a{i}}}}} {{#h{i}}}\n\n" for i in range(n)) + f"# H{n} {{#h{n}}}\n\n"
    result = run(heads + defs)
    assert rules(result) == ["render-designation-limit"]
    assert "terms" in next(d.message for d in result.diagnostics if d.rule == "render-designation-limit")
    assert "{{" not in result.output


def test_a_term_inside_deeply_nested_formatting_is_cut_inside_an_open_term_and_reported() -> None:
    n = 12
    defs = "".join(f'"T{i} {_wrap(8, "{{ref: h" + str(i + 1) + "}}")}" {{{{def: t{i}}}}}\n\n' for i in range(n))
    heads = "".join(f"# H{i} {{{{term: t{i}}}}} {{#h{i}}}\n\n" for i in range(n)) + f"# H{n} {{#h{n}}}\n\n"
    assert rules(run(heads + defs)) == ["render-designation-limit"]


@pytest.mark.parametrize("scheme", ["decimal", "none"])
def test_a_term_in_one_blocks_deepest_formatting_is_still_a_term(scheme: str) -> None:
    # The builder allows 100 levels in a block; a term met there is not nested inside another unit.
    body = f'# A\n\n{_wrap(99, "{{term: fee}}")}\n\n"Fee" {{{{def: fee}}}}\n'
    result = render(FRONT + body, format="text", overrides={"numbering.scheme": scheme})
    assert " Fee " in next(line for line in result.output.splitlines() if line.startswith("x "))
    assert "render-term-limit" not in {d.rule for d in result.diagnostics}


# -- Where a diagnostic about a heading, and the new Warnings, are ------------------------------------


def _line(source: str, needle: str) -> int:
    return next(number for number, line in enumerate(source.split("\n"), 1) if needle in line)


def test_a_cycle_is_reported_at_the_heading_it_closes_on() -> None:
    source = FRONT + "\nIntro.\n\n# A {{ref: b}} {#a}\n\nText.\n\n# B {{ref: a}} {#b}\n"
    result = render(source, format="text", overrides=NONE)
    assert [(d.rule, d.line) for d in result.diagnostics if d.rule == "render-designation-cycle"] == [
        ("render-designation-cycle", _line(source, "# A "))]


def test_a_limit_is_reported_at_the_heading_that_reached_it() -> None:
    source = FRONT + "\n" + _chain(40)
    result = render(source, format="text", overrides=NONE)
    assert [d.line for d in result.diagnostics if d.rule == "render-designation-limit"] == [_line(source, "# S31 ")]
    source = FRONT + "\n" + _doubling(40)
    result = render(source, format="text", overrides=NONE)
    lines = [d.line for d in result.diagnostics if d.rule == "render-designation-limit"]
    assert len(lines) == 1 and lines[0] is not None


def test_diagnostics_inside_a_referenced_heading_are_reported_once_at_that_heading() -> None:
    source = (FRONT + "\n# A [x](ms-msdt:1) ![alt](/etc/p.png) {{include: f.lgd}} {#a}\n\n"
              "# B {{ref: a}} {#b}\n\n# C {{ref: b}} {#c}\n\nSee {{ref: c}} and {{ref: a}}.\n")
    result = render(source, format="text", overrides=NONE)
    for rule in ("render-link-refused", "path-not-relative", "render-not-processed"):
        assert [d.line for d in result.diagnostics if d.rule == rule] == [_line(source, "# A ")], rule


def test_a_reference_inside_a_heading_is_a_link_there_and_not_in_the_table_of_contents() -> None:
    result = render(FRONT + "# A {{ref: b}} {#a}\n\n# B {#b}\n", standalone=False,
                    overrides={**NONE, "contents.enabled": True})
    page = result.output
    nav = page[page.index("<nav"):page.index("</nav>")]
    assert '<a class="ld-ref" href="#b">B</a>' not in nav
    assert nav.count("<a ") == 2  # one entry per section, each a single link
    body = page[page.index("</nav>"):]
    assert 'A <a class="ld-ref" href="#b">B</a>' in body


# -- No references in the headings: nothing to work out -----------------------------------------------


def test_nothing_is_worked_out_for_headings_that_read_no_references(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[int] = []
    real = Resolver._designate
    monkeypatch.setattr(Resolver, "_designate", lambda self, sections: (called.append(1), real(self, sections))[1])
    body = '# Fee {{term: fee}} {#a}\n\n"Fee *rate*" {{def: fee}}\n\nSee {{ref: a}}.\n'
    assert designations(body) == ["Fee Fee rate"]
    assert called == []
    run("# A {{ref: b}} {#a}\n\n# B {#b}\n")
    assert called == [1]
    called.clear()
    run('# Fee {{term: fee}} {#a}\n\n"Fee {{ref: a}}" {{def: fee}}\n')  # a reference in a term the heading uses
    assert called == [1]


def test_a_preamble_list_is_numbered_the_same_on_every_pass() -> None:
    body = "- pre {#p}\n\n# A {{ref: b}} {#a}\n\n- one {#i}\n\n# B {#b}\n\nSee {{ref: i}}.\n"
    out = text(body, overrides=NONE)
    assert "See A B (a)." in out


def test_file_reference_findings_are_reported_once_whatever_designates_them(tmp_path: Path) -> None:
    # A frontmatter path, and an image and an include in a heading that other headings refer to.
    source = ("---\ntitle: T\nattachments:\n  - id: annex\n    title: Annex\n    file: /etc/annex.lgd\n---\n\n"
              "# A ![logo](../../logo.png) {{include: ../../part.lgd}} {#a}\n\n# B {{ref: a}} {#b}\n\n"
              "# C {{ref: b}} {#c}\n\nSee {{ref: c}}.\n")
    result = render(source, format="text", overrides=NONE, document_root=tmp_path)
    found = [(d.rule, d.line) for d in result.diagnostics if d.rule.startswith("path-")]
    assert sorted(found) == sorted([("path-not-relative", _line(source, "file: /etc")),
                                    ("path-outside-root", _line(source, "# A ")),
                                    ("path-outside-root", _line(source, "# A "))])
    assert [d.line for d in result.diagnostics if d.rule == "render-not-processed"] == [_line(source, "# A ")]


def test_a_heading_read_from_a_term_it_uses_is_a_circle_and_is_reported() -> None:
    # A is read from the text of t, which A uses, so t is cut short in it: what A reads there
    # depends on where it was asked for, and is not kept.
    body = ('# Intro {{term: t}} {#i}\n\n# A {{term: t}} {#a}\n\nSee {{ref: a}}.\n\n"x {{ref: a}}" {{def: t}}\n')
    source = FRONT + body
    result = render(source, format="text", overrides=NONE)
    assert [d.line for d in result.diagnostics if d.rule == "render-designation-cycle"] == [_line(source, "# A ")]


def test_a_term_with_a_term_in_it_at_the_deepest_formatting_of_a_block() -> None:
    # The levels a block has where its first term opens are its own: only those added inside count.
    for depth in (58, 98):
        for scheme in ("decimal", "none"):
            body = f'# H\n\n{_wrap(depth, "{{term: a}}")}\n\n"A {{{{term: b}}}}" {{{{def: a}}}}\n\n"B" {{{{def: b}}}}\n'
            result = render(FRONT + body, format="text", overrides={"numbering.scheme": scheme})
            assert " A B " in next(line for line in result.output.splitlines() if line.startswith("x ")), (depth, scheme)
            assert "render-term-limit" not in {d.rule for d in result.diagnostics}, (depth, scheme)


def test_a_term_chain_inside_deep_formatting_is_cut_not_a_stack_overflow() -> None:
    n = 40
    defs = "".join(f'"T{i} {_wrap(40, "{{term: t" + str(i + 1) + "}}")}" {{{{def: t{i}}}}}\n\n' for i in range(n))
    body = f'# A\n\n{_wrap(98, "{{term: t0}}")}\n\n{defs}"End" {{{{def: t{n}}}}}\n'
    for scheme in ("decimal", "none"):
        result = render(FRONT + body, format="text", overrides={"numbering.scheme": scheme})
        assert "render-term-limit" in {d.rule for d in result.diagnostics}


def test_a_reference_to_an_item_under_a_long_heading_keeps_the_item_piece() -> None:
    result = run("# " + "word " * 300 + "{#a}\n\n- one {#i}\n\nSee {{ref: i}}.\n")
    shown = next(line for line in result.output.splitlines() if line.startswith("See "))
    assert shown.endswith("\u2026 (a).")
    assert len(shown) < 1010
    message = next(d.message for d in result.diagnostics if d.rule == "render-designation-limit")
    assert message.startswith("A reference to the heading 'word word") and "refer to each other" not in message


def test_a_term_defined_in_the_body_is_not_searched_in_the_validators_text(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[int] = []
    real = Resolver._designate
    monkeypatch.setattr(Resolver, "_designate", lambda self, sections: (called.append(1), real(self, sections))[1])
    run('# Fee {{term: fee}} {#a}\n\n"Fee {{placeholder: p}}" {{def: fee}}\n\nSee {{ref: a}}.\n')
    assert called == []


def _tangle(rng: random.Random) -> str:
    """Headings, items and terms that refer to each other at random, circles included."""
    n = rng.randint(2, 6)
    terms = [f"t{i}" for i in range(rng.randint(1, 3))]
    ids = [f"h{i}" for i in range(n)] + [f"h{i}i" for i in range(n)]
    out = []
    for i in range(n):
        parts = [f"W{i}"]
        for _ in range(rng.randint(0, 2)):
            parts.append("{{ref: " + rng.choice(ids) + "}}" if rng.random() < 0.6
                         else "{{term: " + rng.choice(terms) + "}}")
        out.append(f"# {' '.join(parts)} {{#h{i}}}\n\n- item {{#h{i}i}}\n")
    for term in terms:
        parts = [term.upper()]
        if rng.random() < 0.7:
            parts.append("{{ref: " + rng.choice(ids) + "}}")
        if rng.random() < 0.3:
            parts.append("{{term: " + rng.choice(terms) + "}}")
        out.append(f'"{" ".join(parts)}" {{{{def: {term}}}}}\n')
    return "\n".join(out)


def test_a_heading_that_reads_differently_from_its_designation_is_always_reported() -> None:
    rng = random.Random(329)
    reported = {"render-designation-cycle", "render-designation-limit", "render-term-limit"}
    for _ in range(250):
        source = FRONT + _tangle(rng)
        result = render(source, format="text", overrides=NONE)
        if any(" ".join(plain_text(s.title).split()) != s.designation for s in result.tree.sections):
            assert reported & {d.rule for d in result.diagnostics}, source
