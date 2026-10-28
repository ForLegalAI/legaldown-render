"""Running headers and footers (``page.header``, ``page.footer``).

Each is one paragraph in a ``w:hdr`` or ``w:ftr`` part. Its left, centre and right
templates are laid out with tab stops at the middle and at the right margin; a
paragraph with only one of them is simply aligned. ``{page}`` and ``{pages}`` are
``PAGE`` and ``NUMPAGES`` fields, which Word and LibreOffice evaluate while
laying the pages out (their cached result is a placeholder); ``{title}``,
``{version}`` and ``{date}`` are the document's own text, written as text.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping

from ...style import PageText
from .ooxml import complex_field, el, p, serialize, text_run
from .styles import FOOTER, HEADER

#: ``{name}`` as the style loader reads a field (docs/style-templates.md).
_FIELD_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: The page-number fields, by the template field that stands for them.
_FIELDS = {"page": "PAGE", "pages": "NUMPAGES"}


def template_runs(template: str, values: Mapping[str, str]) -> list[ET.Element]:
    """The runs of *template*: its text, ``{page}`` and ``{pages}`` as fields
    (cached as 1), and any other field written as its text from *values*. An
    identifier in braces that *values* does not name stays as written; the loader
    refuses such a template, so this is for a style built by hand."""
    runs: list[ET.Element] = []
    position = 0
    for match in _FIELD_RE.finditer(template):
        if match.start() > position:
            runs.append(text_run(template[position:match.start()]))
        name = match.group(1)
        if name in _FIELDS:
            runs += complex_field(_FIELDS[name], [text_run("1")])
        else:
            runs.append(text_run(values.get(name, match.group(0))))
        position = match.end()
    if position < len(template):
        runs.append(text_run(template[position:]))
    return runs


def page_text_paragraph(text: PageText, values: Mapping[str, str], text_width: int, style: str) -> ET.Element:
    """The paragraph for *text*: left, centre and right templates separated by tabs,
    with tab stops at the middle and the right margin (*text_width* twips from the
    left one). A single template is aligned instead, so it needs no tabs."""
    left, center, right = (template_runs(template, values) for template in (text.left, text.center, text.right))
    if not text.left and bool(text.center) != bool(text.right):
        return p(left or center or right, style=style,
                 props=[el("jc", {"val": "center" if text.center else "right"})])
    content: list[ET.Element] = [*left]
    tabs: list[ET.Element] = []
    if text.center:
        content += [el("r", None, el("tab")), *center]
        tabs.append(el("tab", {"val": "center", "pos": text_width // 2}))
    if text.right:
        content += [el("r", None, el("tab")), *right]
        tabs.append(el("tab", {"val": "right", "pos": text_width}))
    return p(content, style=style, props=[tabs and el("tabs", None, tabs)])


def build_header(text: PageText, values: Mapping[str, str], text_width: int) -> bytes:
    """A ``w:hdr`` part showing *text*."""
    return serialize(el("hdr", None, page_text_paragraph(text, values, text_width, HEADER)))


def build_footer(text: PageText, values: Mapping[str, str], text_width: int) -> bytes:
    """A ``w:ftr`` part showing *text*."""
    return serialize(el("ftr", None, page_text_paragraph(text, values, text_width, FOOTER)))
