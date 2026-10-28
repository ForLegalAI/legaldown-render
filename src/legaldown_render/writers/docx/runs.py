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
from ...urls import displayed_url, is_link_target
from .ooxml import _BOOKMARK_RE, Bookmarks, complex_field, el, hyperlink_external, hyperlink_internal, r, text_run, val
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
    that enclosing ``*``/``**`` add. *link_color* is set inside a link, where a run has one character
    style and it is not ``Hyperlink``: the run then carries the link's colour and underline directly."""

    style: str | None = None
    bold: bool = False
    italic: bool = False
    link_color: str | None = None

    @property
    def props(self) -> list[Any]:
        """The run properties (``w:rPr`` children, in any order, :func:`~.ooxml.rpr` sorts them)."""
        linked = self.link_color is not None and self.style != HYPERLINK
        return [val("rStyle", self.style) if self.style else None, self.bold and [el("b"), el("bCs")],
                self.italic and [el("i"), el("iCs")], linked and el("color", {"val": self.link_color}),
                linked and el("u", {"val": "single"})]

    def styled(self, style: str) -> Format:
        return replace(self, style=style)


#: The longest screen tip of a hyperlink, in characters (Word's limit).
_TOOLTIP = 255

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
    make stays text); *bookmarks* allocates the ids of the bookmarks written here; *placed* is the
    lower-cased names of the bookmarks the document writes, so that no field or link points at one that is
    not there (None: not known yet, as in the dry run that finds them out, :meth:`.body.BodyWriter.body`);
    *attachment_titles* maps an attachment's anchor to its title as text; *link_color* is the colour of a
    hyperlink (``w:color``), which runs inside a link carry when their own style is not ``Hyperlink``."""

    def __init__(self, plan: DocxPlan, relationships: Relationships, bookmarks: Bookmarks,
                 placed: Collection[str] | None, attachment_titles: Mapping[str, str], link_color: str) -> None:
        self.plan = plan
        self.relationships = relationships
        self.bookmarks = bookmarks
        self.placed = placed
        self.attachment_titles = attachment_titles
        self.link_color = link_color

    def _is_placed(self, name: str) -> bool:
        return self.placed is None or name.lower() in self.placed

    def _reference_target(self, name: str) -> str:
        """*name*, a bookmark a field points at: it must be a valid name and written by the document, or the plan
        is wrong (Word would show "Error! Reference source not found")."""
        if not _BOOKMARK_RE.fullmatch(name):
            raise InternalError("a reference field names a bookmark that is not a valid name (a bug in the DOCX plan)")
        if not self._is_placed(name):
            raise InternalError(f"a reference field points at the bookmark '{name}', which the document does not "
                                "contain (a bug in the DOCX plan)")
        return name

    # -- plan segments --------------------------------------------------------------------

    def segments(self, segments: Iterable[Segment], fmt: Format = PLAIN) -> list[ET.Element]:
        """The runs of *segments*: literal text, and each ``REF`` field with its cached result.
        A field whose bookmark the document does not write, or that is not a bookmark name, is a bug in the plan."""
        runs: list[ET.Element] = []
        for segment in segments:
            if isinstance(segment, Literal_):
                if segment.text:
                    runs.append(text_run(segment.text, props=fmt.props))
                continue
            self._reference_target(segment.bookmark)
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
            case _:
                raise InternalError(f"a {type(inline).__name__} reached the DOCX writer, which does not write it "
                                    "(a bug: the tree is not resolved)")

    def _internal(self, anchor: str) -> str | None:
        """The bookmark of the output *anchor*, when the document has it."""
        name = self.plan.anchors.get(anchor)
        return name if name is not None and self._is_placed(name) else None

    def _linked(self, fmt: Format) -> Format:
        """*fmt* inside a link: the ``Hyperlink`` style, and the link's colour for runs that take another style."""
        return replace(fmt, style=HYPERLINK, link_color=self.link_color)

    def _link_runs(self, link: Link, fmt: Format) -> list[ET.Element]:
        """What a link shows (§8.7): its text, or its address when it has none, as HTML does. An image with no
        alt text writes no run, so it shows the address too: a hyperlink is never empty."""
        shown = link.children if has_visible_content(link.children) else ()
        runs = self.runs(shown, fmt, in_link=True)
        return runs or self.runs((Text(displayed_url(link.href)),), fmt, in_link=True)

    def _link(self, link: Link, fmt: Format, in_link: bool) -> list[ET.Element]:
        """A link: external through a relationship, or to a bookmark for ``#anchor`` of this document. A link that
        cannot be made one here shows what HTML shows: its text (address when it has none) when the URL is a link
        at all (§8.7), else its children alone."""
        if in_link:
            return self._link_runs(link, fmt)
        tooltip = plain_text(link.title)[:_TOOLTIP] or None
        if link.href.startswith("#"):
            name = self._internal(link.href[1:])
            if name is not None:
                return [hyperlink_internal(name, *self._link_runs(link, self._linked(fmt)), tooltip=tooltip)]
        else:
            identifier = self.relationships.add_external(link.href)
            if identifier is not None:
                return [hyperlink_external(identifier, *self._link_runs(link, self._linked(fmt)), tooltip=tooltip)]
        if is_link_target(link.href):
            return self._link_runs(link, fmt)
        return self.runs(link.children, fmt)

    def _value(self, value: Value, fmt: Format, in_link: bool) -> list[ET.Element]:
        """A field value as text. An ``attach`` value leads to its attachment: a ``REF`` field when the
        text is the attachment's title (so Word keeps it equal to the title), else a link with the
        text it shows (``label=``)."""
        text = [text_run(value.text, props=fmt.props)]
        href = value.href
        if not href or in_link:
            return text
        linked = text_run(value.text, props=self._linked(fmt).props)
        if href.startswith("#"):
            name = self._internal(href[1:])
            if name is None:
                return text
            if self.attachment_titles.get(href[1:]) == value.text:
                return complex_field(f"REF {self._reference_target(name)} \\h", text, props=fmt.props)
            return [hyperlink_internal(name, linked)]
        identifier = self.relationships.add_external(href)
        return text if identifier is None else [hyperlink_external(identifier, linked)]

    # -- helpers for paragraphs -----------------------------------------------------------

    def wrap(self, names: Sequence[str], content: Sequence[Any]) -> list[ET.Element]:
        """*content* inside nested bookmarks, the first of *names* outermost."""
        pairs = [self.bookmarks.pair(name) for name in names]
        return [*(start for start, _ in pairs), *content, *(end for _, end in reversed(pairs))]
