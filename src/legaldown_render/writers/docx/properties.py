"""``docProps/core.xml`` and ``docProps/app.xml``: the document's properties.

They carry the title and the language, and the program that wrote the file. They carry
no date and no author, so that the same document is the same bytes on any day (the
dates of a contract are its own, in its text)."""
from __future__ import annotations

from .ooxml import EXTENDED_PROPERTIES, el, flat_part, serialize


def build_core(title: str, language: str) -> bytes:
    """``docProps/core.xml``: ``dc:title`` (left out when the title is empty) and ``dc:language``."""
    return serialize(el("cp:coreProperties", None,
                        el("dc:title", text=title) if title.strip() else None,
                        el("dc:language", text=language)))


def build_app(application: str) -> bytes:
    """``docProps/app.xml``: the name of the program, as Word shows it under Properties."""
    return flat_part(EXTENDED_PROPERTIES, "Properties", [("Application", {}, application)])
