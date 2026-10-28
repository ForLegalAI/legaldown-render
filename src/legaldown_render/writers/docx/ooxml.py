"""Building blocks for WordprocessingML: namespaces, the sanitiser, element
builders, fields, bookmarks and hyperlinks.

Everything in a DOCX goes through :func:`el`, and :func:`el` passes every
attribute value and text through :func:`_t`. That is the one place that makes
text from a document, a style or a command line safe for XML (docs/architecture.md,
Security), so a builder cannot forget it: ``xml.etree`` alone would write
``\\x01`` raw and a lone surrogate as ``&#55296;``, neither of which is XML,
and Word then reports unreadable content.

The builders place children in the order the schema requires
(:func:`ppr`, :func:`rpr`), so callers pass properties in any order and the
output still validates (ISO/IEC 29500-4, CT_PPr and CT_RPr are sequences).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from ...errors import InternalError

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML = "http://www.w3.org/XML/1998/namespace"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC = "http://purl.org/dc/elements/1.1/"
DCTERMS = "http://purl.org/dc/terms/"
DCMITYPE = "http://purl.org/dc/dcmitype/"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
PACKAGE_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
EXTENDED_PROPERTIES = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"

_PREFIXES = {"w": W, "r": R, "cp": CP, "dc": DC, "dcterms": DCTERMS, "dcmitype": DCMITYPE, "xsi": XSI}
for _prefix, _uri in _PREFIXES.items():
    ET.register_namespace(_prefix, _uri)

#: Characters XML 1.0 cannot carry (C0 but TAB, LF and CR), and the ones Word
#: refuses or reads as something else: DEL and C1 (NEL included), the
#: noncharacters U+FFFE and U+FFFF, and lone surrogates (which cannot be encoded as UTF-8).
_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff\ufffe\uffff]")
_BREAKS = re.compile(r"\r\n|[\r\n]")
_WHITESPACE = re.compile(r"[\t\r\n]")


def _t(value: str, *, breaks: bool = False) -> str:
    """*value* made safe to write: the characters in :data:`_INVALID` are dropped.
    TAB, LF and CR are kept only with *breaks*, for text that is split on them
    (:func:`text_run` turns them into ``w:tab`` and ``w:br``); anywhere else (an
    attribute, an instruction, a relationship) they become a space, as an XML
    parser would read them in an attribute anyway."""
    value = _INVALID.sub("", value)
    return value if breaks else _WHITESPACE.sub(" ", value)


def _qname(name: str) -> str:
    """``{namespace}local`` for *name*: a name already in that form, ``prefix:local``
    for the prefixes used here, or a bare WordprocessingML name."""
    if name.startswith("{"):
        return name
    prefix, colon, local = name.partition(":")
    if not colon:
        return f"{{{W}}}{name}"
    namespaces = {**_PREFIXES, "xml": XML}
    if prefix not in namespaces:
        raise InternalError(f"unknown XML prefix '{prefix}' (a bug in the DOCX writer)")
    return f"{{{namespaces[prefix]}}}{local}"


def el(name: str, attrs: Mapping[str, Any] | None = None, *children: Any, text: str | None = None) -> ET.Element:
    """An element named *name* (see :func:`_qname`: bare names are
    WordprocessingML's) with *attrs* (a ``None`` value leaves the attribute out; an
    ``int`` is written as is, and a ``bool`` as ``1``/``0``), *text*, and *children*.
    A child may be an element, ``None`` or ``False`` (left out), or an iterable of those, so
    optional parts and lists need no ``if``. Every string is passed through
    :func:`_t`."""
    element = ET.Element(_qname(name))
    for key, value in (attrs or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = "1" if value else "0"
        element.set(_qname(key), _t(str(value)))
    if text is not None:
        element.text = _t(text)
    element.extend(_flatten(children))
    return element


def _flatten(children: Iterable[Any]) -> list[ET.Element]:
    """The elements in *children*, which may nest iterables and hold ``None`` and
    ``False`` (so ``condition and element`` can be a child)."""
    flat: list[ET.Element] = []
    for child in children:
        if child is None or child is False:
            continue
        if isinstance(child, ET.Element):
            flat.append(child)
        else:
            flat.extend(_flatten(child))
    return flat


def val(name: str, value: Any = None) -> ET.Element:
    """``<w:name w:val="value"/>``, or ``<w:name/>`` without a *value*: the shape
    of most properties."""
    return el(name, {"val": value})


_DECLARATION = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def serialize(root: ET.Element) -> bytes:
    """*root* as a UTF-8 XML part with the declaration Word writes. Attributes keep
    the order they were set in, and namespace declarations are sorted by prefix,
    so the same tree is always the same bytes."""
    return _DECLARATION + ET.tostring(root, encoding="utf-8")


def flat_part(namespace: str, root: str, children: Iterable[tuple[str, Mapping[str, Any], str | None]]) -> bytes:
    """A part whose elements are all in one *namespace*, written unprefixed with no
    namespace on attributes (the package's content types and relationships, and the
    extended properties), as ``<root xmlns=...>`` and one element per
    ``(name, attributes, text)`` in *children*. ``xml.etree`` cannot write a default
    namespace together with unqualified attributes, so this writes the text itself,
    escaping every value (:func:`_t`, then XML's own escapes)."""
    def attributes(values: Mapping[str, Any]) -> str:
        return "".join(f" {name}={quoteattr(_t(str(value)))}" for name, value in values.items() if value is not None)

    lines = []
    for name, values, text in children:
        lines.append(f"<{name}{attributes(values)}/>" if text is None
                     else f"<{name}{attributes(values)}>{escape(_t(text))}</{name}>")
    body = "".join(lines)
    part = f"<{root} xmlns={quoteattr(namespace)}>{body}</{root}>" if body else f"<{root} xmlns={quoteattr(namespace)}/>"
    return _DECLARATION + part.encode("utf-8")


# ---------------------------------------------------------------------------
# Properties, in schema order
# ---------------------------------------------------------------------------

#: CT_PPrBase, then the elements CT_PPr adds after it (ISO/IEC 29500-1, 17.3.1.26).
_PPR_ORDER = (
    "pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl", "numPr",
    "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap",
    "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd", "snapToGrid",
    "spacing", "ind", "contextualSpacing", "mirrorIndents", "suppressOverlap", "jc", "textDirection",
    "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle", "rPr", "sectPr", "pPrChange",
)

#: EG_RPrBase (17.3.2.28).
_RPR_ORDER = (
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline",
    "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color", "spacing", "w",
    "kern", "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText", "vertAlign",
    "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath",
)

#: CT_TblPrBase (17.4.60).
_TBLPR_ORDER = (
    "tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize", "tblStyleColBandSize", "tblW",
    "jc", "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout", "tblCellMar", "tblLook",
    "tblCaption", "tblDescription",
)

#: CT_TcPr (17.4.70).
_TCPR_ORDER = (
    "cnfStyle", "tcW", "gridSpan", "hMerge", "vMerge", "tcBorders", "shd", "noWrap", "tcMar", "textDirection",
    "tcFitText", "vAlign", "hideMark",
)

#: CT_TrPr: a choice, in any order (17.4.82), kept in this order for stable output.
_TRPR_ORDER = ("cnfStyle", "divId", "gridBefore", "gridAfter", "wBefore", "wAfter", "cantSplit", "trHeight",
               "tblHeader", "tblCellSpacing", "jc", "hidden")


def _ordered(name: str, order: tuple[str, ...], children: Iterable[Any]) -> ET.Element | None:
    """The element *name* holding *children* sorted into *order* (by local name;
    an unknown child is a bug), or None when there are none."""
    flat = _flatten(children)
    if not flat:
        return None
    rank = {local: index for index, local in enumerate(order)}
    for child in flat:
        local = child.tag.rpartition("}")[2]
        if local not in rank:
            raise InternalError(f"'{local}' is not a child of w:{name} (a bug in the DOCX writer)")
    flat.sort(key=lambda child: rank[child.tag.rpartition("}")[2]])  # stable: equal names keep their order
    return el(name, None, flat)


def ppr(*children: Any) -> ET.Element | None:
    """``w:pPr`` with *children* in schema order, or None when there are none."""
    return _ordered("pPr", _PPR_ORDER, children)


def rpr(*children: Any) -> ET.Element | None:
    """``w:rPr`` with *children* in schema order, or None when there are none."""
    return _ordered("rPr", _RPR_ORDER, children)


def tbl_pr(*children: Any) -> ET.Element | None:
    """``w:tblPr`` with *children* in schema order, or None when there are none."""
    return _ordered("tblPr", _TBLPR_ORDER, children)


def tc_pr(*children: Any) -> ET.Element | None:
    """``w:tcPr`` with *children* in schema order, or None when there are none."""
    return _ordered("tcPr", _TCPR_ORDER, children)


def tr_pr(*children: Any) -> ET.Element | None:
    """``w:trPr`` with *children* in a fixed order, or None when there are none."""
    return _ordered("trPr", _TRPR_ORDER, children)


# ---------------------------------------------------------------------------
# Paragraphs and runs
# ---------------------------------------------------------------------------


def p(*content: Any, style: str | None = None, props: Iterable[Any] = ()) -> ET.Element:
    """A paragraph: *style* (a style id) and *props* (``w:pPr`` children, any
    order) first, then *content* (runs, fields, bookmarks, hyperlinks)."""
    return el("p", None, ppr(style and val("pStyle", style), props), content)


def r(*content: Any, style: str | None = None, props: Iterable[Any] = ()) -> ET.Element:
    """A run: *style* (a character style id) and *props* (``w:rPr`` children,
    any order) first, then *content* (``w:t``, ``w:tab``, ``w:br``, field characters)."""
    return el("r", None, rpr(style and val("rStyle", style), props), content)


def text_run(text: str, *, style: str | None = None, props: Iterable[Any] = ()) -> ET.Element:
    """A run showing *text*. TAB becomes ``w:tab`` and a line break (LF, CR, or CRLF)
    ``w:br``, because neither can live in ``w:t``; everything else is
    sanitised (:func:`_t`) and kept, spaces included (``xml:space="preserve"``)."""
    content: list[ET.Element] = []
    for line_index, line in enumerate(_BREAKS.split(_t(text, breaks=True))):
        if line_index:
            content.append(el("br"))
        for tab_index, piece in enumerate(line.split("\t")):
            if tab_index:
                content.append(el("tab"))
            if piece:
                content.append(el("t", {"xml:space": "preserve"}, text=piece))
    return r(content, style=style, props=props)


def complex_field(instruction: str, result: Iterable[ET.Element] = (), *, props: Iterable[Any] = (),
                  dirty: bool = False) -> list[ET.Element]:
    """The runs of a complex field: ``begin``, the instruction, ``separate``,
    the cached *result* runs, ``end``. The instruction is what Word evaluates
    (``PAGE``, ``REF bookmark \\n \\h``); the result is what the renderer
    computed for it, shown until Word updates the field. *props* format the
    field's own runs; *dirty* asks Word to update this one field when the file
    opens."""
    def char(kind: str, **extra: Any) -> ET.Element:
        return r(el("fldChar", {"fldCharType": kind, **extra}), props=props)

    instr = r(el("instrText", {"xml:space": "preserve"}, text=f" {_t(instruction).strip()} "), props=props)
    return [char("begin", dirty=True if dirty else None), instr, char("separate"), *result, char("end")]


def hyperlink_external(relationship_id: str, *runs: ET.Element, tooltip: str | None = None) -> ET.Element:
    """A hyperlink to an address, by the id :meth:`~.package.Relationships.add_external` gave it."""
    return el("hyperlink", {"r:id": relationship_id, "tooltip": tooltip, "history": True}, runs)


def hyperlink_internal(bookmark: str, *runs: ET.Element, tooltip: str | None = None) -> ET.Element:
    """A hyperlink to a bookmark in the document."""
    return el("hyperlink", {"anchor": bookmark, "tooltip": tooltip, "history": True}, runs)


# ---------------------------------------------------------------------------
# Bookmarks
# ---------------------------------------------------------------------------

#: A bookmark name: a letter (or ``_`` for a hidden one), then letters, digits and ``_``,
#: at most 40 characters (Word's limit).
_BOOKMARK_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")


class Bookmarks:
    """Allocates bookmark ids, which must be unique across the document body, the
    headers and the footers, and checks that names are valid and unique (Word
    compares names without regard to case). One instance serves a whole package."""

    def __init__(self) -> None:
        self._next = 1
        self._names: set[str] = set()

    def pair(self, name: str) -> tuple[ET.Element, ET.Element]:
        """The ``w:bookmarkStart`` and ``w:bookmarkEnd`` of a new bookmark *name*."""
        if not _BOOKMARK_RE.match(name):
            raise InternalError("a bookmark name is not valid (a bug in the DOCX writer)")
        if name.lower() in self._names:
            raise InternalError("a bookmark name is used twice (a bug in the DOCX writer)")
        self._names.add(name.lower())
        identifier = self._next
        self._next += 1
        return el("bookmarkStart", {"id": identifier, "name": name}), el("bookmarkEnd", {"id": identifier})

    def around(self, name: str, *content: Any) -> list[ET.Element]:
        """*content* between a new bookmark's start and end, as a list for a paragraph."""
        start, end = self.pair(name)
        return [start, *_flatten(content), end]
