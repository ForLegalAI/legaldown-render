"""LibreOffice as the oracle of the numbering and the cross-references (docs/architecture.md, "The DOCX planner").

Every document of the test corpus (the test documents, the specification's examples and valid fixtures, and
documents written to stress the numbering) is rendered to DOCX under the default, outline, continental and mixed
schemes and under ``none`` (where a reference is a field for the text of a heading), then changed so that the file cannot be right by accident:

* every cached result of a field is replaced by ``XXX``, so that what LibreOffice shows after it has updated the
  fields is what it computed from the numbering definitions and the bookmarks, not what the writer cached;
* a marker ``⟦u3⟧`` leads the paragraph of every numbered unit, so that the number LibreOffice drew before it can be
  read from the exported text, and a paragraph per ``{{ref:}}`` target is added, made of exactly the fields the plan
  gives that reference, preceded by ``⟦ref u3⟧``.

The exported text is then compared with what the renderer wrote (the labels and designations that the text writer
prints), **modulo the gaps the plan documents**, each of which the plan reports with a diagnostic:

* a unit whose number Word draws differently from the renderer's (a letter counter past 26, a label that needs the
  section's number, a list deeper than Word's nine levels, alternatives, which the next stage of the planner
  places): LibreOffice must agree with the plan (the simulator), and the plan must say so;
* ``\\t`` is ignored by LibreOffice (it is Word's switch), so a reference built with it reads there as
  ``FieldSeg.cached_lo`` says;
* LibreOffice fills a skipped heading level with its start value, which is what the simulator models for headings.

Skipped when LibreOffice or its Python bindings are missing, a failure when ``LEGALDOWN_REQUIRE_DOCX_TOOLS`` is set.
"""
from __future__ import annotations

import functools
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import pytest
from conftest import DOCUMENTS, FRONT, spec_root
from docx_support import Exported, W, libreoffice_export, parts, repackage, require_libreoffice
from test_docx_plan import ALTERNATIVES, units

from legaldown_render import DocumentError, render
from legaldown_render.resolve.docx_plan import DocxPlan, FieldSeg, Literal_, plan_docx
from legaldown_render.tree import CrossRef, ListItem, Paragraph, RenderTree, Section, iter_tree_inlines
from legaldown_render.writers.docx.ooxml import Bookmarks, p, text_run
from legaldown_render.writers.docx.package import Relationships
from legaldown_render.writers.docx.runs import InlineWriter

pytestmark = pytest.mark.libreoffice

VARIANTS: dict[str, dict] = {
    "default": {},
    "outline": {"style": "outline"},
    "continental": {"style": "continental"},
    "mixed": {"overrides": {"numbering.scheme": "mixed"}},
    "none": {"overrides": {"numbering.scheme": "none"}},
}

#: Documents that stress the numbering and the references, written for this test.
STRESS: dict[str, str] = {
    "nested": FRONT + """
# Alpha {#alpha}

Intro paragraph one. {#p1}

Intro paragraph two. {#p2}

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

Refs: {{ref: alpha}} {{ref: p1}} {{ref: i1}} {{ref: n2}} {{ref: d1}} {{ref: beta}} {{ref: gamma}} {{ref: o2}}
{{ref: b1}} {{ref: delta}} {{ref: eps}} {{ref: ei}} {{ref: second}} {{ref: sa}} {{ref: i3}} {{ref: p2}}
""",
    "lists": FRONT + """
# A {#a}

- x {#x}
  - n1 {#n1}
  - n2 {#n2}

  Text between.

  - n3 {#n3}
  1. o1 {#o1}
  1. o2 {#o2}
  > - quoted {#q1}
  > - quoted two
- y {#y}
  1. oy {#oy}
  - ny {#ny}

Para.

- z {#z}

Ref {{ref: n3}} {{ref: o2}} {{ref: oy}} {{ref: ny}} {{ref: z}} {{ref: q1}}
""",
    "preamble": FRONT + """- pre one {#p1}
- pre two {#p2}
  - pre nested {#pn}

# A {#a}

- in {#in1}

Ref {{ref: p1}} {{ref: pn}} {{ref: in1}}
""",
    "letters": FRONT + "# A {#a}\n\n" + "\n".join(f"- item {i} {{#m{i}}}" for i in range(1, 32)) + (
        "\n\nRef {{ref: m1}} {{ref: m26}} {{ref: m27}} {{ref: m28}} {{ref: m31}}\n"),
    "level five": FRONT + """
# A

## B

### C

#### D

##### E {#e}

- five item {#fi}
- five item two {#fj}

Para {#pp}

Ref {{ref: fi}} {{ref: fj}} {{ref: e}} {{ref: pp}}
""",
    "skipped": FRONT + """
# One {#one}

### Deep {#deep}

## Two {#two}

#### Deeper {#deeper}

Ref: {{ref: deep}} {{ref: two}} {{ref: deeper}}
""",
    "helpers": FRONT + """
# A {#a}

- unanchored
  - also unanchored
    - anchored {#deep}

- unanchored ordered
  1. unanchored nested
  1. anchored too {#too}

Ref {{ref: deep}} {{ref: too}}
""",
    "alternatives": ALTERNATIVES + """
# Intro {#intro}

# Disputes {#d when=forum:courts}

## Sub one {#s1}

- courts item {#ci when=forum:courts}
- arbitration item {#ai when=forum:arb}
  - nested under alt {#an}
- common item {#cm}

# Disputes {#d when=forum:arb}

## Sub alt {#sa}

# Last {#last}

Ref {{ref: d}} {{ref: s1}} {{ref: sa}} {{ref: ci}} {{ref: an}} {{ref: cm}} {{ref: last}}
""",
}

#: The settings that give the numbering words and symbols, which Word's ``\t`` switch drops from a reference.
WORDS: dict[str, dict] = {
    "article": {"overrides": {"numbering.levels": [
        {"counter": "upper-roman", "label": "Article {n}", "ref": "{n}"}], "references.format": "Article {designation}"}},
    "section-sign": {"overrides": {"numbering.levels": [
        {"counter": "decimal", "label": "§ {n}", "ref": "{n}"}], "references.format": "§ {designation}"}},
    "paragraphs": {"overrides": {"paragraphs.numbered": True}},
}

#: What a diagnostic of the plan has to say when Word numbers a unit differently from the renderer.
EXPLAINING = {"render-docx-number-mismatch", "render-docx-section-label", "render-docx-letter-overflow",
              "render-docx-roman-overflow", "render-docx-list-depth", "render-docx-shared-counter"}

WRONG = "XXX"
OPEN, CLOSE = "⟦", "⟧"
MARKED = re.compile(rf"^(?P<number>.*?){OPEN}(?P<tag>[^{CLOSE}]+){CLOSE}(?P<rest>.*)$")


# -- Making the files ---------------------------------------------------------------------------------------------


def _is_field_part(run: ET.Element) -> str | None:
    """``begin``, ``separate`` or ``end`` for a run that holds that field character, else None."""
    mark = run.find(f"{W}fldChar")
    return None if mark is None else mark.get(f"{W}fldCharType")


def seed_wrong_results(root: ET.Element, wrong: str = WRONG) -> int:
    """Replace the cached result of every complex field under *root* by *wrong*; returns how many there are."""
    count = 0
    for paragraph in root.iter(f"{W}p"):
        state = None
        first: ET.Element | None = None
        for run in list(paragraph.iter(f"{W}r")):
            part = _is_field_part(run)
            if part == "begin":
                state, first = "instruction", None
                count += 1
            elif part == "separate":
                state = "result"
            elif part == "end":
                if state == "result" and first is None:
                    spare = ET.Element(f"{W}r")
                    ET.SubElement(spare, f"{W}t").text = wrong
                    run_list = list(paragraph)
                    paragraph.insert(run_list.index(run), spare)
                state = None
            elif state == "result":
                text = run.find(f"{W}t")
                if text is None:
                    continue
                if first is None:
                    text.text, first = wrong, run
                else:
                    paragraph.remove(run)
    return count


def marked_paragraphs(root: ET.Element, tree: RenderTree, plan: DocxPlan) -> list[Section | ListItem | Paragraph]:
    """The units whose paragraph carries a number: every heading and every unit the plan says is explicit, in the
    order of the document, which is the order of the paragraphs of the body that carry a heading style or ``w:numPr``."""
    expected = [unit for unit in units(tree) if isinstance(unit, Section) or plan.units[unit.unit].explicit]
    body = root.find(f"{W}body")
    assert body is not None
    found = []
    for paragraph in body.findall(f"{W}p"):
        style = paragraph.find(f"{W}pPr/{W}pStyle")
        heading = style is not None and re.fullmatch(r"Heading[1-5]", style.get(f"{W}val", "")) is not None
        if heading or paragraph.find(f"{W}pPr/{W}numPr") is not None:
            found.append(paragraph)
    assert len(found) == len(expected), (len(found), len(expected))
    for paragraph, unit in zip(found, expected, strict=True):
        marker = ET.Element(f"{W}r")
        ET.SubElement(marker, f"{W}t").text = f"{OPEN}{unit.unit}{CLOSE}"
        position = 1 if paragraph.find(f"{W}pPr") is not None else 0
        paragraph.insert(position, marker)
    return expected


def reference_lines(root: ET.Element, plan: DocxPlan) -> None:
    """Add a paragraph for every reference target of the plan, made of the fields of that reference."""
    body = root.find(f"{W}body")
    assert body is not None
    writer = InlineWriter(plan, Relationships(), Bookmarks(), None, {}, "0563C1")
    end = len(body) - 1  # before the section properties
    for offset, (target, reference) in enumerate(plan.refs.items()):
        line = p(text_run(f"{OPEN}ref {target}{CLOSE}"), writer.segments(reference.segments))
        body.insert(end + offset, line)


def prepared(data: bytes, tree: RenderTree, plan: DocxPlan) -> bytes:
    """*data* with the results of its fields made wrong, its unit paragraphs marked and a line per reference."""
    def change(document: str) -> str:
        ET.register_namespace("w", W.strip("{}"))
        ET.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")
        root = ET.fromstring(document.encode("utf-8"))
        seed_wrong_results(root)
        marked_paragraphs(root, tree, plan)
        reference_lines(root, plan)
        return ET.tostring(root, encoding="unicode", xml_declaration=False)

    return repackage(data, "word/document.xml", change)


@dataclass
class Case:
    name: str
    tree: RenderTree
    plan: DocxPlan
    rules: set[str]
    data: bytes
    shown: Exported | None = None


def corpus() -> dict[str, str]:
    """The documents: tests/documents, the specification's examples and valid fixtures, and the stress documents."""
    found = {f"documents/{path.name}": path.read_text(encoding="utf-8") for path in sorted(DOCUMENTS.glob("*.lgd"))}
    spec = spec_root()
    if spec is not None:
        for path in sorted((spec / "examples").rglob("*.lgd")) + sorted((spec / "fixtures" / "valid").rglob("*.lgd")):
            found[str(path.relative_to(spec))] = path.read_text(encoding="utf-8")
    found.update({f"stress/{name}": source for name, source in STRESS.items()})
    return found


@functools.cache
def round_trip(variant: str, update: bool = True) -> tuple[Case, ...]:
    """Every document under *variant*, rendered, made wrong and marked, and shown by LibreOffice."""
    require_libreoffice()
    settings = {**VARIANTS, **WORDS}[variant]
    cases: dict[str, Case] = {}
    for name, source in corpus().items():
        if variant in WORDS and not name.startswith("stress/"):
            continue
        try:
            result = render(source, format="docx", **settings)
        except DocumentError:
            continue
        assert result.binary is not None
        plan, diagnostics = plan_docx(result.tree)
        cases[f"{variant}/{name}"] = Case(name, result.tree, plan, {d.rule for d in diagnostics},
                                          prepared(result.binary, result.tree, plan))
    key = {f"d{index}": case for index, case in enumerate(cases.values())}
    shown = libreoffice_export({name: case.data for name, case in key.items()}, update=update)
    for name, case in key.items():
        case.shown = shown[name]
    return tuple(cases.values())


# -- Reading what LibreOffice says ----------------------------------------------------------------------------------


def marked_lines(shown: Exported) -> dict[str, tuple[str, str]]:
    """Tag to (the number LibreOffice drew before the marker, the text after it), for every marked line."""
    found: dict[str, tuple[str, str]] = {}
    for line in shown.text.lstrip("﻿").splitlines():
        match = MARKED.match(line)
        if match:
            found[match["tag"]] = (match["number"].strip(), match["rest"].strip())
    return found


def lo_reference(segments: tuple) -> str:
    """What LibreOffice shows for *segments*: the cached results, but for a field written with ``\\t`` (which it
    ignores) the result of the same field without it."""
    return "".join(s.text if isinstance(s, Literal_) else s.cached_lo for s in segments)


def renderer_references(tree: RenderTree) -> dict[str, str]:
    return {i.target_unit: i.text for i in iter_tree_inlines(tree) if isinstance(i, CrossRef) and i.target_unit}


def check(case: Case) -> None:
    """The property: every number LibreOffice draws is the renderer's, or the plan's where the plan says Word differs
    (with a diagnostic), and every reference reads as the renderer wrote it, or as the plan says."""
    assert case.shown is not None
    lines = marked_lines(case.shown)
    excused = False
    for node in units(case.tree):
        mine = case.plan.units[node.unit]
        if not (isinstance(node, Section) or mine.explicit):
            continue
        number, _ = lines[node.unit]
        label = node.label or ""
        if mine.num_id is None:
            assert number == "", (node.unit, number)
        elif mine.count is None:  # a bullet: LibreOffice draws its own symbol
            assert number != "", node.unit
        else:
            assert number == mine.label, (node.unit, "LibreOffice", number, "plan", mine.label)
            if mine.label != label:
                excused = True
            else:
                assert number == label, (node.unit, number, label)
    if excused:
        assert case.rules & EXPLAINING, "the plan says Word numbers a unit differently but reports nothing"
    shown_by_renderer = renderer_references(case.tree)
    for target, reference in case.plan.refs.items():
        _, text = lines[f"ref {target}"]
        assert text == lo_reference(reference.segments), (target, "LibreOffice", text, "plan", reference.segments)
        suppressing = any(isinstance(s, FieldSeg) and s.suppress_text for s in reference.segments)
        if reference.exact and not suppressing:
            assert text == shown_by_renderer[target], (target, text, shown_by_renderer[target])


@pytest.mark.parametrize("variant", list(VARIANTS) + list(WORDS))
def test_libreoffice_draws_the_numbers_and_reads_the_references_as_the_renderer_wrote_them(variant: str) -> None:
    cases = round_trip(variant)
    assert cases
    for case in cases:
        try:
            check(case)
        except AssertionError as error:
            raise AssertionError(f"{variant}/{case.name}: {error}") from error


def test_the_round_trip_reaches_numbers_references_and_every_gap() -> None:
    """The corpus is not vacuous: it numbers headings, items and paragraphs, it has references of every kind, and it
    reaches each documented gap (``\\t``, an overflowing letter counter, a label that needs the section)."""
    seen_rules: set[str] = set()
    references = suppressing = numbered = hidden = 0
    for variant in list(VARIANTS) + list(WORDS):
        for case in round_trip(variant):
            seen_rules |= case.rules
            references += len(case.plan.refs)
            numbered += sum(1 for unit in case.plan.units.values() if unit.count is not None)
            suppressing += sum(any(isinstance(s, FieldSeg) and s.suppress_text for s in r.segments)
                               for r in case.plan.refs.values())
            hidden += sum(isinstance(s, FieldSeg) and s.bookmark.startswith("_Ld")
                          for r in case.plan.refs.values() for s in r.segments)
    assert numbered > 200 and references > 100 and suppressing > 0 and hidden > 0
    assert {"render-docx-letter-overflow", "render-docx-section-label"} <= seen_rules


def test_the_cached_results_are_wrong_in_the_file_and_libreoffice_does_not_read_them() -> None:
    """The seed works: the file holds ``XXX`` as the result of every reference field, and LibreOffice shows the right
    results all the same, even before the fields are updated, because it computes them from the numbering and the
    bookmarks when it opens the file (so that what the comparison above checks is its computation, not the cache)."""
    for update in (True, False):
        document = next(case for case in round_trip("default", update=update) if case.name == "stress/nested")
        written = parts(document.data)["word/document.xml"].decode("utf-8")
        assert written.count(f">{WRONG}<") >= len(document.plan.refs) > 10
        lines = marked_lines(document.shown)  # type: ignore[arg-type]
        assert not any(WRONG in text for _, text in lines.values())
        check(document)
