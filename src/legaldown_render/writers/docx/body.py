"""The body of the document: ``w:body`` and its closing ``w:sectPr``.

**Interim.** :func:`interim_body` writes the title, the headings as ``heading N``
paragraphs, and every other block as paragraphs of plain text (through the
text writer's inline text), so that a document round-trips through Word and
LibreOffice with its words in the right order. It writes no numbers: headings
are unnumbered until the numbering definitions exist, and a list item or a
numbered paragraph shows its text alone. The full body writer (numbering,
cross-references, the table of contents, the title block, attachments and
signature blocks) replaces it; :func:`section_properties` and the builders in
:mod:`.ooxml` are what it keeps.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator

from ...style.docx import PageGeometry
from ...tree import Block, CodeBlock, DraftingNote, List, Paragraph, Quote, RenderTree, Rule, Table
from ..text import inline_text
from .ooxml import el, p, text_run
from .styles import CODE, DRAFTING_NOTE, DRAFTING_NOTE_LABEL, QUOTE, SUBTITLE, TITLE, heading_style


def section_properties(page: PageGeometry, *, header_id: str | None = None, footer_id: str | None = None) -> ET.Element:
    """The ``w:sectPr`` of the one section: the running header and footer, by the
    relationship ids of their parts, then the paper and the margins in twips.
    ``w:pgSz`` of a landscape page has the long edge as its width, and says so
    (``w:orient``), which Word needs to print it so."""
    return el(
        "sectPr", None,
        header_id and el("headerReference", {"type": "default", "r:id": header_id}),
        footer_id and el("footerReference", {"type": "default", "r:id": footer_id}),
        el("pgSz", {"w": page.width, "h": page.height, "orient": "landscape" if page.landscape else None}),
        el("pgMar", {"top": page.top, "right": page.right, "bottom": page.bottom, "left": page.left,
                     "header": page.header, "footer": page.footer, "gutter": 0}),
    )


def interim_body(tree: RenderTree, page: PageGeometry, *, header_id: str | None = None,
                 footer_id: str | None = None) -> ET.Element:
    """``w:body`` for *tree*: see the module docstring. Interim."""
    content: list[ET.Element] = []
    title = inline_text(tree.title).strip()
    if title:
        content.append(p(text_run(title), style=TITLE))
    subtitle = inline_text(tree.subtitle).strip()
    if subtitle:
        content.append(p(text_run(subtitle), style=SUBTITLE))
    for block in tree.preamble:
        content += _block(block)
    for section in tree.sections:
        content.append(p(text_run(inline_text(section.title)), style=heading_style(min(max(section.level, 1), 5))))
        for block in section.blocks:
            content += _block(block)
    return el("body", None, content, section_properties(page, header_id=header_id, footer_id=footer_id))


def _block(block: Block, style: str | None = None) -> Iterator[ET.Element]:
    """The paragraphs of *block*, in *style* where it has none of its own."""
    match block:
        case Paragraph(inlines=inlines):
            yield p(text_run(inline_text(inlines)), style=style)
        case List(items=items):
            for item in items:
                for child in item.blocks:
                    yield from _block(child, style)
        case Quote(blocks=blocks):
            for child in blocks:
                yield from _block(child, QUOTE)
        case DraftingNote(blocks=blocks, label=label):
            if label:
                yield p(text_run(label, style=DRAFTING_NOTE_LABEL), style=DRAFTING_NOTE)
            for child in blocks:
                yield from _block(child, DRAFTING_NOTE)
        case CodeBlock(text=text):
            for line in text.rstrip("\n").split("\n"):
                yield p(text_run(line), style=CODE)
        case Table(header=header, rows=rows):
            for row in (header, *rows):
                yield p(text_run("\t".join(inline_text(cell) for cell in row)), style=style)
        case Rule():
            yield p(style=style, props=[el("pBdr", None, el("bottom", {"val": "single", "sz": 4, "space": 1,
                                                                        "color": "C8C8C8"}))])
