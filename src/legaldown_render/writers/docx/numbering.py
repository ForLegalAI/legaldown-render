"""``word/numbering.xml`` from the plan: the abstract numbering definitions and their instances.

The planner decides every one of them (:class:`~legaldown_render.resolve.docx_plan.AbstractNum`,
:class:`~legaldown_render.resolve.docx_plan.Num`); this module lays them out, in the order
CT_Numbering, CT_AbstractNum and CT_Lvl require (ISO/IEC 29500-1, 17.9), and decides nothing.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from ...resolve.docx_plan import AbstractNum, DocxPlan, LevelDef, Num
from .ooxml import el, val
from .settings import build_numbering


def level(definition: LevelDef) -> ET.Element:
    """One ``w:lvl``: start, number format, paragraph style, suffix, text, justification and indent, in schema order."""
    return el(
        "lvl", {"ilvl": definition.ilvl},
        val("start", definition.start),
        val("numFmt", definition.num_fmt),
        definition.p_style is not None and val("pStyle", definition.p_style),
        definition.suff == "space" and val("suff", "space"),
        val("lvlText", definition.lvl_text),
        val("lvlJc", "left"),
        (definition.left > 0 or definition.hanging > 0) and el(
            "pPr", None, el("ind", {"left": definition.left, "hanging": definition.hanging or None})),
    )


def abstract_num(abstract: AbstractNum) -> ET.Element:
    """One ``w:abstractNum``: its list identifier, its kind of list and its levels."""
    return el("abstractNum", {"abstractNumId": abstract.id}, val("nsid", abstract.nsid.upper()),
              val("multiLevelType", abstract.multi_level), [level(definition) for definition in abstract.levels])


def num(instance: Num) -> ET.Element:
    """One ``w:num``: the definition it uses and, for each level that starts elsewhere than at the
    level's own start, a ``w:startOverride``."""
    return el("num", {"numId": instance.id}, val("abstractNumId", instance.abstract_id),
              [el("lvlOverride", {"ilvl": ilvl}, val("startOverride", value))
               for ilvl, value in instance.start_overrides])


def numbering_part(plan: DocxPlan) -> bytes:
    """``word/numbering.xml`` for *plan*: its abstract numberings, then its nums, in the plan's order."""
    return build_numbering([abstract_num(abstract) for abstract in plan.abstract_nums],
                           [num(instance) for instance in plan.nums])
