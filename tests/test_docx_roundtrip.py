"""LibreOffice as the oracle of the numbering and the cross-references (docs/architecture.md, "The DOCX planner").

Every document of the test corpus (the test documents, the specification's examples and valid fixtures, and
documents written to stress the numbering) is rendered to DOCX under the default, outline, continental and mixed
schemes and under ``none`` (where a reference is a field for the text of a heading; the specification's documents
take turns, each under half of them), then changed so that the file cannot be right by accident:

* every cached result of a field is replaced by ``XXX``, so that what LibreOffice shows after it has updated the
  fields is what it computed from the numbering definitions and the bookmarks, not what the writer cached;
* a marker ``⟦u3⟧`` leads the paragraph of every numbered unit, so that the number LibreOffice drew before it can be
  read from the exported text, and a paragraph per ``{{ref:}}`` target is added, made of exactly the fields the plan
  gives that reference, preceded by ``⟦ref u3⟧``.

Every body paragraph's text must also be what the file cached for it, and a second round trip makes the edits a
lawyer makes (an item, a heading and an ordered item inserted before the targets, in LibreOffice) and updates the
fields: every reference and number must read as the renderer writes them for the edited source. The table of
contents is not written yet, so its entries and the ``_Toc`` bookmarks are not exercised.

The exported text is then compared with what the renderer wrote (the labels and designations that the text writer
prints), **modulo the gaps the plan documents**, each of which the plan reports with a diagnostic:

* a unit whose number Word draws differently from the renderer's (a letter counter past 26, a label that needs the
  section's number where the heading list cannot show it, a list deeper than Word's nine levels): LibreOffice must
  agree with the plan (the simulator), and the plan must say so; a later alternative that shares a number has no number
  of its own (``numId`` 0) and shows the holder's as a field, which LibreOffice updates to the renderer's;
* ``\\t`` is ignored by LibreOffice (it is Word's switch), so a reference built with it reads there as
  ``FieldSeg.cached_lo`` says;
* LibreOffice fills a skipped heading level with its start value, which is what the simulator models for headings.

Skipped when LibreOffice or its Python bindings are missing, a failure when ``LEGALDOWN_REQUIRE_DOCX_TOOLS`` is set.
"""
from __future__ import annotations

import difflib
import functools
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace

import pytest
from conftest import DOCUMENTS, FRONT, spec_root
from docx_support import Exported, W, libreoffice_edit, libreoffice_export, parts, repackage, require_libreoffice
from test_docx_plan import ALTERNATIVES, units

from legaldown_render import DocumentError, render
from legaldown_render.resolve.docx_plan import DocxPlan, FieldSeg, Literal_, plan_docx
from legaldown_render.tree import CrossRef, ListItem, Paragraph, RenderTree, Section, iter_tree_inlines, plain_text
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
    "cycles": FRONT + """
# A {{ref: b}} {#a}

# B {{ref: a}} {#b}

# C {{ref: c}} {#c}

# D {{ref: e}} {#d}

# E plain {#e}

- item in {{ref: it}} {#it}

See {{ref: a}} {{ref: b}} {{ref: c}} {{ref: d}} {{ref: it}}.
""",
    "alternatives": ALTERNATIVES + """
# Intro {#intro}

# Disputes {#d when=forum:courts}

## Sub one {#s1}

- courts item {#ci when=forum:courts}
- arbitration item {#ci when=forum:arb}
  - nested under alt {#an}
- common item {#cm}

# Disputes {#d when=forum:arb}

## Sub alt {#sa}

# Last {#last}

Ref {{ref: d}} {{ref: s1}} {{ref: sa}} {{ref: ci}} {{ref: an}} {{ref: cm}} {{ref: last}}
""",
    "alternatives in threes": ALTERNATIVES + """
# Intro {#intro}

Para one. {#pc when=forum:courts}

Para arbitration. {#pc when=forum:arb}

Para expert. {#pc when=forum:expert}

Para next. {#pn}

# Disputes {#d when=forum:courts}

## Sub one {#s1}

## Sub two {#s2}

# Disputes {#d when=forum:arb}

## Sub alt {#sa}

- quoted item {#q}

# Disputes {#d when=forum:expert}

## Expert one {#e1}

## Expert two {#e2}

## Expert three {#e3}

# Items {#items}

- courts {#ci when=forum:courts}
  - courts child {#cc}
- arbitration {#ci when=forum:arb}
  - arbitration child one {#ac1}
  - arbitration child two {#ac2}
- expert {#ci when=forum:expert}
- common {#cm}
  - common child {#cmc}

# Last {#last}

## Last one {#l1}

Ref {{ref: d}} {{ref: s1}} {{ref: s2}} {{ref: sa}} {{ref: q}} {{ref: e1}} {{ref: e3}} {{ref: ci}} {{ref: cc}}
{{ref: ac1}} {{ref: ac2}} {{ref: cm}} {{ref: cmc}} {{ref: pc}} {{ref: pn}} {{ref: last}} {{ref: l1}}
""",
    "nested titles": FRONT + """
# A {{ref: i}} {#a}

- x {#i}

# B {{ref: a}} {#b}

# C {{ref: b}} and {{ref: a}} {#c}

Refs {{ref: a}} {{ref: b}} {{ref: c}} {{ref: i}}
""",
    "qualified": FRONT + """
# Alpha {#alpha}

Intro. {#p1}

- first {#i1}
  - nested one {#n1}
  - nested two {#n2}
    - deep {#d1}
      - deeper {#d2}
        - deepest {#d3}
- second {#i2}
  1. ordered {#o1}
  1. ordered two {#o2}

Between.

- third {#i3}

## Beta {#beta}

- beta item {#bi}
  - beta nested {#bn}

### Gamma {#gamma}

#### Delta {#delta}

##### Epsilon {#eps}

- five item {#ei}
  - five nested {#en}
- five two {#ej}

# Second {#second}

- x {#sx}
  - y {#sy}

Ref {{ref: i1}} {{ref: n2}} {{ref: d3}} {{ref: o2}} {{ref: i3}} {{ref: beta}} {{ref: bn}} {{ref: ei}} {{ref: en}}
{{ref: ej}} {{ref: sy}} {{ref: p1}}
""",
}

#: The stress documents that are shown under some variants only (each costs LibreOffice time): the continental style, where
#: the items ride the heading list, the default, and the default with numbered paragraphs.
FEW_VARIANTS: dict[str, tuple[str, ...]] = {
    name: ("continental", "default", "paragraphs") for name in ("alternatives in threes", "nested titles", "qualified")}

#: The variants that show some stress documents only.
ONLY_DOCS: dict[str, tuple[str, ...]] = {"five levels": ("qualified",), "shared level": ("qualified", "nested")}

#: The settings that give the numbering words and symbols: a field that returns the same words as the reference's format
#: stands for them, otherwise Word's ``\t`` switch drops them from the number.
WORDS: dict[str, dict] = {
    "article": {"overrides": {"numbering.levels": [
        {"counter": "upper-roman", "label": "Article {n}", "ref": "{n}"}], "references.format": "Article {designation}"}},
    "section-sign": {"overrides": {"numbering.levels": [
        {"counter": "decimal", "label": "§ {n}", "ref": "{n}"}], "references.format": "§ {designation}"}},
    # the format says a word the label does not: Word's \t leaves the label's out and the format's is put back
    "article-short": {"overrides": {"numbering.levels": [
        {"counter": "upper-roman", "label": "Article {n}", "ref": "{n}"}], "references.format": "Art. {designation}"}},
    "paragraphs": {"overrides": {"paragraphs.numbered": True}},
    # the items and the paragraphs of a section share the heading list's level: the numbers are restarted where the
    # resolver counts them apart
    # six levels of enumeration with no section in them but the first: a chain of four lists nested in a clause rides all
    # four reserved levels of the heading list
    "five levels": {"style": "continental", "overrides": {"enumeration.levels": [
        {"counter": "decimal", "label": "{section}.{n}", "ref": ".{n}"},
        {"counter": "lower-alpha", "label": "{n})", "ref": "({n})"},
        {"counter": "lower-roman", "label": "{n})", "ref": "({n})"},
        {"counter": "upper-alpha", "label": "({n})", "ref": "({n})"},
        {"counter": "decimal", "label": "[{n}]", "ref": "[{n}]"},
        {"counter": "lower-alpha", "label": "{n}.", "ref": "{n}"}]}},
    "shared level": {"style": "continental", "overrides": {"paragraphs.numbered": True, "paragraphs.format": {
        "counter": "decimal", "label": "{section}.{n}", "ref": ".{n}"}}},
}

#: What a diagnostic of the plan has to say when Word numbers a unit differently from the renderer.
EXPLAINING = {"render-docx-section-label", "render-docx-letter-overflow", "render-docx-roman-overflow",
              "render-docx-list-depth"}

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
    variant: str
    name: str
    tree: RenderTree
    plan: DocxPlan
    rules: set[str]
    original: bytes
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


def make_case(variant: str, name: str, source: str) -> Case | None:
    try:
        result = render(source, format="docx", **{**VARIANTS, **WORDS}[variant])
    except DocumentError:
        return None
    assert result.binary is not None
    plan, diagnostics = plan_docx(result.tree)
    return Case(variant, name, result.tree, plan, {d.rule for d in diagnostics}, result.binary,
                prepared(result.binary, result.tree, plan))


@functools.cache
def round_trips() -> dict[str, tuple[Case, ...]]:
    """Every document under every variant, rendered, made wrong and marked, and shown by LibreOffice: one LibreOffice
    instance for all of them."""
    require_libreoffice()
    cases: dict[str, list[Case]] = {variant: [] for variant in (*VARIANTS, *WORDS)}
    for offset, variant in enumerate(cases):
        for index, (name, source) in enumerate(corpus().items()):
            if variant in WORDS and not name.startswith("stress/"):
                continue
            if variant in ONLY_DOCS and name.removeprefix("stress/") not in ONLY_DOCS[variant]:
                continue
            if (variant not in ONLY_DOCS and name.removeprefix("stress/") in FEW_VARIANTS
                    and variant not in FEW_VARIANTS[name.removeprefix("stress/")]):
                continue
            if name.startswith(("examples/", "fixtures/")) and (index + offset) % 2:
                continue  # the specification's documents take turns under the schemes: each is shown under half
            case = make_case(variant, name, source)
            if case is not None:
                cases[variant].append(case)
    everything = {f"d{index}": case for index, case in enumerate(case for group in cases.values() for case in group)}
    shown = libreoffice_export({key: case.data for key, case in everything.items()})
    for key, case in everything.items():
        case.shown = shown[key]
    return {variant: tuple(group) for variant, group in cases.items()}


def round_trip(variant: str) -> tuple[Case, ...]:
    return round_trips()[variant]


# -- Reading what LibreOffice says ----------------------------------------------------------------------------------


def marked_lines(shown: Exported) -> dict[str, tuple[str, str]]:
    """Tag to (the number LibreOffice drew before the marker, the text after it), for every marked line."""
    found: dict[str, tuple[str, str]] = {}
    for line in shown.text.lstrip("\ufeff").splitlines():
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


def squeeze(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def cached_lines(data: bytes, plan: DocxPlan) -> list[str]:
    """What the paragraphs of the file say, as the writer cached it, one entry per line without the blank ones. A field
    written with ``\\t`` is what LibreOffice shows for it (it ignores the switch), by ``FieldSeg.cached_lo``."""
    lo = {(s.bookmark, s.suppress_text): s.cached_lo for r in plan.refs.values() for s in r.segments
          if isinstance(s, FieldSeg)}
    root = ET.fromstring(parts(data)["word/document.xml"])
    lines: list[str] = []
    for paragraph in root.iter(f"{W}p"):
        text, state, instruction, replacement = "", None, "", None
        for run in paragraph.iter(f"{W}r"):
            mark = _is_field_part(run)
            if mark == "begin":
                state, instruction, replacement = "instruction", "", None
            elif mark == "separate":
                state = "result"
                switches = instruction.split()
                if "\\t" in switches:
                    replacement = lo.get((switches[1], True))
            elif mark == "end":
                text += replacement or ""
                state = None
            elif state == "instruction":
                instruction += "".join(node.text or "" for node in run.findall(f"{W}instrText"))
            elif state == "result" and replacement is not None:
                continue
            else:
                for node in run:
                    text += (node.text or "") if node.tag == f"{W}t" else "\t" if node.tag == f"{W}tab" else (
                        "\n" if node.tag == f"{W}br" else "")
        lines += [squeeze(line) for line in text.split("\n") if squeeze(line)]
    return lines


def shown_lines(shown: Exported) -> list[str]:
    """What LibreOffice says, line by line, without the blank ones, the marker lines of the references and the
    markers and numbers of the numbered paragraphs."""
    lines = []
    for line in shown.text.lstrip("\ufeff").splitlines():
        match = MARKED.match(line)
        if match and match["tag"].startswith("ref "):
            continue
        text = squeeze(match["rest"] if match else line)
        if text:
            lines.append(text)
    return lines


def check(case: Case) -> None:
    """The property: every number LibreOffice draws is the renderer's, or the plan's where the plan says Word differs
    (with a diagnostic), every reference reads as the renderer wrote it, or as the plan says, and every body paragraph
    says what the file cached for it. The table of contents is not written yet, so its entries and the ``_Toc``
    bookmarks are not exercised."""
    assert case.shown is not None
    lines = marked_lines(case.shown)
    excused = False
    for node in units(case.tree):
        mine = case.plan.units[node.unit]
        if not (isinstance(node, Section) or mine.explicit):
            continue
        number, text = lines[node.unit]
        label = node.label or ""
        if mine.num_id is None:
            assert number == "", (node.unit, number)
        elif mine.num_id == 0:  # a later alternative: no number of its own, the holder's as fields, which LibreOffice
            # updates to what the renderer wrote
            assert number == "" and text.startswith(label), (node.unit, "LibreOffice", number, text, "renderer", label)
            assert text.startswith(mine.label), (node.unit, "LibreOffice", text, "plan", mine.label)
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
    cached, said = cached_lines(case.original, case.plan), shown_lines(case.shown)
    assert said == cached, "\n".join(difflib.unified_diff(cached, said, "cached", "LibreOffice", lineterm="", n=1))


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
    seen_causes: set[str] = set()
    references = suppressing = numbered = hidden = 0
    for variant in list(VARIANTS) + list(WORDS):
        for case in round_trip(variant):
            seen_rules |= case.rules
            seen_causes |= {cause for unit in case.plan.units.values() for cause in unit.cause}
            references += len(case.plan.refs)
            numbered += sum(1 for unit in case.plan.units.values() if unit.count is not None)
            suppressing += sum(any(isinstance(s, FieldSeg) and s.suppress_text for s in r.segments)
                               for r in case.plan.refs.values())
            hidden += sum(isinstance(s, FieldSeg) and s.bookmark.startswith("_Ld")
                          for r in case.plan.refs.values() for s in r.segments)
    assert numbered > 200 and references > 100 and suppressing > 0 and hidden > 0
    assert {"render-docx-letter-overflow", "render-docx-section-label", "render-docx-shared-counter"} <= seen_rules
    assert "render-docx-number-mismatch" not in seen_rules
    assert {"alternative", "reserved-level", "resync", "restart-alternative", "restart-section"} <= seen_causes


def test_the_cached_results_are_wrong_in_the_file_and_libreoffice_does_not_read_them() -> None:
    """The seed works: the file holds ``XXX`` as the result of every reference field, and LibreOffice shows the right
    results all the same, even before the fields are updated, because it computes them from the numbering and the
    bookmarks when it opens the file (so that what the comparison above checks is its computation, not the cache)."""
    document = next(case for case in round_trip("default") if case.name == "stress/nested")
    written = parts(document.data)["word/document.xml"].decode("utf-8")
    assert written.count(f">{WRONG}<") >= len(document.plan.refs) > 10
    unupdated = libreoffice_export({"nested": document.data}, update=False)["nested"]
    for shown in (document.shown, unupdated):
        lines = marked_lines(shown)  # type: ignore[arg-type]
        assert not any(WRONG in text for _, text in lines.values())
        check(replace(document, shown=shown))


# -- Edits in LibreOffice, then the fields updated -------------------------------------------------------------------


EDITED = FRONT + """
# Alpha {#alpha}

Para {#p1}

- first {#i1}
- second item {#i2}
  - nested one {#n1}
  - nested two {#n2}
- third {#i3}

## Beta {#beta}

1. ord one {#o1}
2. ord two {#o2}

# Gamma {#gamma}

- g item {#gi}

Refs: {{ref: alpha}} {{ref: p1}} {{ref: i1}} {{ref: i2}} {{ref: n1}} {{ref: n2}} {{ref: i3}} {{ref: beta}} {{ref: o2}}
{{ref: gamma}} {{ref: gi}}
"""

#: What a lawyer does in Word: press Enter at the start of a paragraph and type (an item, a heading, an ordered item).
#: Each is (the text of the paragraph it goes before, what is typed, the source line it goes before, the new line).
INSERTIONS = [
    ("second item", "NEW item", "- second item {#i2}", "- NEW item"),
    ("Gamma", "NEWH", "# Gamma {#gamma}", "# NEWH\n"),
    ("ord two", "NEW ord", "2. ord two {#o2}", "1. NEW ord"),
]

EDIT_VARIANTS = {name: VARIANTS[name] for name in ("default", "outline", "continental", "none")}


def after_the_edits() -> str:
    source = EDITED
    for _, _, before, new in INSERTIONS:
        source = source.replace(before, f"{new}\n{before}")
    return source


@functools.cache
def edited() -> dict[str, tuple[str, str]]:
    """Variant to (what LibreOffice says after the insertions and the update, what the text writer prints for the
    edited source)."""
    require_libreoffice()
    jobs = {}
    for variant, settings in EDIT_VARIANTS.items():
        jobs[variant] = (render(EDITED, format="docx", **settings).binary, [(m, n) for m, n, _, _ in INSERTIONS])
    said = libreoffice_edit(jobs)  # type: ignore[arg-type]
    return {variant: (said[variant], render(after_the_edits(), format="text", **EDIT_VARIANTS[variant]).output)
            for variant in EDIT_VARIANTS}


@pytest.mark.parametrize("variant", EDIT_VARIANTS)
def test_the_references_follow_the_insertions_a_lawyer_makes(variant: str) -> None:
    """The point of the whole design: after items and headings are inserted before the targets and the fields are
    updated, every reference reads as the renderer writes it for the edited document (a reference that was text
    would still say what it said before)."""
    said, written = edited()[variant]
    refs = [squeeze(line) for line in said.splitlines() if "Refs:" in line]
    expected = [squeeze(line) for line in written.splitlines() if "Refs:" in line]
    assert refs == expected and refs
    before = render(EDITED, format="text", **EDIT_VARIANTS[variant]).output
    assert [squeeze(line) for line in before.splitlines() if "Refs:" in line] != expected  # the edits moved them


@pytest.mark.parametrize("variant", ["default", "outline", "continental", "none"])
def test_the_numbers_follow_the_insertions_a_lawyer_makes(variant: str) -> None:
    said, written = edited()[variant]
    assert [squeeze(line) for line in said.lstrip("\ufeff").splitlines() if squeeze(line)] == [
        squeeze(line) for line in written.splitlines() if squeeze(line)]


# -- Clauses and nested items inserted under the continental style --------------------------------------------------


CLAUSES = FRONT + """
# Alpha {#alpha}

- clause one {#c1}
  - nested of one {#n1}
- clause two {#c2}
  - nested of two {#n2}
    - deep of two {#d2}

Refs: {{ref: c1}} {{ref: n1}} {{ref: c2}} {{ref: n2}} {{ref: d2}}
"""

#: A clause and a nested item inserted before the second clause and its nested item.
CLAUSE_EDITS = [
    ("clause two", "NEW clause", "- clause two {#c2}", "- NEW clause"),
    ("nested of two", "NEW nested", "  - nested of two {#n2}", "  - NEW nested"),
]

#: A heading section whose sub-headings follow numbered clauses: Word's counter of the level is shared with the clauses,
#: so the first sub-heading is restarted at the number the renderer gives it (a fixed value).
RESYNCED = FRONT + """
# Alpha {#alpha}

- clause one {#c1}
- spare clause
- clause two {#c2}

## Beta {#beta}

## Gamma {#gamma}

Refs: {{ref: c1}} {{ref: c2}} {{ref: beta}} {{ref: gamma}}
"""


def applied(source: str, edits: list[tuple[str, str]]) -> str:
    """*source* with each new line put before the line it goes before."""
    for before, new in edits:
        source = source.replace(before, f"{new}\n{before}", 1)
    return source


@functools.cache
def clauses() -> dict[str, str]:
    """What LibreOffice says about the clause document after the two insertions, under the continental style and under
    the default (whose nested lists are the family's own)."""
    require_libreoffice()
    edits = [(match, new) for match, new, _, _ in CLAUSE_EDITS]
    jobs = {style: (render(CLAUSES, format="docx", **settings).binary, edits)
            for style, settings in {"continental": {"style": "continental"}, "default": {}}.items()}
    return libreoffice_edit(jobs)  # type: ignore[arg-type]


def lines_of(text: str) -> list[str]:
    return [squeeze(line) for line in text.lstrip("\ufeff").splitlines() if squeeze(line)]


@pytest.mark.parametrize("style", ["continental", "default"])
def test_a_clause_and_a_nested_item_inserted_in_word_are_numbered_as_the_renderer_numbers_them(style: str) -> None:
    settings = {"style": "continental"} if style == "continental" else {}
    after = render(applied(CLAUSES, [(before, new) for _, _, before, new in CLAUSE_EDITS]), format="text", **settings)
    said = lines_of(clauses()[style])
    assert said == lines_of(after.output)
    # the item inserted into a list of nested items starts it: "a)" under the continental style, "(i)" under the default
    first, second = ("a)", "b)") if style == "continental" else ("(i)", "(ii)")
    assert [line for line in said if line.endswith(("NEW nested", "nested of two"))] == [
        f"{first} NEW nested", f"{second} nested of two"]


def test_the_clause_inserted_under_the_continental_style_takes_the_next_number_of_the_heading_list() -> None:
    said = lines_of(clauses()["continental"])
    assert [line for line in said if line.endswith(("NEW clause", "clause two"))] == ["1.2 NEW clause", "1.3 clause two"]
    assert [line for line in said if line.startswith("(1) Refs:")] == ["(1) Refs: 1.1 1.1(a) 1.3 1.3(b) 1.3(b)(i)"]


# -- A sub-heading Word restarts at a fixed number: the limit of what an edit keeps live -------------------------------


@functools.cache
def resynced() -> dict[str, str]:
    require_libreoffice()
    data = render(RESYNCED, format="docx", style="continental").binary
    jobs = {"original": (data, []), "clause deleted": (data, [("spare clause", None)]),
            "clause inserted": (data, [("spare clause", "NEW clause")]),
            "heading deleted": (data, [("Beta", None)]), "heading inserted": (data, [("Beta", "NEWH")])}
    return libreoffice_edit(jobs)  # type: ignore[arg-type]


def test_a_sub_heading_after_clauses_is_restarted_by_a_fixed_value_and_the_plan_says_so() -> None:
    result = render(RESYNCED, format="docx", style="continental")
    plan, diagnostics = plan_docx(result.tree)
    assert plan.units["s2"].cause == ("resync",)
    [found] = [d for d in diagnostics if d.rule == "render-docx-shared-counter"]
    assert found.level == "warning"
    assert lines_of(resynced()["original"]) == lines_of(render(RESYNCED, format="text", style="continental").output)


def test_a_clause_inserted_or_deleted_before_the_restarted_sub_heading_leaves_it_and_what_follows_right() -> None:
    sources = {"clause deleted": RESYNCED.replace("- spare clause\n", ""),
               "clause inserted": applied(RESYNCED, [("- spare clause", "- NEW clause")])}
    for name, source in sources.items():
        assert lines_of(resynced()[name]) == lines_of(render(source, format="text", style="continental").output), name


def test_a_sub_heading_inserted_before_the_restarted_one_takes_its_num_and_everything_follows() -> None:
    source = applied(RESYNCED, [("## Beta", "## NEWH\n")])
    assert lines_of(resynced()["heading inserted"]) == lines_of(
        render(source, format="text", style="continental").output)


def test_deleting_the_restarted_sub_heading_is_the_documented_limit() -> None:
    """The heading that holds the fixed value is gone: the headings after it go on from the counter the clauses of the
    section used, so Gamma reads 1.4 where the renderer's document says 1.1 (the plan's warning says so). The
    clauses before it are right and the broken reference to the deleted heading is Word's."""
    _, diagnostics = plan_docx(render(RESYNCED, format="docx", style="continental").tree)
    [found] = [d for d in diagnostics if d.rule == "render-docx-shared-counter"]
    assert "if it is deleted in Word the headings after it read higher" in found.message
    said = lines_of(resynced()["heading deleted"])
    assert [line for line in said if "clause" in line and not line.startswith("(")] == [
        "1.1 clause one", "1.2 spare clause", "1.3 clause two"]
    assert "1.4 Gamma" in said and "1.1 Gamma" in lines_of(render(
        RESYNCED.replace("## Beta {#beta}\n\n", ""), format="text", style="continental").output)


@functools.cache
def shared_level() -> dict[str, str]:
    require_libreoffice()
    levels = {"paragraphs.numbered": True, "paragraphs.format": {"counter": "decimal", "label": "{section}.{n}",
                                                                 "ref": ".{n}"}}
    data = render(PARAGRAPHS_AND_CLAUSES, format="docx", style="continental", overrides=levels).binary
    return libreoffice_edit({"original": (data, []), "inserted": (data, [("One.", "NEWP")])})  # type: ignore[arg-type]


PARAGRAPHS_AND_CLAUSES = FRONT + "# A\n\nOne.\n\n- x\n- y\n\nTwo.\n"


def test_a_paragraph_restarted_after_clauses_does_not_follow_a_paragraph_inserted_before_it() -> None:
    levels = {"paragraphs.numbered": True, "paragraphs.format": {"counter": "decimal", "label": "{section}.{n}",
                                                                 "ref": ".{n}"}}
    assert lines_of(shared_level()["original"]) == lines_of(render(
        PARAGRAPHS_AND_CLAUSES, format="text", style="continental", overrides=levels).output)
    plan, diagnostics = plan_docx(render(PARAGRAPHS_AND_CLAUSES, format="docx", style="continental",
                                         overrides=levels).tree)
    [found] = [d for d in diagnostics if d.rule == "render-docx-shared-counter"]
    assert "inserted or deleted before it in Word does not move it" in found.message
    # the renderer would number the new paragraph 1.1, One 1.2 and Two 1.3; Two holds its fixed value
    said = lines_of(shared_level()["inserted"])
    assert [line for line in said if line.endswith(("NEWP", "One.", "Two."))] == ["1.1 NEWP", "1.2 One.", "1.2 Two."]


# -- The contents: the headings with the numbers Word draws, and none of the units on the heading list ------------------

CONTENTS = ALTERNATIVES + """
# Alpha {#alpha}

- item one {#i1}
  - nested {#n1}
- item two {#i2}

## Beta {#beta}

- beta item

# Disputes {#d when=forum:courts}

- courts item

## Sub

# Disputes {#d when=forum:arb}

## Sub alt

# Last
"""


@functools.cache
def contents() -> tuple[DocxPlan, tuple[str, ...], str]:
    """The plan, the entries of the contents LibreOffice builds from the ``TOC`` field the writer wrote (the lines that
    end in a page number), and the rest of what it shows."""
    require_libreoffice()
    result = render(CONTENTS, format="docx", style="continental", overrides={"contents.enabled": True,
                                                                              "contents.depth": 5})
    assert result.binary is not None
    plan, _ = plan_docx(result.tree)
    lines = libreoffice_export({"contents": result.binary})["contents"].text.lstrip("\ufeff").splitlines()
    entries = tuple(line for line in lines if re.fullmatch(r".*\t\d+", line))
    return plan, entries, "\n".join(line for line in lines if line not in entries)


def test_the_contents_list_the_headings_as_the_plan_does_and_none_of_the_items_on_the_heading_list() -> None:
    plan, entries, body = contents()
    expected = [squeeze(f"{entry.number} {plain_text(entry.title)} {entry.condition_label or ''}") for entry in plan.toc]
    said = [re.sub(r"\s+\d+$", "", squeeze(line)) for line in entries]
    assert said == expected
    assert len(plan.toc) == 7
    assert [e.number for e in plan.toc] == ["1.", "1.1", "2.", "2.1", "2.", "2.1", "3."]  # the alternative's is shared
    assert not any(word in line for line in entries for word in ("item", "nested"))
    assert "1.1 item one" in body and "a) nested" in body  # the items are in the body: on the heading list, not in it
