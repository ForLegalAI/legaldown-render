"""Acceptance of the DOCX writer: the measurable claims of ADR 0008, over the whole corpus and the three built-in styles.

The corpus is that of ``test_docx_roundtrip.py`` (``tests/documents``, the specification's examples and valid
fixtures, and the documents written to stress the numbering); that module is the finer instrument (it makes the cached
results wrong on purpose and compares every number and reference unit by unit), and this one the coarse, complete one:

* **Equivalence.** LibreOffice opens each document with its fields and indexes updated, and what it shows is what the
  plain-text writer prints, line by line, **modulo the differences the formats have by nature** (:func:`normalise`:
  borders for rules and signing lines, a table's cells, the markup of the plain-text layout) **and the gaps the plan
  reports** (:data:`GAPS`: a number Word draws differently from the renderer's, with a ``render-docx-*`` diagnostic;
  :data:`GAP_LINES` pins each line that differs, so that nothing else may).
  The numbers, the cross-references, the entries of the contents, the attachments and the signature blocks are in that
  text. The page numbers of the contents are checked on their own: they are positive, never decrease and never pass the
  number of pages.
* **Live after edits.** In one LibreOffice session, for a handful of documents under each style, a heading, a list
  item, a nested item and (under the continental style) a numbered paragraph are inserted, and a clause is deleted,
  as a lawyer does (:mod:`tests/lo/uno_edit.py`); the fields and indexes are updated; the result is what the text
  writer prints for the source with the same edits made, exactly: in the documents of the subset no number is fixed in a
  way these edits reach.
* **Valid and deterministic.** Every part of every document validates against the ISO/IEC 29500 schemas, and rendering a
  document twice gives the same bytes (that test has the marker ``acceptance`` alone).

The LibreOffice tests are skipped when ``soffice`` or ``python3-uno`` is missing and fail instead when
``LEGALDOWN_REQUIRE_DOCX_TOOLS`` is set (``tests/docx_support.py``). The module is part of the DOCX job (``pytest -m
"libreoffice or ooxml_schema"``): ``conftest.py`` leaves it out of a run that names no marker, which keeps the default
``pytest`` as quick as it was.
"""
from __future__ import annotations

import difflib
import functools
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from conftest import spec_root
from docx_support import (
    Exported,
    W,
    libreoffice_edit,
    libreoffice_export,
    parts,
    require_libreoffice,
    require_schemas,
    tool_missing,
    validation_errors,
)
from test_docx_roundtrip import EXPLAINING, corpus

from legaldown_render import DocumentError, render
from legaldown_render.tree import (
    Code,
    CrossRef,
    DraftingNote,
    Emphasis,
    List,
    ListItem,
    Paragraph,
    Quote,
    RenderTree,
    Section,
    Strong,
    Text,
    iter_tree_inlines,
)
from legaldown_render.writers.text import inline_text

pytestmark = pytest.mark.acceptance

#: The built-in styles, with the table of contents on (down to the fifth level, so that every heading is in it).
STYLES: dict[str, dict] = {
    "default": {"overrides": {"contents.enabled": True, "contents.depth": 5}},
    "outline": {"style": "outline", "overrides": {"contents.enabled": True, "contents.depth": 5}},
    "continental": {"style": "continental", "overrides": {"contents.enabled": True, "contents.depth": 5}},
}

#: The diagnostics that say Word (and so LibreOffice, which follows its numbering) draws a number differently from the
#: renderer's: a letter counter past 26, a Roman numeral from 4000, a list nested past Word's levels, a label that shows
#: counters Word's list cannot (``render-docx-section-label``), a number off by a count (``render-docx-number-mismatch``),
#: and the two readings of a reference that only LibreOffice has (``\t`` ignored, a level nothing numbered counted).
GAPS = EXPLAINING | {"render-docx-number-mismatch", "render-docx-reference-word-only", "render-docx-libreoffice-count"}


# -- The corpus ---------------------------------------------------------------------------------------------------


@dataclass
class Shown:
    """One document under one style: what the text writer prints, what LibreOffice shows, and what the render said."""

    name: str
    style: str
    tree: RenderTree
    written: str
    rules: frozenset[str]
    data: bytes
    shown: Exported | None = None


@functools.cache
def documents() -> dict[str, str]:
    """The corpus. A run that promises its tools (CI) must have the specification's documents in it."""
    found = corpus()
    if spec_root() is None:
        tool_missing("no LegalDown specification checkout (set LEGALDOWN_SPEC_DIR)")
    return found


def rendered(source: str, style: str) -> tuple[bytes, str, RenderTree, frozenset[str]] | None:
    """The DOCX, the text, the tree and the rules reported for *source* under *style* (None when it is not a document)."""
    try:
        made = render(source, format="docx", **STYLES[style])
    except DocumentError:
        return None
    assert made.binary is not None
    written = render(source, format="text", **STYLES[style])
    return made.binary, written.output, written.tree, frozenset(d.rule for d in made.diagnostics)


@functools.cache
def shown_corpus() -> dict[str, tuple[Shown, ...]]:
    """Every document under every style, shown by LibreOffice with its fields and indexes updated: one session."""
    require_libreoffice()
    cases: dict[str, list[Shown]] = {style: [] for style in STYLES}
    for name, source in documents().items():
        for style in STYLES:
            made = rendered(source, style)
            if made is not None:
                data, written, tree, rules = made
                cases[style].append(Shown(name, style, tree, written, rules, data))
    everything = {f"d{index}": case for index, case in enumerate(case for group in cases.values() for case in group)}
    exported = libreoffice_export({key: case.data for key, case in everything.items()})
    for key, case in everything.items():
        case.shown = exported[key]
    return {style: tuple(group) for style, group in cases.items()}


# -- Comparing -----------------------------------------------------------------------------------------------------


def squeeze(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def blocks_of(tree: RenderTree, *, quoted: bool = True) -> Iterator[Section | Paragraph | ListItem | DraftingNote]:
    """Every section, paragraph, list item and drafting note of the tree, in the order of the document; with *quoted* False
    not those in a quote or a drafting note (which are neither numbered units nor anything a lawyer edits as a clause)."""
    def walk(blocks: tuple) -> Iterator[Paragraph | ListItem | DraftingNote]:
        for block in blocks:
            match block:
                case Paragraph():
                    yield block
                case DraftingNote(blocks=inner):
                    yield block
                    if quoted:
                        yield from walk(inner)
                case List():
                    for item in block.items:
                        yield item
                        yield from walk(item.blocks)
                case Quote(blocks=inner):
                    if quoted:
                        yield from walk(inner)

    yield from walk(tree.preamble)
    for section in tree.sections:
        yield section
        yield from walk(section.blocks)


def normalise(written: str, shown: str, tree: RenderTree) -> tuple[list[str], list[str], list[int]]:
    """The lines of the plain-text writer's output *written* and of the text LibreOffice *shown*, made comparable, and the
    page numbers of the contents. The formats differ by nature in these ways, each undone here and in no other:

    * a rule (``---``) and a signing line (``____``) are borders of paragraphs and cells in the DOCX, not text;
    * a table is pipes and a separator row in the text and cells in the DOCX (tests/lo/uno_text.py reads one line a cell): a row is
      compared as the words of its cells, however many lines they take, so that a text laid out by row would do as well;
    * a quote's ``>`` is the indent of a style in the DOCX;
    * a link's ``<url>`` after its text is the target of the hyperlink;
    * a condition label (§15.8) and the label of a drafting note are in ``[ ]`` in the text and a styled run in the DOCX,
      the label of a heading or an attachment is a line of its own in the text and a run of the title's paragraph in the
      DOCX, and the label of a numbered paragraph stands before its number in the text and after it in the DOCX
      (the number is drawn by Word before the paragraph);
    * ``label: value`` rows of the title block and the parties are two cells in the DOCX (compared like a table row);
    * the table of contents of LibreOffice ends its entries with a tab and a page number, which the text has not: they are
      taken off and returned (their pages are not the renderer's to promise);
    * white space is not compared.
    """
    labels = {unit.condition_label for unit in blocks_of(tree) if getattr(unit, "condition_label", None)}
    labels |= {unit.label for unit in blocks_of(tree) if isinstance(unit, DraftingNote) and unit.label}
    labels |= {entry.condition_label for entry in tree.contents if entry.condition_label}
    labels |= {part.condition_label for part in tree.attachments if part.condition_label}
    titles = {(squeeze(" ".join(filter(None, (s.label, inline_text(s.title))))), s.condition_label)
              for s in tree.sections if s.condition_label}
    titles |= {(squeeze(inline_text(a.title)), a.condition_label) for a in tree.attachments if a.condition_label}
    swaps = [(unit.condition_label, unit.label) for unit in blocks_of(tree)
             if isinstance(unit, Paragraph) and unit.condition_label and unit.label]
    rows = [*tree.header, *(row for side in tree.sides for party in side.parties for row in party.details)]
    split = {squeeze(f"{label}{tree.colon}{inline_text(value)}"): [squeeze(label), squeeze(inline_text(value))]
             for label, value in rows if label}

    #: (the text of a line, whether it is the cells of a table row or the label and value of a title-block row)
    items: list[tuple[str, bool]] = []
    for raw in written.splitlines():
        line = raw.strip()
        if re.fullmatch(r"-{3,}|_{3,}", line) or re.fullmatch(r"\|[:\-| ]+\|", line):
            continue
        if line.startswith("|") and line.endswith("|"):
            cells = [squeeze(cell) for cell in re.split(r"(?<!\\)\|", line[1:-1])]
            items.append((squeeze(" ".join(cells)), True))
            continue
        line = re.sub(r" <[^<>\s]*>", "", re.sub(r"^(>\s?)+", "", line))
        for label in labels:
            line = line.replace(f"[{label}]", label)
        for condition, number in swaps:
            line = line.replace(f"{condition} {number} ", f"{number} {condition} ")
        line = squeeze(line)
        if not line:
            continue
        if items and (items[-1][0], line) in titles:
            items[-1] = (f"{items[-1][0]} {line}", False)
            continue
        items.append((" ".join(split[line]) if line in split else line, line in split))

    raw_lines = shown.lstrip("\ufeff").splitlines()
    pages: list[int] = []
    squeezed = [squeeze(raw) for raw in raw_lines]
    if tree.contents_label and tree.contents_label in squeezed:
        start = squeezed.index(tree.contents_label) + 1
        for position in range(start, min(start + len(tree.contents), len(raw_lines))):
            entry = re.fullmatch(r"(.*)\t(\d+)\s*", raw_lines[position])
            if entry:
                squeezed[position] = squeeze(entry[1])
                pages.append(int(entry[2]))
    shown_lines = [line for line in squeezed if line]

    # A row that the text writer prints on one line is read from as many lines of LibreOffice's as it takes to say the
    # same words (one a cell, as tests/lo/uno_text.py reads the document, but not by that alone).
    written_lines: list[str] = []
    seen: list[str] = []
    cursor = 0
    for text, cells in items:
        written_lines.append(text)
        if cursor >= len(shown_lines):
            continue
        line = shown_lines[cursor]
        cursor += 1
        while cells and line != text and text.startswith(line) and cursor < len(shown_lines):
            line = squeeze(f"{line} {shown_lines[cursor]}")
            cursor += 1
        seen.append(line)
    seen += shown_lines[cursor:]
    return written_lines, seen, pages


def difference(written: list[str], shown: list[str]) -> str:
    return "\n".join(difflib.unified_diff(written, shown, "text writer", "LibreOffice", lineterm="", n=1))


#: The lines that differ, by (style, document), where the plan reports a gap: what the text writer prints, then what LibreOffice
#: (which follows Word's numbering) shows. Nothing else may differ: not a line more, not a word of any length.
GAP_LINES: dict[tuple[str, str], list[tuple[str, str]]] = {
    # letters past 26: Word repeats them (bb), the renderer counts on (ab) -- render-docx-letter-overflow
    ("default", "stress/letters"): [
        ("(ab) item 28", "(bb) item 28"), ("(ac) item 29", "(cc) item 29"), ("(ad) item 30", "(dd) item 30"),
        ("(ae) item 31", "(ee) item 31"), ("Ref 1(a) 1(z) 1(aa) 1(ab) 1(ae)", "Ref 1(a) 1(z) 1(aa) 1(bb) 1(ee)")],
    ("outline", "stress/letters"): [
        ("(ab) item 28", "(bb) item 28"), ("(ac) item 29", "(cc) item 29"), ("(ad) item 30", "(dd) item 30"),
        ("(ae) item 31", "(ee) item 31"), ("Ref I(a) I(z) I(aa) I(ab) I(ae)", "Ref I(a) I(z) I(aa) I(bb) I(ee)")],
    # a list that cannot show the section's number draws its own counter alone -- render-docx-section-label
    ("continental", "stress/qualified"): [("1.1 deeper", "1 deeper")],
    ("continental", "stress/paths"): [("1.1 four", "1 four"), ("1.1 seven", "1 seven")],
    ("continental", "stress/ordered paths"): [
        ("1.1 four", "1 four"), ("1.2 four b", "2 four b"), ("1.1 four c", "1 four c"), ("1.1 y", "1 y"), ("1.1 w", "1 w")],
    ("continental", "stress/deep"): [
        ("1.1 level 3", "1 level 3"), ("1.1 level 6", "1 level 6"), ("1.1 level 9", "1 level 9"),
        ("1.1 level 9 again", "1 level 9 again")],
}


def check(case: Shown) -> bool:
    """Assert the equivalence of *case*; returns True if it holds exactly, False if it holds modulo the reported gap that
    :data:`GAP_LINES` pins line by line (and that the render must report)."""
    assert case.shown is not None
    written, seen, pages = normalise(case.written, case.shown.text, case.tree)
    if case.tree.contents:
        assert pages, "the contents lists no page numbers"
        assert pages == sorted(pages) and pages[0] >= 1 and pages[-1] <= case.shown.pages, (pages, case.shown.pages)
    if written == seen:
        assert (case.style, case.name) not in GAP_LINES, "the gap that was pinned is gone: remove it from GAP_LINES"
        return True
    assert case.rules & GAPS, f"the numbers differ and the render reports no gap:\n{difference(written, seen)}"
    assert len(written) == len(seen), f"a gap changes lines, not their number:\n{difference(written, seen)}"
    differing = [(a, b) for a, b in zip(written, seen, strict=True) if a != b]
    assert differing == GAP_LINES.get((case.style, case.name)), f"not the pinned gap:\n{difference(written, seen)}"
    return False


@pytest.mark.libreoffice
@pytest.mark.parametrize("style", STYLES)
def test_libreoffice_shows_every_document_as_the_text_writer_prints_it(style: str) -> None:
    """Numbers, references, the contents, the attachments, the signatures: everything the plain-text writer prints is what
    LibreOffice shows after it has updated the fields and indexes, except in the documents the plan reports a gap for."""
    for case in shown_corpus()[style]:
        try:
            check(case)
        except AssertionError as error:
            raise AssertionError(f"{style}/{case.name}: {error}") from error


@pytest.mark.libreoffice
def test_the_corpus_is_not_vacuous_and_every_gap_is_one_the_plan_reports() -> None:
    """The comparison reaches what it is meant to: the specification's documents, a table of contents and signature
    blocks, attachments, template constructs and references; and the documents that differ are few and are the
    stress documents that ask for what Word draws differently (letters past 26, labels that need the section)."""
    cases = [case for group in shown_corpus().values() for case in group]
    assert len(cases) >= 3 * 30
    assert sum(case.name.startswith(("examples/", "fixtures/")) for case in cases) >= 3 * 18
    assert all(case.tree.contents for case in cases if case.tree.sections)
    assert any(case.tree.signatures for case in cases) and any(case.tree.attachments for case in cases)
    assert any(case.tree.is_template for case in cases)
    assert sum(isinstance(i, CrossRef) for case in cases for i in iter_tree_inlines(case.tree)) > 100
    different = [case for case in cases if not check(case)]
    assert {case.name for case in different} <= {f"stress/{name}" for name in (
        "letters", "qualified", "paths", "ordered paths", "deep")}, [(c.style, c.name) for c in different]
    assert different, "the gaps the plan reports are not reached"
    assert all(case.name.startswith("stress/") for case in different)
    assert all(case.style != "default" or case.name == "stress/letters" for case in different)


@pytest.mark.libreoffice
def test_the_comparison_catches_a_wrong_number_a_missing_line_and_a_wrong_page_order() -> None:
    """The check is not vacuous: a number LibreOffice draws differently, a line it leaves out, and a table of contents whose
    pages run backwards are each a failure."""
    case = next(c for c in shown_corpus()["default"] if c.name == "documents/features.lgd")
    assert case.shown is not None and check(case)
    text = case.shown.text
    assert "\n2. Services" in text
    for broken in (text.replace("\n2. Services", "\n3. Services", 1),
                   text.replace("Business Day means", "", 1),
                   re.sub(r"\t(\d+)\n", lambda m: f"\t{9 - int(m[1])}\n", text, count=2)):
        assert broken != text
        mutated = Shown(case.name, case.style, case.tree, case.written, case.rules, case.data, Exported(
            broken, case.shown.header, case.shown.footer, case.shown.pages))
        with pytest.raises(AssertionError):
            check(mutated)


@pytest.mark.libreoffice
def test_a_table_row_on_one_line_reads_as_the_same_row_on_several() -> None:
    """A table laid out as a line a cell (as the document is read) or a line a row (as some LibreOffice text filters write
    it) is the same table, so both pass."""
    case = next(c for c in shown_corpus()["default"] if c.name == "documents/features.lgd")
    assert case.shown is not None
    text = case.shown.text
    for cells in (["Service", "Fee", "Notes"], ["Hosting", "$500.00", "per month"], ["Support", "$250.75", "see 3"]):
        stacked = "\n".join(cells)
        assert stacked in text, cells
        text = text.replace(stacked, "\t".join(cells))
    assert text != case.shown.text
    assert check(Shown(case.name, case.style, case.tree, case.written, case.rules, case.data,
                       Exported(text, case.shown.header, case.shown.footer, case.shown.pages)))


# -- Edits ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Edit:
    """What a lawyer does in Word, and the same thing in the source."""

    kind: str
    #: The text of the paragraph it goes before (UNO inserts before the first paragraph that holds it), or the whole text of
    #: the paragraph that is deleted.
    match: str
    #: The text typed (None: the paragraph is deleted).
    new: str | None
    #: The line of the source it is made at (1-based), and what takes that line's place.
    line: int
    source: str


#: The documents the edits are made in: the two test documents, specification examples with lists, a template and
#: numbered clauses, and documents written for the numbering.
EDITED = (
    "documents/features.lgd", "documents/template.lgd", "examples/advanced/msa/master-service-agreement.lgd",
    "examples/advanced/template/consulting-agreement-template.lgd", "examples/simple/nda/mutual-nda.lgd",
    "examples/simple/policy/remote-work-policy.lgd", "fixtures/valid/anchors-and-refs.lgd", "stress/nested",
    "stress/lists", "stress/alternatives",
)

MARKER = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)")


def body_texts(data: bytes) -> list[str]:
    """The text of every paragraph of the document that is not a line of the table of contents or its label."""
    root = ET.fromstring(parts(data)["word/document.xml"])
    found = []
    for paragraph in root.iter(f"{W}p"):
        style = paragraph.find(f"{W}pPr/{W}pStyle")
        if style is not None and re.fullmatch(r"TOC\d|ContentsHeading", style.get(f"{W}val", "")):
            continue
        found.append("".join(t.text or "" for t in paragraph.iter(f"{W}t")))
    return found


def plain(inlines: tuple) -> str | None:
    """The text of *inlines* if it is words alone, possibly emphasised (a paragraph that holds a field, a value or a break has
    text that changes or that Word keeps apart, which words do not find)."""
    found = []
    for inline in inlines:
        if isinstance(inline, Text | Code):
            found.append(inline.text)
        elif isinstance(inline, Emphasis | Strong):
            inner = plain(inline.children)
            if inner is None:
                return None
            found.append(inner)
        else:
            return None
    return "".join(found) or None


def leading(inlines: tuple) -> str | None:
    """The words a paragraph starts with, if it starts with some (up to 40 characters of its first text, which a field
    later in the paragraph does not change): what UNO needs to find a paragraph to insert before."""
    first = inlines[0] if inlines else None
    return first.text[:40].strip() if isinstance(first, Text) and len(first.text.strip()) >= 8 else None


def unique(texts: list[str], text: str) -> bool:
    """Whether exactly one paragraph holds *text* (so that UNO, which takes the first that does, finds that one)."""
    return len(text) >= 4 and sum(text in other for other in texts) == 1


def edits_for(source: str, data: bytes, tree: RenderTree) -> list[Edit]:
    """The edits the document allows: a heading, an item and a nested item inserted before the first that qualify, a
    numbered paragraph where the style numbers them, and the last clause that is a single line nothing refers to deleted.
    A target qualifies when it stands in the source on a line of its own, has no condition, is not a later alternative and
    has text alone (so that its words find it)."""
    lines = source.splitlines()
    texts = body_texts(data)
    referred = {i.target_unit for i in iter_tree_inlines(tree) if isinstance(i, CrossRef)}
    found: dict[str, Edit] = {}
    deletable: Edit | None = None

    def lead(line: int) -> str:
        return lines[line - 1] if 0 < line <= len(lines) else ""

    for node in blocks_of(tree, quoted=False):
        if getattr(node, "condition", "") or getattr(node, "shares_number_with", None) or node.line is None:
            continue
        if isinstance(node, Section):
            text, row = plain(node.title) or leading(node.title), lead(node.line)
            if text and "heading" not in found and re.match(rf"#{{{node.level}}} ", row) and unique(texts, text):
                found["heading"] = Edit("heading", text, "ZZ heading", node.line,
                                        f"{'#' * node.level} ZZ heading\n\n{row}")
        elif isinstance(node, ListItem) and node.blocks and isinstance(node.blocks[0], Paragraph):
            whole, row = plain(node.blocks[0].inlines), lead(node.line)
            text = whole or leading(node.blocks[0].inlines)
            marker = MARKER.match(row)
            if not text or not marker or not unique(texts, text):
                continue
            nested = node.parent_unit is not None and any(
                isinstance(other, ListItem) and other.unit == node.parent_unit for other in blocks_of(tree, quoted=False))
            kind = "nested item" if nested else "item"
            if kind not in found:
                found[kind] = Edit(kind, text, f"ZZ {kind}", node.line, f"{marker[1]}ZZ {kind}\n{row}")
            single = whole is not None and texts.count(whole) == 1 and len(node.blocks) == 1 \
                and row[marker.end():].startswith(whole)
            following = lead(node.line + 1)
            if (single and node.unit not in referred
                    and (not following.strip() or MARKER.match(following) or following.startswith("#"))):
                deletable = Edit("delete", whole, None, node.line, "")
        elif isinstance(node, Paragraph) and node.top_level and node.label:
            text, row = plain(node.inlines) or leading(node.inlines), lead(node.line)
            if text and "paragraph" not in found and row.startswith(text[:12]) and unique(texts, text):
                found["paragraph"] = Edit("paragraph", text, "ZZ paragraph", node.line, f"ZZ paragraph\n\n{row}")
    chosen = list(found.values())
    if deletable is not None and all(deletable.line != edit.line for edit in chosen):
        chosen.append(deletable)
    return chosen


def made(source: str, edits: list[Edit]) -> str:
    """*source* with the edits made, from the last line up so that the lines of the others stay where they are."""
    lines = source.splitlines()
    for edit in sorted(edits, key=lambda e: e.line, reverse=True):
        lines[edit.line - 1:edit.line] = edit.source.split("\n") if edit.source else []
    return "\n".join(lines) + "\n"


@dataclass
class Edited:
    name: str
    style: str
    rules: frozenset[str]
    edits: list[Edit]
    after: RenderTree
    written: str
    #: The tree and the text of the document before the edits.
    before: RenderTree
    unedited: str
    shown: str = ""


@functools.cache
def edited_corpus() -> dict[str, tuple[Edited, ...]]:
    """The edited documents under each style, edited in LibreOffice and updated: one session for all of them."""
    require_libreoffice()
    jobs: dict[str, tuple[bytes, list[tuple[str, ...]]]] = {}
    cases: dict[str, list[Edited]] = {style: [] for style in STYLES}
    keyed: dict[str, Edited] = {}
    for name in EDITED:
        source = documents()[name]
        for style in STYLES:
            made_ = rendered(source, style)
            assert made_ is not None, name
            data, unedited, tree, rules = made_
            edits = edits_for(source, data, tree)
            if not edits:
                continue
            after = render(made(source, edits), format="text", **STYLES[style])
            case = Edited(name, style, rules, edits, after.tree, after.output, tree, unedited)
            cases[style].append(case)
            key = f"e{len(keyed)}"
            keyed[key] = case
            jobs[key] = (data, [(e.match, e.new) for e in edits])
    shown = libreoffice_edit(jobs)
    for key, case in keyed.items():
        case.shown = shown[key]
    return {style: tuple(group) for style, group in cases.items()}


@pytest.mark.libreoffice
@pytest.mark.parametrize("style", STYLES)
def test_the_document_follows_the_edits_a_lawyer_makes(style: str) -> None:
    """After a heading, an item, a nested item and a numbered paragraph are inserted and a clause is deleted, and the fields
    and indexes are updated, the document reads as the plain-text writer prints the source with the same edits: every number
    and reference moved, the contents list the new heading. The text is equal, line for line, in every document: an edit
    that reached a number fixed in the file (a restart by a fixed value) would show, and none does in this subset."""
    for case in edited_corpus()[style]:
        written, seen, _ = normalise(case.written, case.shown, case.after)
        assert written == seen, (f"{style}/{case.name}: not live after {[e.kind for e in case.edits]}:\n"
                                 f"{difference(written, seen)}")


@pytest.mark.libreoffice
@pytest.mark.parametrize("style", STYLES)
def test_the_edits_move_the_numbers_after_them(style: str) -> None:
    """The edits are not no-ops that a document with static numbers would pass: in every document, what the edits did not
    type or delete reads differently afterwards (the headings after an inserted one are numbered one higher, and so on)."""
    for case in edited_corpus()[style]:
        before, _, _ = normalise(case.unedited, "", case.before)
        after, _, _ = normalise(case.written, "", case.after)
        typed = [line for line in after if "ZZ " in line]
        deleted = {edit.match for edit in case.edits if edit.new is None}
        rest = [line for line in after if "ZZ " not in line]
        assert typed and rest != [line for line in before if not any(m in line for m in deleted)], (style, case.name)


@pytest.mark.libreoffice
def test_every_kind_of_edit_is_made_in_the_corpus() -> None:
    """The subset is not vacuous: a heading, an item, a nested item and a deleted clause are edited under every style, and a
    numbered paragraph under the continental style (the others do not number paragraphs)."""
    for style, group in edited_corpus().items():
        kinds = {edit.kind for case in group for edit in case.edits}
        assert {"heading", "item", "nested item", "delete"} <= kinds, (style, kinds)
        assert ("paragraph" in kinds) == (style == "continental"), (style, kinds)


# -- The schemas and determinism -----------------------------------------------------------------------------------


@pytest.mark.ooxml_schema
@pytest.mark.parametrize("style", STYLES)
def test_every_part_of_every_corpus_document_validates(style: str) -> None:
    require_schemas()
    for name, source in documents().items():
        made_ = rendered(source, style)
        if made_ is not None:
            assert validation_errors(made_[0]) == [], f"{style}/{name}"


@pytest.mark.parametrize("style", STYLES)
def test_rendering_a_corpus_document_twice_gives_the_same_bytes(style: str) -> None:
    """Needs no tool but the corpus (marker ``acceptance`` alone: ``pytest -m acceptance`` runs it)."""
    for name, source in documents().items():
        first, second = rendered(source, style), rendered(source, style)
        assert (first is None) == (second is None), f"{style}/{name}"
        if first is not None and second is not None:
            assert first[0] == second[0], f"{style}/{name}: two renders differ"
