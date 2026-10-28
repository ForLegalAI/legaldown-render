"""A plan with no numbering, for as long as the DOCX planner is not wired to the writer.

**Temporary.** :func:`_unnumbered_plan` builds a valid :class:`~legaldown_render.resolve.docx_plan.DocxPlan`
that has no numbering definitions, no bookmarks and no reference fields: every unit is unnumbered and
every cross-reference shows its text, so the document reads in the right order but carries no numbers
(the ``render-docx-interim`` warning says so). A list item is indented by its depth, so that the
structure of nested lists shows. The planner (``resolve/docx_plan.py``)
replaces it: it is the only place with the Word numbering rules (docs/decisions/0003).
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Literal

from ...resolve.docx_plan import DocxPlan, UnitPlan
from ...tree import Block, DraftingNote, List, Paragraph, Quote, RenderTree

#: The left indent of one level of nesting, in twips (1 cm).
_STEP = 567


def _unit(unit: str, kind: Literal["section", "item", "paragraph"], ind: tuple[int, int] | None = None) -> UnitPlan:
    return UnitPlan(unit=unit, kind=kind, num_id=None, ilvl=None, explicit=False, ind=ind, alt=None, label="", word_ref="", count=None, cause=(), bookmarks=(), toc_bookmark=None)


def _units(blocks: Iterable[Block]) -> Iterator[UnitPlan]:
    for block in blocks:
        match block:
            case Paragraph(unit=unit) if unit:
                yield _unit(unit, "paragraph")
            case List(items=items, depth=depth):
                for item in items:
                    if item.unit:
                        yield _unit(item.unit, "item", ((depth + 1) * _STEP, 0))
                    yield from _units(item.blocks)
            case Quote(blocks=inner) | DraftingNote(blocks=inner):
                yield from _units(inner)


def _unnumbered_plan(tree: RenderTree) -> DocxPlan:
    """A :class:`DocxPlan` for *tree* with no numbering: see the module docstring."""
    units = [*_units(tree.preamble)]
    for section in tree.sections:
        if section.unit:
            units.append(_unit(section.unit, "section"))
        units += _units(section.blocks)
    return DocxPlan(abstract_nums=(), nums=(), heading_num_id=None, units={unit.unit: unit for unit in units})
