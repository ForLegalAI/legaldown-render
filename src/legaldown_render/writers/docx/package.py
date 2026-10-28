"""The Open Packaging Conventions container: parts, relationships, content types
and the ZIP they are written to (ISO/IEC 29500-2).

Everything here is deterministic: parts are written in the order they were added
(after ``[Content_Types].xml`` and ``_rels/.rels``), relationship ids are numbered
in the order the relationships were added, and the ZIP entries have a fixed time,
attributes and compression, so the same document is the same bytes.
"""
from __future__ import annotations

import io
import re
import zipfile
from urllib.parse import quote

from ...errors import InternalError
from ...urls import LINK_SCHEMES, is_link_target, url_scheme
from .ooxml import CONTENT_TYPES, PACKAGE_RELATIONSHIPS, _t, flat_part

#: Relationship types (ISO/IEC 29500-1, 11.3 and 15.2).
REL_OFFICE_DOCUMENT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
REL_CORE_PROPERTIES = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
REL_EXTENDED_PROPERTIES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties"
REL_STYLES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
REL_NUMBERING = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering"
REL_SETTINGS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings"
REL_HEADER = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"
REL_FOOTER = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer"
REL_HYPERLINK = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"

#: Content types of the parts this writer produces.
CT_RELATIONSHIPS = "application/vnd.openxmlformats-package.relationships+xml"
CT_XML = "application/xml"
CT_DOCUMENT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
CT_STYLES = "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"
CT_NUMBERING = "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"
CT_SETTINGS = "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"
CT_HEADER = "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"
CT_FOOTER = "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"
CT_CORE_PROPERTIES = "application/vnd.openxmlformats-package.core-properties+xml"
CT_EXTENDED_PROPERTIES = "application/vnd.openxmlformats-officedocument.extended-properties+xml"

#: What may stand in a URI reference unescaped: RFC 3986's unreserved and reserved
#: characters, and ``%`` (an escape already there stays one).
_URI_SAFE = "!#$&'()*+,/:;=?@[]%-._~"
_URI_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f-\x9f\ud800-\udfff]")


def external_target(url: str) -> str | None:
    """*url* as the Target of an external relationship, which must be a valid URI
    reference: what a URI cannot hold (a space, a quotation mark, ``<``, ``>``, a
    backslash, a non-ASCII letter) is percent-encoded as UTF-8, an escape already
    in it is kept. None when it cannot be made one: it is empty, or holds a control
    character or a lone surrogate, which no encoding makes safe to hand to Word."""
    url = url.strip()
    if not url or _URI_FORBIDDEN.search(url):
        return None
    return quote(url, safe=_URI_SAFE)


class Relationships:
    """The relationships of one part, with ids ``rId1``, ``rId2``, ... in the order they were added."""

    def __init__(self) -> None:
        self._items: list[tuple[str, str, bool]] = []
        self._ids: dict[tuple[str, str, bool], str] = {}

    def add(self, relationship_type: str, target: str, *, external: bool = False) -> str:
        """The id of the relationship to *target* (a part name relative to the owning
        part, or with *external* an address, see :func:`external_target`), added if
        it is not there yet."""
        key = (relationship_type, target, external)
        if key not in self._ids:
            self._items.append(key)
            self._ids[key] = f"rId{len(self._items)}"
        return self._ids[key]

    def add_external(self, url: str) -> str | None:
        """The id of a hyperlink relationship to *url*, or None when *url* is not one the
        document may link to: the caller then shows the link's text alone. It is the
        HTML writer's rule (:func:`~legaldown_render.urls.is_link_target`) and more, as
        Word, unlike a browser, follows what a relationship says without asking: the URL
        needs a scheme from :data:`~legaldown_render.urls.LINK_SCHEMES` (no relative path,
        and no ``//host``), has no backslash, and can be written as a URI
        (:func:`external_target`)."""
        stripped = url.strip()
        scheme = url_scheme(stripped)
        if scheme is None or scheme not in LINK_SCHEMES or not is_link_target(stripped) \
                or stripped.startswith("//") or "\\" in stripped:
            return None
        target = external_target(stripped)
        return None if target is None else self.add(REL_HYPERLINK, target, external=True)

    def __bool__(self) -> bool:
        return bool(self._items)

    def to_xml(self) -> bytes:
        return flat_part(PACKAGE_RELATIONSHIPS, "Relationships", [
            ("Relationship", {"Id": f"rId{index}", "Type": relationship_type, "Target": target,
                              "TargetMode": "External" if external else None}, None)
            for index, (relationship_type, target, external) in enumerate(self._items, 1)])


class Package:
    """A DOCX package under construction: parts by name (``/word/document.xml``
    without the slash), their relationships, and the ZIP they become."""

    def __init__(self) -> None:
        self._parts: dict[str, tuple[str, bytes]] = {}
        #: The package's own relationships (``_rels/.rels``): the main document and the properties.
        self.relationships = Relationships()
        self._part_relationships: dict[str, Relationships] = {}

    def add(self, name: str, content_type: str, data: bytes, relationships: Relationships | None = None) -> None:
        """Add the part *name* (such as ``word/styles.xml``) with its *content_type*, and
        the *relationships* it is the source of, if it has any."""
        if name in self._parts:
            raise InternalError(f"the part {name} is added twice (a bug in the DOCX writer)")
        self._parts[name] = (content_type, data)
        if relationships:
            self._part_relationships[name] = relationships

    def _content_types(self) -> bytes:
        return flat_part(CONTENT_TYPES, "Types", [
            ("Default", {"Extension": "rels", "ContentType": CT_RELATIONSHIPS}, None),
            ("Default", {"Extension": "xml", "ContentType": CT_XML}, None),
            *(("Override", {"PartName": f"/{name}", "ContentType": content_type}, None)
              for name, (content_type, _) in self._parts.items())])

    def to_bytes(self) -> bytes:
        """The package as a ZIP: content types first, then the package relationships, then
        every part with its own relationships beside it (``word/_rels/document.xml.rels``)."""
        entries: list[tuple[str, bytes]] = [("[Content_Types].xml", self._content_types()),
                                            ("_rels/.rels", self.relationships.to_xml())]
        for name, (_, data) in self._parts.items():
            entries.append((name, data))
            if name in self._part_relationships:
                folder, _, base = name.rpartition("/")
                entries.append((f"{folder}/_rels/{base}.rels" if folder else f"_rels/{base}.rels",
                                self._part_relationships[name].to_xml()))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in entries:
                # A fixed date, system and mode; the compression the same on every platform.
                info = zipfile.ZipInfo(_t(name), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data, compresslevel=6)
        return buffer.getvalue()
