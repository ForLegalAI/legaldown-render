#!/usr/bin/env python3
"""The document for the Word checks of what the DOCX planner assumes (.github/PUBLISHING.md, "Checking the DOCX in
Word", items 8 and 9): what Word's ``REF \\n \\t`` returns for the label forms the plan relies on, whether a level text
of the heading list that names the levels around a never-used one (``%1.%2(%6)`` with levels 3 to 5 unused) draws and
restarts as the plan models it, and what a skipped heading level draws.

Run it with the project's environment, from the repository root::

    python tests/word_check.py word-check/numbering-assumptions.docx

Open the file in Word, update every field (Ctrl+A, F9), and compare each line with its last column: the number Word
draws, ``REF \\n`` and ``REF \\n \\t`` as Word computes them, against what the planner assumes under each setting of
``docx.reference_text_switch``. Every field holds, until it is updated, the planner's own value. Anything that differs
is a finding for the planner (report it, and set ``docx.reference_text_switch`` to what your Word does).

The planner's side comes from the planner's own code (the simulator of Word's counters, ``suppressed``), never from a
table written here, so a change to the model shows in the file.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from legaldown_render.resolve.docx_cover import suppressed
from legaldown_render.resolve.docx_numbering import Count, Lit, WordNumbering
from legaldown_render.resolve.docx_plan import AbstractNum, LevelDef, Num
from legaldown_render.style import load_style
from legaldown_render.style.docx import docx_settings
from legaldown_render.writers.docx import package as pkg
from legaldown_render.writers.docx.numbering import abstract_num, num
from legaldown_render.writers.docx.ooxml import Bookmarks, complex_field, el, p, serialize, text_run, val
from legaldown_render.writers.docx.properties import build_app, build_core
from legaldown_render.writers.docx.settings import build_numbering, build_settings
from legaldown_render.writers.docx.styles import build_styles

_STEP = 567


def _level(ilvl: int, num_fmt: str, text: str) -> LevelDef:
    """A level of a list of the check document. None is linked to a paragraph style: the document's own titles are
    ``Heading`` paragraphs that Word must not number, and the numbered lines carry ``w:numPr`` themselves."""
    return LevelDef(ilvl, num_fmt, text, left=_STEP * (ilvl + 1), hanging=_STEP)


def _neutral(ilvl: int) -> LevelDef:
    return _level(ilvl, "decimal", f"%{ilvl + 1}")


@dataclass(frozen=True)
class Row:
    """One label form: the levels of its own list, and the level whose paragraph is referred to (the paragraphs of the
    levels above it come first)."""

    title: str
    levels: tuple[LevelDef, ...]
    target: int


#: The label forms the cover can meet. Documented: Microsoft's example, ``Section 1.01`` gives ``1.01``.
ROWS = (
    Row("roman counter with a word: Article %1", (_level(0, "upperRoman", "Article %1"),), 0),
    Row("section sign: § %1", (_level(0, "decimal", "§ %1"),), 0),
    Row("Microsoft's example: Section %1.%2", (_level(0, "decimal", "%1"), _level(1, "decimal", "Section %1.%2")), 1),
    Row("word and roman counter before a decimal: Section %1.%2 with a roman %1",
        (_level(0, "upperRoman", "%1"), _level(1, "decimal", "Section %1.%2")), 1),
    Row("an abbreviation with a period: Čl. %1", (_level(0, "decimal", "Čl. %1"),), 0),
    Row("an abbreviation with a period: No. %1", (_level(0, "decimal", "No. %1"),), 0),
    Row("parentheses around a letter: (%1)", (_level(0, "lowerLetter", "(%1)"),), 0),
    Row("a parenthesis after a letter: %1)", (_level(0, "lowerLetter", "%1)"),), 0),
    Row("a colon after a number: %1:", (_level(0, "decimal", "%1:"),), 0),
    Row("a path with a parenthesis: %1.%2(%3)",
        (_level(0, "decimal", "%1"), _level(1, "decimal", "%1.%2"), _level(2, "lowerLetter", "%1.%2(%3)")), 2),
    Row("words between the counters: Part %1 of %2 (only the guess drops them)",
        (_level(0, "decimal", "%1"), _level(1, "decimal", "Part %1 of %2")), 1),
)

#: The levels of the heading list as the planner writes them for a style with items on the heading list's second level
#: and a ``{path}`` list nested in them on the reserved level 6: levels 3 to 5 are never used.
HEADING_LIST = (
    _level(0, "decimal", "%1."), _level(1, "decimal", "%1.%2"), _level(2, "decimal", "%1.%2.%3"),
    _level(3, "decimal", "%1.%2.%3.%4"), _level(4, "decimal", "%1.%2.%3.%4.%5"),
    _level(5, "lowerLetter", "%1.%2(%6)"), _level(6, "lowerRoman", "%1.%2(%6)(%7)"), _neutral(7), _neutral(8))
SKIPPING_LIST = (
    _level(0, "decimal", "%1."), _level(1, "decimal", "%1.%2"),
    _level(2, "decimal", "%1.%2.%3"), *(_neutral(k) for k in range(3, 9)))


@dataclass
class _Document:
    """What the check document is made of: paragraphs, and the numbering definitions they are numbered by."""

    body: list[ET.Element]
    abstracts: list[ET.Element]
    nums: list[ET.Element]
    bookmarks: Bookmarks

    def list_(self, levels: Sequence[LevelDef], *, overrides: Sequence[tuple[int, int]] = ()) -> tuple[int, WordNumbering]:
        """A new abstractNum with a num; returns the numId and the simulator of Word's counters for it."""
        padded = [*levels, *(_neutral(k) for k in range(len(levels), 9))]
        abstract = AbstractNum(len(self.abstracts), "enumerated", f"{0x4C440100 + len(self.abstracts):08X}",
                               "multilevel", tuple(padded))
        instance = Num(len(self.nums) + 1, abstract.id, tuple(overrides))
        self.abstracts.append(abstract_num(abstract))
        self.nums.append(num(instance))
        simulator = WordNumbering(skipped="lo")
        simulator.add_abstract(abstract.id, padded)
        simulator.add_num(instance)
        return instance.id, simulator

    def line(self, *content: object, style: str | None = None, numbering: tuple[int, int] | None = None) -> None:
        props = [el("numPr", None, val("ilvl", numbering[1]), val("numId", numbering[0]))] if numbering else []
        self.body.append(p(*content, style=style, props=props))

    def text(self, text: str, *, style: str | None = None) -> None:
        self.line(text_run(text), style=style)


def _field(instruction: str, cached: str) -> list[ET.Element]:
    return complex_field(instruction, [text_run(cached)])


def _assumed(tokens: Sequence[Lit | Count]) -> str:
    """What the planner assumes ``REF \\n \\t`` returns, under each setting of ``docx.reference_text_switch``."""
    said = []
    for mode in ("documented", "guess"):
        kept = suppressed(tokens, mode)  # type: ignore[arg-type]
        said.append(f"{mode}: " + ("the switch is not used" if kept is None else f"'{''.join(t.text for t in kept)}'"))
    return "; ".join(said)


def _words(tokens: Sequence[Lit | Count]) -> str:
    """What ``REF \\n`` returns: the number's text without its trailing periods."""
    return "".join(t.text for t in tokens).rstrip(".")


def build() -> bytes:
    """The check document as a ``.docx``."""
    doc = _Document([], [], [], Bookmarks())
    doc.text("Word check of the planner's assumptions about numbering", style="Title")
    doc.text("Update every field (Ctrl+A, then F9). Each row gives the number as Word draws it, the field REF \\n and "
             "the field REF \\n \\t as Word computes them, and the planner's assumption. The fields hold the planner's "
             "values until they are updated: report every field that changes, and every row whose number is not the "
             "one shown as 'drawn'.")
    doc.text("A. The switch \\t on each label form (docx.reference_text_switch)", style="Heading1")
    for number, row in enumerate(ROWS, 1):
        num_id, simulator = doc.list_(row.levels)
        step = None
        for ilvl in range(row.target + 1):
            step = simulator.step(num_id, ilvl, f"row{number}-{ilvl}")
            name = f"t{number}"
            runs = text_run(f"target of row {number}") if ilvl == row.target else text_run(f"(parent {ilvl})")
            doc.line(*(doc.bookmarks.around(name, runs) if ilvl == row.target else [runs]),
                     numbering=(num_id, ilvl))
        assert step is not None
        tokens = list(step.tokens)
        doc.line(text_run(f"Row {number}, {row.title}: drawn '{step.label}'. REF \\n gives "),
                 *_field(f"REF t{number} \\n \\h", _words(tokens)), text_run(". REF \\n \\t gives "),
                 *_field(f"REF t{number} \\n \\t \\h", ("".join(t.text for t in (suppressed(tokens, "guess") or tokens)))),
                 text_run(f". The planner assumes REF \\n gives '{_words(tokens)}'; for REF \\n \\t, {_assumed(tokens)}."))
    doc.text("B. A level text that names the levels around three that are never used", style="Heading1")
    doc.text("The heading list below has levels 3 to 5 that nothing numbers, and a nested level 6 whose text is "
             "%1.%2(%6). The nested items must read 1.1(a), 1.1(b), then 1.2(a) under the next item.")
    num_id, simulator = doc.list_(HEADING_LIST, overrides=((1, 1),))
    for name, ilvl, text in (("r_head", 0, "heading"), ("r_item1", 1, "item 1"), ("r_n1", 5, "nested a"),
                             ("r_n2", 5, "nested b"), ("r_item2", 1, "item 2"), ("r_n3", 5, "nested under item 2")):
        step = simulator.step(num_id, ilvl, name)
        doc.line(*doc.bookmarks.around(name, text_run(text)), text_run(f"  [planner: {step.label}] REF \\n gives "),
                 *_field(f"REF {name} \\n \\h", step.word_ref), numbering=(num_id, ilvl))
    doc.text("C. A heading level the document skips", style="Heading1")
    doc.text("A level-1 heading followed by a level-3 one. The renderer, and LibreOffice, count the skipped level as 1 "
             "(the planner caches that reading, and writes the skipped level's part of a reference as text); Word is "
             "expected to draw 0 for it. Record what Word draws.")
    heading_id, heading_sim = doc.list_(SKIPPING_LIST)
    for name, ilvl, style, text in (("s_one", 0, "Heading1", "First heading"), ("s_deep", 2, "Heading3", "Third level")):
        step = heading_sim.step(heading_id, ilvl, name)
        doc.line(*doc.bookmarks.around(name, text_run(text)), style=style, numbering=(heading_id, ilvl))
        doc.line(text_run(f"[planner: {step.label}] REF \\n gives "), *_field(f"REF {name} \\n \\h", step.word_ref))
    return _package(doc)


def _package(doc: _Document) -> bytes:
    style = load_style("default")
    settings = docx_settings(style)
    package = pkg.Package()
    package.relationships.add(pkg.REL_OFFICE_DOCUMENT, "word/document.xml")
    package.relationships.add(pkg.REL_CORE_PROPERTIES, "docProps/core.xml")
    package.relationships.add(pkg.REL_EXTENDED_PROPERTIES, "docProps/app.xml")
    rels = pkg.Relationships()
    rels.add(pkg.REL_STYLES, "styles.xml")
    rels.add(pkg.REL_NUMBERING, "numbering.xml")
    rels.add(pkg.REL_SETTINGS, "settings.xml")
    page = settings.page
    section = el("sectPr", None,
                 el("pgSz", {"w": page.width, "h": page.height}),
                 el("pgMar", {"top": page.top, "right": page.right, "bottom": page.bottom, "left": page.left,
                              "header": page.header, "footer": page.footer, "gutter": 0}))
    package.add("docProps/core.xml", pkg.CT_CORE_PROPERTIES, build_core("Word check of the numbering assumptions", "en"))
    package.add("docProps/app.xml", pkg.CT_EXTENDED_PROPERTIES, build_app("legaldown-render word_check"))
    package.add("word/document.xml", pkg.CT_DOCUMENT, serialize(el("document", None, el("body", None, doc.body, section))),
                rels)
    package.add("word/styles.xml", pkg.CT_STYLES, build_styles(style, settings, "en", heading_num_id=None))
    package.add("word/numbering.xml", pkg.CT_NUMBERING, build_numbering(doc.abstracts, doc.nums))
    package.add("word/settings.xml", pkg.CT_SETTINGS, build_settings(update_fields=True))
    return package.to_bytes()


def main(argv: Sequence[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    path = Path(argv[1])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build())
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
