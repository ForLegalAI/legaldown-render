"""``word/styles.xml``: the Word styles a style template becomes (docs/style-templates.md).

The document body only names these styles (``Heading1``, ``Quote``, ``DraftingNote``,
...), so a lawyer who changes a style in Word changes every paragraph that uses
it. The built-in styles keep the names Word knows them by (``heading 1``,
``toc 1``, ``Title``, ``Hyperlink``, ``Table Grid``), because Word's table of
contents, navigation pane and style gallery find them by name; the ones that are
the renderer's own are custom styles.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from typing import Any

from ...style import Style
from ...style.docx import DocxSettings, HeadingFormat
from .ooxml import el, ppr, rpr, serialize, val

# Style ids. A paragraph or a run names a style by id; Word shows the name.
NORMAL = "Normal"
TITLE = "Title"
SUBTITLE = "Subtitle"
QUOTE = "Quote"
CONTENTS_HEADING = "ContentsHeading"
DRAFTING_NOTE = "DraftingNote"
DRAFTING_NOTE_LABEL = "DraftingNoteLabel"
CODE = "Code"
INLINE_CODE = "InlineCode"
TABLE_GRID = "TableGrid"
HYPERLINK = "Hyperlink"
HEADER = "Header"
FOOTER = "Footer"
DEFINED_TERM = "DefinedTerm"
TERM_REFERENCE = "TermReference"
BLANK = "Blank"
CHOICE = "Choice"
FAILURE_MARKER = "FailureMarker"
CONDITION_LABEL = "ConditionLabel"
TITLE_BLOCK_TEXT = "TitleBlockText"
TITLE_BLOCK_LABEL = "TitleBlockLabel"
PARTY_SIDE = "PartySide"
PARTY_NAME = "PartyName"
ATTACHMENTS_HEADING = "AttachmentsHeading"
ATTACHMENT_TITLE = "AttachmentTitle"
ATTACHMENT_FILE = "AttachmentFile"
SIGNATURE_SIDE = "SignatureSide"
SIGNATURE_TEXT = "SignatureText"
SIGNATURE_LINE = "SignatureLine"


def heading_style(level: int) -> str:
    """The id of the style for heading *level* (1 to 5), named ``heading N``."""
    return f"Heading{level}"


def toc_style(level: int) -> str:
    """The id of the table of contents' style for entries of *level* (1 to 5), named ``toc N``."""
    return f"TOC{level}"


#: The left indent in twips that ``Quote`` and ``DraftingNote`` set, which the body adds to the paragraphs
#: that carry an indent of their own (a list item in a quote).
QUOTE_INDENT = 567
NOTE_INDENT = 227
CODE_INDENT = 113
#: The outline level (0-based) of the attachments heading and of an attachment's title: the levels the
#: contents lists them at, as the HTML contents does (the heading at 1, the titles under it at 2).
ATTACHMENTS_OUTLINE = 0
ATTACHMENT_OUTLINE = 1
#: The space above a signing line, in twips: room to sign in.
SIGNATURE_SPACE = 720

_MUTED = "666666"
_RULE = "C8C8C8"
_MONOSPACE = "Courier New"
#: Two of the HTML writer's marker colours, so the formats look alike.
_FAILURE_FILL, _FAILURE_TEXT = "FDE2E1", "8A1C14"
_CONDITION_FILL, _CONDITION_TEXT = "EFE9FB", "5B3FA0"
_NOTE_FILL, _NOTE_RULE = "FFF8E1", "D9A400"
_CODE_FILL = "F5F5F5"

_LANGUAGE_RE = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8}){0,3}")


def language_tag(*candidates: str | None) -> str:
    """The first of *candidates* that is a language tag (``en``, ``cs-CZ``), else ``en-US``:
    the document's ``language`` is free text to the renderer, and ``w:lang`` is read by Word's proofing."""
    return next((tag for tag in candidates if tag and _LANGUAGE_RE.fullmatch(tag)), "en-US")


def _fonts(name: str) -> ET.Element:
    """The Latin font. The complex-script and East Asian fonts stay unset, for Word to choose by
    script: they are not the Latin font's to give."""
    return el("rFonts", {"ascii": name, "hAnsi": name})


def _size(half_points: int) -> list[ET.Element]:
    return [val("sz", half_points), val("szCs", half_points)]


def _shade(fill: str) -> ET.Element:
    return el("shd", {"val": "clear", "color": "auto", "fill": fill})


def _border(side: str, color: str, size: int = 4, space: int = 0) -> ET.Element:
    return el(side, {"val": "single", "sz": size, "space": space, "color": color})


def container_bar(style_id: str) -> ET.Element | None:
    """The left border that ``Quote`` and ``DraftingNote`` draw, for the paragraphs inside them that are not
    in that style (code, rules), so that the bar runs unbroken; None for any other style."""
    return {QUOTE: lambda: _border("left", _RULE, 18, 10),
            DRAFTING_NOTE: lambda: _border("left", _NOTE_RULE, 24, 8)}.get(style_id, lambda: None)()


def _style(style_id: str, name: str, *, kind: str = "paragraph", based_on: str | None = None,
           next_style: str | None = None, priority: int | None = None, quick: bool = False, custom: bool = False,
           hidden_until_used: bool = False, default: bool = False, props: Iterable[Any] = (),
           run: Iterable[Any] = (), table: ET.Element | None = None, table_conditions: Iterable[Any] = (),
           semi_hidden: bool = False) -> ET.Element:
    """One ``w:style``, its children in the order CT_Style requires."""
    return el(
        "style", {"type": kind, "default": True if default else None, "customStyle": True if custom else None,
                  "styleId": style_id},
        val("name", name),
        based_on and val("basedOn", based_on),
        next_style and val("next", next_style),
        priority is not None and val("uiPriority", priority),
        semi_hidden and el("semiHidden"),
        hidden_until_used and el("unhideWhenUsed"),
        quick and el("qFormat"),
        ppr(props),
        rpr(run),
        table,
        table_conditions,
    )


def _text_style(kind: str) -> list[ET.Element]:
    """The run properties of a ``definitions.style`` or ``term_style`` value."""
    return {
        "bold": [el("b"), el("bCs")],
        "italic": [el("i"), el("iCs")],
        "underline": [el("u", {"val": "single"})],
        "small-caps": [el("smallCaps")],
    }.get(kind, [])


def _heading_run(heading: HeadingFormat | None, settings: DocxSettings) -> list[Any]:
    """The run properties of a heading level (None: a style that sets none: bold at the body size), for the
    paragraph styles that look like a heading and are not one (so that no heading numbering reaches them)."""
    if heading is None:
        return [el("b"), el("bCs"), _size(settings.half_points)]
    return [heading.bold and [el("b"), el("bCs")], heading.italic and [el("i"), el("iCs")],
            heading.caps and el("caps"), heading.small_caps and el("smallCaps"), _size(heading.half_points)]


def build_styles(style: Style, settings: DocxSettings, language: str, *, heading_num_id: int | None = None) -> bytes:
    """``word/styles.xml`` for *style*, converted to Word units as *settings*, in the
    document *language* (``w:lang``). *heading_num_id* links ``heading 1`` to ``heading 5``
    to that numbering definition, one level each (``w:numPr`` in the style, matched by a
    ``w:pStyle`` in the level)."""
    font_points = settings.points

    def em(factor: float) -> int:
        """Twips in *factor* times the body font size."""
        return round(factor * font_points * 20)

    def half_points(factor: float) -> int:
        return max(2, round(factor * settings.half_points))

    text_width = settings.page.text_width
    defaults = el(
        "docDefaults",
        None,
        el("rPrDefault", None, rpr(_fonts(settings.font), _size(settings.half_points),
                                   el("lang", {"val": language}))),
        el("pPrDefault"),
    )

    styles = [
        _style(NORMAL, "Normal", default=True, quick=True,
               props=[el("spacing", {"after": em(0.6), "line": settings.line, "lineRule": settings.line_rule}),
                      el("jc", {"val": "both"}) if settings.justify else None],
               run=[el("color", {"val": settings.color}), _size(settings.half_points)]),
        _style("DefaultParagraphFont", "Default Paragraph Font", kind="character", default=True, priority=1,
               semi_hidden=True, hidden_until_used=True),
        _style("TableNormal", "Normal Table", kind="table", default=True, priority=99, semi_hidden=True,
               hidden_until_used=True,
               table=el("tblPr", None, el("tblInd", {"w": 0, "type": "dxa"}),
                        el("tblCellMar", None, *(el(side, {"w": width, "type": "dxa"})
                                                 for side, width in (("top", 0), ("left", 108),
                                                                     ("bottom", 0), ("right", 108)))))),
        _style("NoList", "No List", kind="numbering", default=True, priority=99, semi_hidden=True,
               hidden_until_used=True),
    ]

    for level in range(1, 6):
        heading = settings.headings.get(level)
        if heading is None:
            continue
        styles.append(_style(
            heading_style(level), f"heading {level}", based_on=NORMAL, next_style=NORMAL, priority=9, quick=True,
            props=[el("keepNext"), el("keepLines"),
                   heading_num_id is not None and el("numPr", None, val("ilvl", level - 1),
                                                     val("numId", heading_num_id)),
                   el("spacing", {"before": em(1.2) if level == 1 else em(0.9), "after": em(0.4)}),
                   el("jc", {"val": heading.align}), val("outlineLvl", level - 1)],
            run=[heading.bold and [el("b"), el("bCs")], heading.italic and [el("i"), el("iCs")],
                 heading.caps and el("caps"), heading.small_caps and el("smallCaps"), _size(heading.half_points)]))

    first = settings.headings.get(1)
    second = settings.headings.get(2) or first
    styles += [
        _style(TITLE, "Title", based_on=NORMAL, next_style=NORMAL, priority=10, quick=True,
               props=[el("keepNext"), el("spacing", {"before": 0, "after": em(0.5)}), el("jc", {"val": "center"})],
               run=[el("b"), el("bCs"), _size(half_points(1.6))]),
        _style(SUBTITLE, "Subtitle", based_on=NORMAL, next_style=NORMAL, priority=11, quick=True,
               props=[el("spacing", {"before": 0, "after": em(1)}), el("jc", {"val": "center"})],
               run=[el("i"), el("iCs"), el("color", {"val": _MUTED}), _size(half_points(1.1))]),
        _style(QUOTE, "Quote", based_on=NORMAL, next_style=NORMAL, priority=29, quick=True,
               props=[el("pBdr", None, _border("left", _RULE, 18, 10)),
                      el("spacing", {"before": em(0.5), "after": em(0.5)}), el("ind", {"left": QUOTE_INDENT, "right": 567})]),
        _style(CONTENTS_HEADING, "Contents Heading", based_on=NORMAL, next_style=NORMAL, priority=39, custom=True,
               props=[el("keepNext"), el("spacing", {"before": em(1.2), "after": em(0.4)}),
                      el("jc", {"val": first.align if first else "left"})],
               run=[(first is None or first.bold) and [el("b"), el("bCs")], first and first.caps and el("caps"),
                    first and first.small_caps and el("smallCaps"),
                    _size(first.half_points if first else settings.half_points)]),
        _style(TITLE_BLOCK_TEXT, "Title Block Text", based_on=NORMAL, priority=41, custom=True,
               props=[el("spacing", {"before": 0, "after": em(0.2)}), el("jc", {"val": "left"})]),
        _style(PARTY_SIDE, "Party Side", based_on=NORMAL, next_style=NORMAL, priority=42, custom=True,
               props=[el("keepNext"), el("spacing", {"before": em(1), "after": em(0.2)}), el("jc", {"val": "left"})],
               run=[el("b"), el("bCs")]),
        _style(PARTY_NAME, "Party Name", based_on=NORMAL, priority=43, custom=True,
               props=[el("keepNext"), el("spacing", {"before": em(0.5), "after": em(0.2)}), el("jc", {"val": "left"})]),
        _style(ATTACHMENTS_HEADING, "Attachments Heading", based_on=NORMAL, next_style=NORMAL, priority=44,
               custom=True, props=[el("keepNext"), el("keepLines"), el("spacing", {"before": em(1.2), "after": em(0.4)}),
                                   el("jc", {"val": first.align if first else "left"}),
                                   val("outlineLvl", ATTACHMENTS_OUTLINE)],
               run=_heading_run(first, settings)),
        _style(ATTACHMENT_TITLE, "Attachment Title", based_on=NORMAL, next_style=NORMAL, priority=45, custom=True,
               props=[el("keepNext"), el("keepLines"), el("spacing", {"before": em(0.9), "after": em(0.4)}),
                      el("jc", {"val": second.align if second else "left"}), val("outlineLvl", ATTACHMENT_OUTLINE)],
               run=_heading_run(second, settings)),
        _style(ATTACHMENT_FILE, "Attachment File", based_on=NORMAL, priority=46, custom=True,
               run=[el("color", {"val": _MUTED})]),
        _style(SIGNATURE_SIDE, "Signature Side", based_on=NORMAL, priority=47, custom=True,
               props=[el("keepLines"), el("spacing", {"before": em(1.5), "after": em(0.2)}), el("jc", {"val": "left"})],
               run=[el("b"), el("bCs")]),
        _style(SIGNATURE_TEXT, "Signature Text", based_on=NORMAL, priority=48, custom=True,
               props=[el("keepLines"), el("spacing", {"before": 0, "after": em(0.2)}), el("jc", {"val": "left"})]),
        _style(SIGNATURE_LINE, "Signature Line", based_on=NORMAL, priority=49, custom=True,
               props=[el("keepLines"), el("pBdr", None, _border("bottom", "auto", 4, 1)),
                      el("spacing", {"before": SIGNATURE_SPACE, "after": em(0.3)})]),
        _style(DRAFTING_NOTE, "Drafting Note", based_on=NORMAL, priority=30, custom=True,
               props=[el("keepLines"), el("pBdr", None, _border("left", _NOTE_RULE, 24, 8)), _shade(_NOTE_FILL),
                      el("spacing", {"before": em(0.4), "after": em(0.4)}), el("ind", {"left": NOTE_INDENT, "right": 113})]),
        _style(CODE, "Code", based_on=NORMAL, priority=31, custom=True,
               props=[_shade(_CODE_FILL), el("spacing", {"before": 0, "after": 0, "line": 240, "lineRule": "auto"}),
                     el("ind", {"left": CODE_INDENT, "right": 113}), el("jc", {"val": "left"})],
               run=[_fonts(_MONOSPACE), _size(half_points(0.9))]),
        _style(TABLE_GRID, "Table Grid", kind="table", based_on="TableNormal", priority=39,
               table=el("tblPr", None, el("tblBorders", None, *(_border(side, _RULE, 4)
                                                                for side in ("top", "left", "bottom", "right",
                                                                             "insideH", "insideV")))),
               table_conditions=el("tblStylePr", {"type": "firstRow"}, rpr([el("b"), el("bCs")]))),
        _style(HEADER, "header", based_on=NORMAL, priority=99, hidden_until_used=True,
               props=[el("spacing", {"after": 0, "line": 240, "lineRule": "auto"})],
               run=[el("color", {"val": _MUTED}), _size(half_points(0.9))]),
        _style(FOOTER, "footer", based_on=NORMAL, priority=99, hidden_until_used=True,
               props=[el("spacing", {"after": 0, "line": 240, "lineRule": "auto"})],
               run=[el("color", {"val": _MUTED}), _size(half_points(0.9))]),
    ]
    for level in range(1, 6):
        styles.append(_style(
            toc_style(level), f"toc {level}", based_on=NORMAL, next_style=NORMAL, priority=39, hidden_until_used=True,
            props=[el("tabs", None, el("tab", {"val": "right", "leader": "dot", "pos": text_width})),
                   el("spacing", {"after": em(0.2)}), el("ind", {"left": 240 * (level - 1)})]))

    styles += [
        _style(HYPERLINK, "Hyperlink", kind="character", priority=99, hidden_until_used=True,
               run=[el("color", {"val": settings.link_color}), el("u", {"val": "single"})]),
        _style(INLINE_CODE, "Inline Code", kind="character", priority=32, custom=True,
               run=[_fonts(_MONOSPACE), _size(half_points(0.9))]),
        _style(DEFINED_TERM, "Defined Term", kind="character", priority=33, custom=True,
               run=_text_style(style.definitions.style)),
        _style(TERM_REFERENCE, "Term Reference", kind="character", priority=34, custom=True,
               run=_text_style(style.definitions.term_style)),
        _style(BLANK, "Blank", kind="character", priority=35, custom=True, run=[el("noProof")]),
        _style(CHOICE, "Choice", kind="character", priority=36, custom=True, run=[_shade(_CONDITION_FILL)]),
        _style(FAILURE_MARKER, "Failure Marker", kind="character", priority=37, custom=True,
               run=[el("color", {"val": _FAILURE_TEXT}), _shade(_FAILURE_FILL)]),
        _style(CONDITION_LABEL, "Condition Label", kind="character", priority=38, custom=True,
               run=[el("color", {"val": _CONDITION_TEXT}), _size(half_points(0.8)), _shade(_CONDITION_FILL)]),
        _style(TITLE_BLOCK_LABEL, "Title Block Label", kind="character", priority=41, custom=True,
               run=[el("i"), el("iCs")]),
        _style(DRAFTING_NOTE_LABEL, "Drafting Note Label", kind="character", priority=40, custom=True,
               run=[el("b"), el("bCs"), _size(half_points(0.9))]),
    ]
    return serialize(el("styles", None, defaults, styles))
