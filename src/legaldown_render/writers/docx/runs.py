"""Inlines as runs: text, formatting, fields, hyperlinks and bookmarks (§13.3 to §13.5, §8).

:class:`InlineWriter` turns the resolved inlines of the tree into the content of a paragraph, and
the segments of the plan into runs. The plan says what a cross-reference is made of (literal text
and ``REF`` fields with their cached results) and which bookmark every anchor is; the writer
lays that out and decides nothing (docs/decisions/0003). It writes the cached result of a field
exactly as the plan gives it: Word computes the live value when it updates the field.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from ...errors import InternalError
from ...resolve.docx_plan import DocxPlan, FieldSeg, Literal_, Segment
from ...tree import (
    Blank,
    Choice,
    Code,
    CrossRef,
    DefinedTerm,
    Emphasis,
    FailureMarker,
    HardBreak,
    Image,
    Inline,
    Link,
    SoftBreak,
    Strong,
    TermRef,
    Text,
    Value,
    has_visible_content,
    plain_text,
)
from ...urls import displayed_url
from .ooxml import Bookmarks, complex_field, el, hyperlink_external, hyperlink_internal, r, text_run, val
from .package import Relationships
from .styles import (
    BLANK,
    CHOICE,
    DEFINED_TERM,
    FAILURE_MARKER,
    HYPERLINK,
    INLINE_CODE,
    TERM_REFERENCE,
)


@dataclass(frozen=True, slots=True)
class Format:
    """The character formatting of the runs being written: a character style and the emphasis
    that enclosing ``*``/``**`` add."""

    style: str | None = None
    bold: bool = False
    italic: bool = False

    @property
    def props(self) -> list[Any]:
        """The run properties (``w:rPr`` children, in any order, :func:`~.ooxml.rpr` sorts them)."""
        return [val("rStyle", self.style) if self.style else None, self.bold and [el("b"), el("bCs")],
                self.italic and [el("i"), el("iCs")]]

    def styled(self, style: str) -> Format:
        return replace(self, style=style)


#: No character formatting.
PLAIN = Format()


def reference_instruction(segment: FieldSeg) -> str:
    """The instruction of *segment*: ``REF bookmark``, then ``\\n`` (the paragraph number), ``\\t`` (without
    the text around the counter) and ``\\h`` (a link to the bookmark), as the segment says."""
    return " ".join(["REF", segment.bookmark, *(switch for switch, on in (
        ("\\n", segment.number), ("\\t", segment.suppress_text), ("\\h", segment.hyperlink)) if on)])


class InlineWriter:
    """Writes inlines for one document.

    *plan* names the bookmarks and the reference fields; *relationships* receives the
    external hyperlinks (:meth:`~.package.Relationships.add_external`: a link the document may not
    make stays text); *bookmarks* allocates the ids of the bookmarks written here; *placed* holds the
    names of the bookmarks the document writes, so that no field or link points at one that is not
    there; *attachment_titles* maps an attachment's anchor to its title as text."""

    def __init__(self, plan: DocxPlan, relationships: Relationships, bookmarks: Bookmarks, placed: Collection[str],
                 attachment_titles: Mapping[str, str]) -> None:
        self.plan = plan
        self.relationships = relationships
        self.bookmarks = bookmarks
        self.placed = placed
        self.attachment_titles = attachment_titles

    # -- plan segments --------------------------------------------------------------------

    def segments(self, segments: Iterable[Segment], fmt: Format = PLAIN) -> list[ET.Element]:
        """The runs of *segments*: literal text, and each ``REF`` field with its cached result.
        A field whose bookmark the document does not write is a bug in the plan."""
        runs: list[ET.Element] = []
        for segment in segments:
            if isinstance(segment, Literal_):
                if segment.text:
                    runs.append(text_run(segment.text, props=fmt.props))
                continue
            if segment.bookmark not in self.placed:
                raise InternalError(f"a reference field points at the bookmark '{segment.bookmark}', which the "
                                    "document does not contain (a bug in the DOCX plan)")
            runs += complex_field(reference_instruction(segment),
                                  [text_run(segment.cached, props=fmt.props)] if segment.cached else [],
                                  props=fmt.props)
        return runs

    # -- inlines --------------------------------------------------------------------------

    def runs(self, inlines: Iterable[Inline], fmt: Format = PLAIN, *, in_link: bool = False) -> list[ET.Element]:
        """The content of a paragraph (runs, fields, hyperlinks, bookmarks) for *inlines*. *in_link* is
        True inside an external link, where no further link is made: its text stays one link."""
        content: list[ET.Element] = []
        for inline in inlines:
            content += self._inline(inline, fmt, in_link)
        return content

    def _inline(self, inline: Inline, fmt: Format, in_link: bool) -> list[ET.Element]:
        match inline:
            case Text(text=text):
                return [text_run(text, props=fmt.props)]
            case SoftBreak():
                return [text_run(" ", props=fmt.props)]
            case HardBreak():
                return [r(el("br"), props=fmt.props)]
            case Emphasis(children=children):
                return self.runs(children, replace(fmt, italic=True), in_link=in_link)
            case Strong(children=children):
                return self.runs(children, replace(fmt, bold=True), in_link=in_link)
            case Code(text=text):
                return [text_run(text, props=fmt.styled(INLINE_CODE).props)]
            case Link():
                return self._link(inline, fmt, in_link)
            case Image(children=children):
                # Alt text at conformance Level 2: no file is read (§2.3, §17.6).
                alt = plain_text(children)
                return [text_run(alt, props=fmt.props)] if alt else []
            case CrossRef(text=text, target_unit=unit):
                reference = self.plan.refs.get(unit) if unit is not None else None
                if reference is None:
                    return [text_run(text, props=fmt.props)]
                return self.segments(reference.segments, fmt)
            case TermRef(text=text, target=target):
                run = text_run(text, props=fmt.styled(TERM_REFERENCE).props)
                name = self._internal(target) if target is not None and not in_link else None
                return [hyperlink_internal(name, run)] if name else [run]
            case DefinedTerm(children=children, anchor=anchor, defining=defining):
                content = self.runs(children, fmt.styled(DEFINED_TERM), in_link=in_link)
                name = self.plan.anchors.get(anchor) if anchor and defining else None
                return self.bookmarks.around(name, content) if name else content
            case Value():
                return self._value(inline, fmt, in_link)
            case Blank(text=text, prompt=prompt):
                runs = [text_run(text, props=fmt.styled(BLANK).props)]
                if prompt:
                    runs.append(text_run(f" ({prompt})", props=replace(fmt, italic=True).props))
                return runs
            case Choice(options=options, separator=separator):
                return [text_run("[" + separator.join(options) + "]", props=fmt.styled(CHOICE).props)]
            case FailureMarker(text=text):
                return [text_run(text, props=fmt.styled(FAILURE_MARKER).props)]
        return []

    def _internal(self, anchor: str) -> str | None:
        """The bookmark of the output *anchor*, when the document has it."""
        name = self.plan.anchors.get(anchor)
        return name if name in self.placed else None

    def _link(self, link: Link, fmt: Format, in_link: bool) -> list[ET.Element]:
        """A link: external through a relationship, or to a bookmark for ``#anchor`` of this
        document. One that is not a link (§8.7: a scheme links may not use) shows its text alone, as in HTML."""
        # A link with no text shows its address (§8.7: it is still a hyperlink).
        shown = link.children if has_visible_content(link.children) else (Text(displayed_url(link.href)),)
        if in_link:
            return self.runs(shown, fmt, in_link=True)
        tooltip = plain_text(link.title) or None
        if link.href.startswith("#"):
            name = self._internal(link.href[1:])
            if name is None:
                return self.runs(link.children, fmt)
            return [hyperlink_internal(name, *self.runs(shown, fmt.styled(HYPERLINK), in_link=True), tooltip=tooltip)]
        identifier = self.relationships.add_external(link.href)
        if identifier is None:
            return self.runs(link.children, fmt)
        return [hyperlink_external(identifier, *self.runs(shown, fmt.styled(HYPERLINK), in_link=True),
                                   tooltip=tooltip)]

    def _value(self, value: Value, fmt: Format, in_link: bool) -> list[ET.Element]:
        """A field value as text. An ``attach`` value leads to its attachment: a ``REF`` field when the
        text is the attachment's title (so Word keeps it equal to the title), else a link with the
        text it shows (``label=``)."""
        text = [text_run(value.text, props=fmt.props)]
        href = value.href
        if not href or in_link:
            return text
        if href.startswith("#"):
            name = self._internal(href[1:])
            if name is None:
                return text
            if self.attachment_titles.get(href[1:]) == value.text:
                return complex_field(f"REF {name} \\h", text, props=fmt.props)
            return [hyperlink_internal(name, text_run(value.text, props=fmt.styled(HYPERLINK).props))]
        identifier = self.relationships.add_external(href)
        if identifier is None:
            return text
        return [hyperlink_external(identifier, text_run(value.text, props=fmt.styled(HYPERLINK).props))]

    # -- helpers for paragraphs -----------------------------------------------------------

    def wrap(self, names: Sequence[str], content: Sequence[Any]) -> list[ET.Element]:
        """*content* inside nested bookmarks, the first of *names* outermost."""
        pairs = [self.bookmarks.pair(name) for name in names]
        return [*(start for start, _ in pairs), *content, *(end for _, end in reversed(pairs))]
