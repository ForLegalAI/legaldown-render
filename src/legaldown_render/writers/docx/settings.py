"""``word/settings.xml`` and ``word/numbering.xml``."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable

from .ooxml import el, serialize, val

#: ``w:compat``'s mode 15 is Word 2013 and later; without it Word opens the file in Compatibility Mode.
COMPATIBILITY_MODE = 15


def build_settings(*, update_fields: bool) -> bytes:
    """``word/settings.xml``. *update_fields* writes ``w:updateFields``, so that Word
    updates every field (cross-references, the table of contents and its page
    numbers) when it opens the file, after asking once.

    The children are in the order CT_Settings requires: ``updateFields`` comes
    after ``characterSpacingControl`` (and ``alwaysMergeEmptyNamespace``) and before
    ``compat``. There are no revision save ids (``w:rsids``): they would make two
    renders of one document differ and say nothing."""
    return serialize(el(
        "settings", None,
        val("defaultTabStop", 720),
        val("characterSpacingControl", "doNotCompress"),
        update_fields and val("updateFields", "true"),
        el("compat", None, el("compatSetting", {
            "name": "compatibilityMode", "uri": "http://schemas.microsoft.com/office/word",
            "val": COMPATIBILITY_MODE})),
    ))


def build_numbering(abstracts: Iterable[ET.Element] = (), nums: Iterable[ET.Element] = ()) -> bytes:
    """``word/numbering.xml`` holding the ``w:abstractNum`` definitions, then the ``w:num``
    instances that CT_Numbering requires in that order. With none it is an
    empty part: valid, and ready for the numbering definitions of headings, lists and paragraphs."""
    return serialize(el("numbering", None, abstracts, nums))
