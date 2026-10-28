"""Bookmark names for the DOCX plan (docs/decisions/0008, §6.3).

Word reaches a paragraph by a bookmark: a ``REF`` field, an internal link and a table of contents entry all name one.
The planner gives every anchor of the tree a bookmark name; the writer allocates the ids (``writers/docx/ooxml.py``
checks that a name is valid, at most 40 characters and unique ignoring case, which is how Word compares them).

* A name is a prefix for what it marks and the anchor: ``ld_`` for a section, list item or paragraph, ``lddef_`` for a
  defined term, ``ldatt_`` for an attachment, with ``-`` and ``:`` written as ``_`` (an identifier holds only
  letters, digits and ``-``, so nothing else is lost). It is used only if it is a valid name of at most 40
  characters, nothing but the dashes was changed, and no name taken before is the same ignoring case.
* Otherwise, and for a unit a field needs that has no anchor, the name is hidden: ``_Ld<k>``, ``k`` counting from 1 in
  the order the names are given out. Word does not list names that start with ``_`` in its Bookmark dialog.
* The table of contents uses ``_Toc`` and nine digits, from 100000001, as Word does.
"""
from __future__ import annotations

import re

#: The longest bookmark name Word accepts, in characters.
MAX_LENGTH = 40

_LOSSLESS = re.compile(r"[A-Za-z0-9_:\-]+")
_VALID = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: The name of the attachments heading (the anchor ``ld:attachments``).
ATTACHMENTS_HEADING = "_LdAttachments"


class BookmarkNames:
    """Hands out bookmark names, each unique ignoring case. Give names in a fixed order and the result is the same
    on every run."""

    def __init__(self) -> None:
        self._taken: set[str] = set()
        self._hidden = 0
        self._toc = 100000000

    def _take(self, name: str) -> str | None:
        if len(name) > MAX_LENGTH or not _VALID.fullmatch(name) or name.lower() in self._taken:
            return None
        self._taken.add(name.lower())
        return name

    def anchor(self, prefix: str, anchor: str) -> str:
        """The name for *anchor* with *prefix* (``ld_``, ``lddef_``, ``ldatt_``), or a hidden one when the anchor
        cannot be written as one."""
        name = self._take(prefix + re.sub(r"[-:]", "_", anchor)) if _LOSSLESS.fullmatch(anchor) else None
        return name if name is not None else self.hidden()

    def fixed(self, name: str) -> str:
        """*name*, a name of the renderer's own (taken so that no other can be the same)."""
        taken = self._take(name)
        assert taken is not None, name
        return taken

    def hidden(self) -> str:
        """The next hidden name, ``_Ld1``, ``_Ld2``, ..."""
        self._hidden += 1
        name = self._take(f"_Ld{self._hidden}")
        assert name is not None
        return name

    def toc(self) -> str:
        """The next name for a table of contents entry, ``_Toc100000001``, ..."""
        self._toc += 1
        name = self._take(f"_Toc{self._toc}")
        assert name is not None
        return name
