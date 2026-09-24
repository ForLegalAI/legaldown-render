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

from dataclasses import dataclass, field
from typing import Literal

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
    #: Overrides the scheme's formats, level by level from level 1 (at most
    #: five, one per heading level, §4.1). Levels not listed keep the
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
    #: ``{file}`` is the attachment's declared file.
    attachment_file: str | None = None
    signatures: str | None = None
    signature_date: str | None = None
    signature_place: str | None = None
    signature_name: str | None = None
    signature_title: str | None = None


@dataclass(frozen=True, slots=True)
class HeadingStyle:
    """Presentation of one heading level."""

    size: str = "1em"
    weight: str = "700"
    italic: bool = False
    transform: Literal["none", "uppercase", "small-caps"] = "none"
    align: Literal["left", "center"] = "left"


@dataclass(frozen=True, slots=True)
class Typography:
    font_family: str = 'Georgia, "Times New Roman", serif'
    font_size: str = "11pt"
    line_height: str = "1.5"
    color: str = "#1a1a1a"
    link_color: str = "#1f4e8c"
    justify: bool = False


@dataclass(frozen=True, slots=True)
class Page:
    """Print layout. Used by the HTML writer's print stylesheet now, and by
    the PDF writer later."""

    size: str = "A4"
    margin: str = "25mm"


@dataclass(frozen=True, slots=True)
class Html:
    max_width: str = "46rem"
    #: Appended to the generated stylesheet. Trusted configuration: a hosted
    #: service must not accept it from untrusted users.
    extra_css: str = ""


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
