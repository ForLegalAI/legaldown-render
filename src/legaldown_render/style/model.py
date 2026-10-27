"""The style template model (specification §13.7).

A style template holds everything about how a document looks and nothing
about what it says. It is split in two:

* the **semantic** part — numbering, enumeration, references, values,
  labels — drives resolution, so it applies identically to every output
  format;
* the **presentation** part — typography, headings, page, html — is read only
  by writers.

Every field has a default, so the built-in ``default`` template is simply
``Style()``. Templates are loaded from YAML by :mod:`.loader`, which checks
every key and value against these dataclasses.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field, fields, replace
from typing import Any, Literal

Counter = Literal["decimal", "lower-alpha", "upper-alpha", "lower-roman", "upper-roman"]


@dataclass(frozen=True, slots=True)
class LevelFormat:
    """How one level of headings, list items, or paragraphs is numbered.

    ``label`` is what the output shows next to the heading or item;
    ``ref`` is this level's piece of a designation, the text ``{{ref:}}``
    renders (§13.3). Both are templates:

    * ``{n}`` — this level's counter, formatted by ``counter``
    * ``{path}`` — the full designation up to and including this level
    * ``{section}`` — the designation of the containing section (list items
      and paragraphs only)

    For example, decimal headings use ``label: "{path}"`` and ``ref: ".{n}"``
    so that the third subsection of Article 2 shows ``2.3`` and is referenced
    as ``2.3``; legal enumeration uses ``label: "({n})"`` and ``ref: "({n})"``
    so an item is referenced as ``2.3(b)``.
    """

    counter: Counter = "decimal"
    label: str = "{n}."
    ref: str = "{n}"


@dataclass(frozen=True, slots=True)
class Numbering:
    """Section numbering (§13.1)."""

    #: A built-in scheme. ``none`` shows no numbers, and references render
    #: the target's heading text instead (§13.3).
    scheme: Literal["decimal", "legal-outline", "mixed", "none"] = "decimal"
    #: Overrides the scheme's formats by part of a section number: the first
    #: format for "2", the second for the ".1" of "2.1" (at most five, as
    #: headings have five levels, §4.1). Levels not listed keep the
    #: scheme's format.
    levels: tuple[LevelFormat, ...] = ()


@dataclass(frozen=True, slots=True)
class Enumeration:
    """List enumeration (§13.2)."""

    #: False keeps plain bullets; items then have no designation, and a
    #: ``{{ref:}}`` to an item anchor falls back to its section's number with
    #: a ``ref-not-enumerated`` Warning (§6.3).
    enabled: bool = True
    #: Formats by nesting depth, cycling when a list is nested deeper.
    levels: tuple[LevelFormat, ...] = (
        LevelFormat("lower-alpha", "({n})", "({n})"),
        LevelFormat("lower-roman", "({n})", "({n})"),
        LevelFormat("upper-alpha", "({n})", "({n})"),
    )
    #: Ordered lists are always renumbered — numbers in the source are not
    #: authoritative (§13.2). ``renumber`` shows ``1.``, ``2.``, …;
    #: ``enumerate`` applies ``levels`` as for unordered lists.
    ordered: Literal["renumber", "enumerate"] = "renumber"


@dataclass(frozen=True, slots=True)
class Paragraphs:
    """Numbering of the top-level paragraphs in a section (§13.2)."""

    numbered: bool = False
    format: LevelFormat = LevelFormat("decimal", "{section}.{n}", ".{n}")


@dataclass(frozen=True, slots=True)
class References:
    """How ``{{ref:}}`` is displayed (§13.3)."""

    #: ``{designation}`` is the target's number ("4.2", "4.2(b)"), or its
    #: heading text under the ``none`` scheme.
    format: str = "{designation}"


TextStyle = Literal["bold", "italic", "underline", "small-caps", "plain"]


@dataclass(frozen=True, slots=True)
class Definitions:
    """How defined terms look (§7.2). Quotation marks are never rendered."""

    #: The defining occurrence, ``"Services" {{def:}}``.
    style: TextStyle = "bold"
    #: Every ``{{term:}}`` reference.
    term_style: TextStyle = "plain"


@dataclass(frozen=True, slots=True)
class Values:
    """Formatting of field specs (§10, §13.5)."""

    #: ``short``, ``medium``, ``long``, ``full``, or a CLDR date pattern.
    date: str = "long"
    #: ``symbol`` ($10,000.00), ``code`` (USD 10,000.00), or ``name``
    #: (10,000.00 US dollars).
    money: Literal["symbol", "code", "name"] = "symbol"
    #: ``long`` (12 months), ``short`` (12 mths), ``narrow`` (12m).
    duration: Literal["long", "short", "narrow"] = "long"


@dataclass(frozen=True, slots=True)
class Placeholders:
    """How fillable blanks render (§10.7, §13.5)."""

    blank: str = "[_____]"
    #: Show the question's ``prompt`` next to the blank (§15.8).
    show_prompt: bool = False


@dataclass(frozen=True, slots=True)
class TitleBlock:
    """The block under the document title."""

    parties: bool = True
    effective_date: bool = True
    version: bool = False


@dataclass(frozen=True, slots=True)
class Attachments:
    """Attachments rendered after the body (§13.8). At the Rendering level
    their files are not read, so each renders as a titled placeholder."""

    render: Literal["placeholder", "omit"] = "placeholder"
    separator: Literal["page-break", "rule", "none"] = "page-break"


@dataclass(frozen=True, slots=True)
class Contents:
    """A table of contents, after the title block."""

    enabled: bool = False
    #: The deepest heading level listed.
    depth: Literal[1, 2, 3, 4, 5] = 2
    #: List the attachment placeholders after the sections.
    attachments: bool = True


@dataclass(frozen=True, slots=True)
class Signatures:
    """Generated signature blocks (§2.2). The document's
    ``include_signatures: false`` always wins."""

    enabled: bool = True


@dataclass(frozen=True, slots=True)
class TemplateView:
    """How a template renders without answers (§15.8)."""

    #: Between the phrases of a ``{{choose:}}``: ``[a / b]``.
    choice_separator: str = " / "
    #: Shown for an empty phrase (``""``), which otherwise shows nothing.
    empty_choice: str = ""


@dataclass(frozen=True, slots=True)
class Labels:
    """Every word the renderer generates itself. ``None`` takes the value
    from the built-in labels for the document's ``language``, so a Czech
    document gets Czech labels without a style saying so (§13.7)."""

    drafting_note: str | None = None
    #: ``{condition}`` is the condition as written, e.g. ``forum:courts``.
    condition: str | None = None
    identification_number: str | None = None
    date_of_birth: str | None = None
    address: str | None = None
    represented_by: str | None = None
    effective_date: str | None = None
    version: str | None = None
    attachments: str | None = None
    contents: str | None = None
    #: Between a label and its value ("Name: ..."); French puts a no-break
    #: space before the colon.
    colon: str | None = None
    #: ``{file}`` is the attachment's declared file.
    attachment_file: str | None = None
    signatures: str | None = None
    signature_date: str | None = None
    signature_place: str | None = None
    signature_name: str | None = None
    signature_title: str | None = None


# ---------------------------------------------------------------------------
# Values that reach the stylesheet
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CssKind:
    """What a style setting that is written into the HTML stylesheet holds.

    ``grammar`` is the strict grammar an untrusted style is held to
    (docs/style-templates.md, "Untrusted styles"): plain values only, no
    function, no variable, nothing a reader could mistake for more than one
    value. ``expects`` says what it accepts, for the message."""

    grammar: re.Pattern[str]
    expects: str


_NUMBER = r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
_LENGTH = rf"(?:0|{_NUMBER}(?:em|rem|ex|ch|pt|pc|px|mm|cm|in|%))"
_FONT_NAME = r"""(?:"[\w .\-]{1,60}"|'[\w .\-]{1,60}'|-?[A-Za-z][\w\-]{0,40}(?: [\w\-]{1,40}){0,4})"""
_PAPER = r"(?:[Aa][3-5]|[Bb][4-5]|[Ll]etter|[Ll]egal|[Ll]edger)"
_ORIENTATION = r"(?:portrait|landscape)"

CSS_KINDS: dict[str, CssKind] = {
    "length": CssKind(re.compile(_LENGTH), "a length such as 11pt, 1.2em or 0"),
    "line-height": CssKind(re.compile(rf"(?:normal|{_NUMBER}|{_LENGTH})"), "normal, a number such as 1.5, or a length"),
    "color": CssKind(
        re.compile(r"(?:#(?:[0-9A-Fa-f]{3,4}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})|[A-Za-z]{3,24}"
                   r"|(?:rgb|rgba|hsl|hsla)\([0-9.%, /]{1,40}\))"),
        "a colour: #rgb, #rrggbb, a colour name, or rgb()/hsl() with plain numbers"),
    "font-weight": CssKind(re.compile(r"(?:normal|bold|bolder|lighter|[1-9][0-9]{0,2}|1000)"),
                           "normal, bold, or a number from 1 to 1000"),
    "font-family": CssKind(re.compile(rf"{_FONT_NAME}(?:, ?{_FONT_NAME}){{0,9}}"),
                           "font names separated by commas, with names of more than one word quoted"),
    "page-size": CssKind(
        re.compile(rf"(?:auto|{_ORIENTATION}|{_PAPER}(?: {_ORIENTATION})?|{_LENGTH}(?: {_LENGTH})?)"),
        "a paper size such as A4 or letter (and landscape or portrait), or one or two lengths"),
    "margin": CssKind(re.compile(rf"{_LENGTH}(?: {_LENGTH}){{0,3}}"), "one to four lengths separated by spaces"),
    "max-width": CssKind(re.compile(rf"(?:none|{_LENGTH})"), "none or a length such as 46rem"),
    # Raw CSS appended to the stylesheet: no grammar, so an untrusted style may not set it at all.
    "css": CssKind(re.compile(r"(?!)"), "empty"),
}

#: What no value placed in the stylesheet may contain, trusted or not: it
#: would open or close a comment, end the ``<style>`` element, or escape what
#: follows. Rejected, never stripped: removing one token can form another.
CSS_FORBIDDEN = ("/*", "*/", "</", "\\")


def css_field(kind: str, default: str) -> Any:
    """A text setting that reaches the stylesheet, of the :data:`CSS_KINDS`
    entry *kind*. The loader checks its value (:func:`css_problem`)."""
    assert kind in CSS_KINDS, kind
    return field(default=default, metadata={"css": kind})


def css_problem(value: str, kind: str, *, trusted: bool) -> str | None:
    """What is wrong with *value* for a setting of *kind* written into the
    stylesheet, or None. It never quotes the value.

    Always: no comment delimiter, ``</``, backslash, control character, or
    line break (and, for raw CSS, no ``</style``). An untrusted style is held
    to the grammar of its kind, and may set no raw CSS."""
    if kind == "css":
        if "</style" in value.lower():
            return "must not contain '</style' (it would end the stylesheet)"
        if not trusted and value:
            return "is not allowed in an untrusted style (it is raw CSS); leave it empty"
        return None
    for token in CSS_FORBIDDEN:
        if token in value:
            return f"must not contain '{token}' (it would break out of the stylesheet)"
    if any(unicodedata.category(char) in ("Cc", "Zl", "Zp") for char in value):
        return "must not contain a control character or a line break"
    if not trusted:
        css = CSS_KINDS[kind]
        if not css.grammar.fullmatch(value):
            return f"is not a plain value an untrusted style may use; expected {css.expects}"
    return None


@dataclass(frozen=True, slots=True)
class HeadingStyle:
    """Presentation of one heading level."""

    size: str = css_field("length", "1em")
    weight: str = css_field("font-weight", "700")
    italic: bool = False
    transform: Literal["none", "uppercase", "small-caps"] = "none"
    align: Literal["left", "center"] = "left"


@dataclass(frozen=True, slots=True)
class Typography:
    font_family: str = css_field("font-family", 'Georgia, "Times New Roman", serif')
    font_size: str = css_field("length", "11pt")
    line_height: str = css_field("line-height", "1.5")
    color: str = css_field("color", "#1a1a1a")
    link_color: str = css_field("color", "#1f4e8c")
    justify: bool = False


@dataclass(frozen=True, slots=True)
class Page:
    """Print layout. Used by the HTML writer's print stylesheet now, and by
    the PDF writer later."""

    size: str = css_field("page-size", "A4")
    margin: str = css_field("margin", "25mm")


@dataclass(frozen=True, slots=True)
class Html:
    max_width: str = css_field("max-width", "46rem")
    #: Appended to the generated stylesheet. Raw CSS, so trusted configuration:
    #: it may not contain ``</style``, and an untrusted style may not set it
    #: (``load_style(trusted=False)``).
    extra_css: str = css_field("css", "")


def _default_headings() -> dict[int, HeadingStyle]:
    return {
        1: HeadingStyle(size="1.15em", transform="uppercase"),
        2: HeadingStyle(size="1em"),
        3: HeadingStyle(size="1em", weight="600", italic=True),
        4: HeadingStyle(size="1em", weight="600", italic=True),
        5: HeadingStyle(size="1em", weight="400", italic=True),
    }


@dataclass(frozen=True, slots=True)
class Style:
    """A complete, validated style template."""

    version: int = 1
    name: str = "default"
    #: The template this one builds on; resolved by the loader.
    extends: str | None = None
    #: The formatting locale (§10.1), e.g. ``en-US`` or ``cs-CZ``. ``None``
    #: uses the document's ``language`` as a hint, as §10.1 allows.
    locale: str | None = None
    numbering: Numbering = field(default_factory=Numbering)
    enumeration: Enumeration = field(default_factory=Enumeration)
    paragraphs: Paragraphs = field(default_factory=Paragraphs)
    references: References = field(default_factory=References)
    definitions: Definitions = field(default_factory=Definitions)
    values: Values = field(default_factory=Values)
    placeholders: Placeholders = field(default_factory=Placeholders)
    title_block: TitleBlock = field(default_factory=TitleBlock)
    contents: Contents = field(default_factory=Contents)
    attachments: Attachments = field(default_factory=Attachments)
    signatures: Signatures = field(default_factory=Signatures)
    template_view: TemplateView = field(default_factory=TemplateView)
    labels: Labels = field(default_factory=Labels)
    typography: Typography = field(default_factory=Typography)
    headings: dict[int, HeadingStyle] = field(default_factory=_default_headings)
    page: Page = field(default_factory=Page)
    html: Html = field(default_factory=Html)


# ---------------------------------------------------------------------------
# Text templates
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TemplateFields:
    """The ``{field}`` names a text template setting accepts and requires.

    Only identifier-shaped braces (``{n}``, ``{designation}``) are fields; any
    other braces (``{}``, ``{1}``, ``{a b}``) are literal text, as the
    resolver keeps them. ``why`` says what a required field is for, in the
    message about a template that leaves it out. ``empty_falls_back`` marks
    a setting the resolver replaces with the bare field when it is empty
    (``labels.condition or "{condition}"``), so an empty template is not
    missing its required field: it shows the condition as written."""

    accepted: frozenset[str] = frozenset()
    required: frozenset[str] = frozenset()
    why: str = ""
    empty_falls_back: bool = False


#: The designation-building settings are read in :mod:`..resolve.numbering`
#: (``fill``) and :mod:`..resolve.resolver`. A heading's ``label`` sees its own
#: counter and its designation; its ``ref`` is one piece of a designation, so
#: it sees only the counter.
HEADING_LABEL = TemplateFields(frozenset({"n", "path"}))
HEADING_REF = TemplateFields(frozenset({"n"}), frozenset({"n"}), "each level must tell its sections apart (§13.3)")
#: List items and paragraphs also see the containing section's designation.
ITEM_LABEL = TemplateFields(frozenset({"n", "path", "section"}))
ITEM_REF = TemplateFields(frozenset({"n", "section"}), frozenset({"n"}), "each item must tell itself apart (§13.3)")
REFERENCE_FORMAT = TemplateFields(
    frozenset({"designation"}), frozenset({"designation"}), "every reference would read the same (§13.3)"
)
#: Labels are plain words, except for the two that carry a value.
LABEL_FIELDS: dict[str, TemplateFields] = {
    "condition": TemplateFields(
        frozenset({"condition"}), frozenset({"condition"}), "a conditional unit's condition must be shown (§15.8)",
        empty_falls_back=True,  # Resolver._condition: ``self.labels.condition or "{condition}"``
    ),
    # Resolver: ``self.labels.attachment_file or "{file}"``; it requires nothing, so the fallback is moot.
    "attachment_file": TemplateFields(frozenset({"file"}), empty_falls_back=True),
}


def _unused(fields: TemplateFields) -> TemplateFields:
    """*fields* for a setting the style does not use: a typo is still a typo,
    but nothing is missing from a setting nothing reads."""
    return replace(fields, required=frozenset(), why="")


def template_settings(style: Style) -> Iterator[tuple[str, str, TemplateFields]]:
    """Every text template setting of *style* that has been set and substitutes
    fields, as ``(key path, template, fields)``. A label left unset (``None``)
    takes a built-in one, which is correct by construction. A plain label
    (``labels.*`` but ``condition`` and ``attachment_file``) substitutes
    nothing, so braces in it are text and it is not listed.

    A ``ref`` that is not in use does not have to tell its units apart: the
    heading levels' under ``numbering.scheme: none`` (a reference shows the
    heading text, §13.3), the item levels' when lists are not enumerated
    (``enumeration.enabled: false`` and ``ordered: renumber``), the
    paragraph's unless ``paragraphs.numbered``. Their fields are still checked
    for typos."""
    numbered = style.numbering.scheme != "none"
    enumerated = style.enumeration.enabled or style.enumeration.ordered == "enumerate"
    for name, kind_label, kind_ref, in_use in (
        ("numbering", HEADING_LABEL, HEADING_REF, numbered),
        ("enumeration", ITEM_LABEL, ITEM_REF, enumerated),
    ):
        for index, level in enumerate(getattr(style, name).levels):
            yield f"{name}.levels[{index}].label", level.label, kind_label
            yield f"{name}.levels[{index}].ref", level.ref, kind_ref if in_use else _unused(kind_ref)
    yield "paragraphs.format.label", style.paragraphs.format.label, ITEM_LABEL
    yield ("paragraphs.format.ref", style.paragraphs.format.ref,
           ITEM_REF if style.paragraphs.numbered else _unused(ITEM_REF))
    yield "references.format", style.references.format, REFERENCE_FORMAT
    for item in fields(Labels):
        template = getattr(style.labels, item.name)
        if template is not None and item.name in LABEL_FIELDS:
            yield f"labels.{item.name}", template, LABEL_FIELDS[item.name]
