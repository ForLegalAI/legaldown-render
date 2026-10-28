"""A style's presentation settings as the DOCX writer reads them: Word units.

The style template speaks CSS (``25mm``, ``1.15em``, ``#1a1a1a``,
``'Georgia, serif'``), because the HTML writer passes the values on. Word has
twips, half-points and six-digit colours, so :func:`docx_settings` converts
every setting the DOCX writer uses, once, and reports **all** that it cannot
as one :class:`~..errors.StyleError` (docs/decisions/0006: a configuration
problem raises before rendering, and the render API calls it before the
document is touched). What it converts:

* Lengths: ``mm``, ``cm``, ``in``, ``pt``, ``pc`` and ``px`` are absolute
  (96 px to the inch, as CSS has it). ``em`` and ``rem`` are relative to
  ``typography.font_size``, which itself is relative to the 12 pt (16 px) a
  browser starts from when it is written in them. ``%``, ``ch`` and ``ex``
  depend on a layout or a font Word does not give the renderer, so they are an
  error for DOCX (HTML takes them).
* ``page.size`` and ``page.margin``: paper names, one or two lengths, an
  orientation; one to four margin lengths as CSS shorthand.
* Colours: ``#rgb``, ``#rrggbb`` (with or without an alpha digit pair, which Word
  has no use for), ``rgb()``, ``hsl()``, and the sixteen basic names with
  ``orange``; any other name is an error asking for a hexadecimal colour.
* The first family of ``typography.font_family``.

A message names the setting and what is wrong with it and never quotes the
value, so it is safe to show for a style from somebody else.
"""
from __future__ import annotations

import colorsys
import math
import re
from dataclasses import dataclass

from ..errors import StyleError
from .model import Style

#: A browser starts from 16 px; the size of text written in ``em`` or ``rem``
#: before ``typography.font_size`` says otherwise.
BASE_FONT_POINTS = 12.0

_TWIPS_PER_POINT = 20
#: Twips per unit of the absolute CSS lengths: 1 in = 72 pt = 96 px = 25.4 mm.
_ABSOLUTE = {
    "pt": 20.0,
    "pc": 240.0,
    "px": 15.0,
    "in": 1440.0,
    "cm": 1440.0 / 2.54,
    "mm": 1440.0 / 25.4,
}
_RELATIVE = ("em", "rem")
#: Units that cannot be a Word measure, as the message says.
_REFUSED = ("%", "ch", "ex")

_LENGTH_RE = re.compile(r"^([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))(em|rem|ex|ch|pt|pc|px|mm|cm|in|%)?$")
_NUMBER_RE = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$")

#: Paper sizes (width, height) in twips, portrait: ISO A and B in millimetres
#: (CSS's B is ISO B), the American ones in inches.
_PAPER = {
    "a3": (16838, 23811),
    "a4": (11906, 16838),
    "a5": (8391, 11906),
    "b4": (14173, 20013),
    "b5": (9979, 14173),
    "letter": (12240, 15840),
    "legal": (12240, 20160),
    "ledger": (15840, 24480),
}
_ORIENTATIONS = ("portrait", "landscape")

#: The largest size Word accepts for a font (``ST_HpsMeasure``), in points.
_MAX_FONT_POINTS = 1638
#: The largest body text: the title is 1.6 times it and must stay within :data:`_MAX_FONT_POINTS`.
_MAX_BODY_POINTS = 1023
#: Word's limit for a page edge, a margin or a line (22 inches), in twips; and the least page edge.
_MAX_TWIPS = 31680
_MIN_PAGE_TWIPS = 144
#: The least width and height of the text area, so that no tab stop or text width is negative or absurd.
_MIN_TEXT_TWIPS = 720
#: A number of more characters than this is no length (and is not converted: ``float`` of a
#: few hundred digits is infinity); a length over this many twips is refused before it is used.
_MAX_NUMBER_CHARS = 32
_MAX_LENGTH_TWIPS = 10_000_000

_BASIC_COLORS = {
    "black": "000000", "silver": "C0C0C0", "gray": "808080", "grey": "808080", "white": "FFFFFF",
    "maroon": "800000", "red": "FF0000", "purple": "800080", "fuchsia": "FF00FF", "magenta": "FF00FF",
    "green": "008000", "lime": "00FF00", "olive": "808000", "yellow": "FFFF00", "navy": "000080",
    "blue": "0000FF", "teal": "008080", "aqua": "00FFFF", "cyan": "00FFFF", "orange": "FFA500",
}

#: What the families CSS names generically are in a Word document.
_GENERIC_FAMILIES = {
    "serif": "Times New Roman", "sans-serif": "Arial", "system-ui": "Arial", "ui-sans-serif": "Arial",
    "ui-serif": "Times New Roman", "monospace": "Courier New", "ui-monospace": "Courier New",
    "cursive": "Times New Roman", "fantasy": "Times New Roman",
}
_DEFAULT_FAMILY = "Times New Roman"


@dataclass(frozen=True, slots=True)
class PageGeometry:
    """The page, in twips (1/1440 inch)."""

    width: int
    height: int
    top: int
    right: int
    bottom: int
    left: int
    #: Distance of the running header from the top edge, and of the footer from the bottom.
    header: int
    footer: int
    landscape: bool

    @property
    def text_width(self) -> int:
        """The width between the margins: where a right tab stop goes."""
        return self.width - self.left - self.right


@dataclass(frozen=True, slots=True)
class HeadingFormat:
    """One heading level's character and paragraph formatting."""

    #: Half-points, the unit of ``w:sz``.
    half_points: int
    bold: bool
    italic: bool
    caps: bool
    small_caps: bool
    align: str


@dataclass(frozen=True, slots=True)
class DocxSettings:
    """Every presentation setting the DOCX writer reads, in Word's units."""

    page: PageGeometry
    #: The first family of ``typography.font_family``.
    font: str
    #: Body text size, in half-points.
    half_points: int
    #: ``w:spacing/@w:line`` and its rule (``auto``: 240 is one line; ``atLeast``: twips).
    line: int
    line_rule: str
    #: Six hexadecimal digits.
    color: str
    link_color: str
    justify: bool
    headings: dict[int, HeadingFormat]
    update_fields_on_open: bool

    @property
    def points(self) -> float:
        return self.half_points / 2


class _Problems:
    """The problems found while converting, each naming its setting."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, key: str, problem: str) -> None:
        message = f"{key}: {problem}"
        if message not in self.items:
            self.items.append(message)


def docx_settings(style: Style, *, trusted: bool = True) -> DocxSettings:
    """*style*'s presentation settings in Word units. Raises
    :class:`~..errors.StyleError` listing every setting the DOCX writer cannot
    express (see the module docstring); *trusted* only says how the style is
    named in it."""
    problems = _Problems()
    typography = style.typography

    font_points = _font_points(typography.font_size, "typography.font_size", BASE_FONT_POINTS, problems,
                               _MAX_BODY_POINTS)
    font_points = font_points if font_points is not None else 11.0
    half_points = round(font_points * 2)

    line, rule = _line_height(typography.line_height, font_points, problems)
    page = _page(style.page.size, style.page.margin, font_points, problems)
    color = _color(typography.color, "typography.color", problems)
    link_color = _color(typography.link_color, "typography.link_color", problems)

    headings: dict[int, HeadingFormat] = {}
    for level in range(1, 6):
        heading = style.headings.get(level)
        if heading is None:
            continue
        key = f"headings.{level}"
        size = _font_points(heading.size, f"{key}.size", font_points, problems)
        bold = _bold(heading.weight, f"{key}.weight", problems)
        headings[level] = HeadingFormat(
            half_points=round((size if size is not None else font_points) * 2), bold=bold, italic=heading.italic,
            caps=heading.transform == "uppercase", small_caps=heading.transform == "small-caps", align=heading.align)

    if problems.items:
        raise StyleError(f"'{style.name}'" if trusted else "'untrusted style'", problems.items)
    return DocxSettings(
        page=page, font=_family(typography.font_family), half_points=half_points, line=line, line_rule=rule,
        color=color, link_color=link_color, justify=typography.justify, headings=headings,
        update_fields_on_open=style.docx.update_fields_on_open)


# -- Lengths ----------------------------------------------------------------------


def _length(value: str, key: str, em_points: float, problems: _Problems) -> int | None:
    """*value*, a CSS length, in twips; None (with a problem) when Word cannot
    have it. ``em`` and ``rem`` are *em_points* points."""
    match = _LENGTH_RE.match(value.strip().lower())
    if match is None:
        problems.add(key, "is not a length the DOCX writer can read (a number and a unit such as 25mm, 11pt or 1.2em)")
        return None
    if len(match.group(1)) > _MAX_NUMBER_CHARS:
        problems.add(key, "is a number too large for a Word document")
        return None
    number, unit = float(match.group(1)), match.group(2)
    if unit is None:
        if number != 0:
            problems.add(key, "a length needs a unit (only 0 has none)")
            return None
        return 0
    if unit in _REFUSED:
        problems.add(key, f"uses '{unit}', which a Word document cannot express; use pt, mm, cm, in, px, em or rem "
                          f"(HTML accepts it)")
        return None
    twips = number * (em_points * _TWIPS_PER_POINT if unit in _RELATIVE else _ABSOLUTE[unit])
    if not math.isfinite(twips) or abs(twips) > _MAX_LENGTH_TWIPS:
        problems.add(key, "is a length too large for a Word document")
        return None
    return round(twips)


def _font_points(value: str, key: str, em_points: float, problems: _Problems,
                 maximum: int = _MAX_FONT_POINTS) -> float | None:
    twips = _length(value, key, em_points, problems)
    if twips is None:
        return None
    points = twips / _TWIPS_PER_POINT
    if not 1 <= points <= maximum:
        problems.add(key, f"must be between 1pt and {maximum}pt in a Word document")
        return None
    return points


def _line_height(value: str, font_points: float, problems: _Problems) -> tuple[int, str]:
    """``w:spacing``'s ``line`` and ``lineRule``: ``normal`` is single spacing, a
    number multiplies the font size (``auto``, 240 to the line), a length is
    the least height of a line (``atLeast``: ``exact`` would cut off tall text)."""
    text = value.strip()
    if text == "normal":
        return 240, "auto"
    if _NUMBER_RE.match(text):
        factor = float(text)
        if not 0 < factor <= 100:  # also false for infinity
            problems.add("typography.line_height", "a number must be greater than 0 and at most 100")
            return 240, "auto"
        return round(factor * 240), "auto"
    twips = _length(text, "typography.line_height", font_points, problems)
    if twips is None or not 0 < twips <= _MAX_TWIPS:
        if twips is not None:
            problems.add("typography.line_height", f"must be greater than 0 and at most {_MAX_TWIPS // 20}pt")
        return 240, "auto"
    return twips, "atLeast"


def _page(size: str, margin: str, font_points: float, problems: _Problems) -> PageGeometry:
    """The page in twips. Word's limits hold: an edge of 144 to 31680, a margin of 0 to
    31680, and margins that leave a text area of at least 720 in each direction, so a
    tab stop at the text width can never be negative."""
    before = len(problems.items)
    width, height = _page_size(size, font_points, problems)
    top, right, bottom, left = _margins(margin, font_points, problems)
    if len(problems.items) == before:  # a value that is already wrong has said so
        if not all(_MIN_PAGE_TWIPS <= edge <= _MAX_TWIPS for edge in (width, height)):
            problems.add("page.size", f"each edge must be between {_MIN_PAGE_TWIPS / 20:g}pt and "
                                      f"{_MAX_TWIPS // 20}pt in a Word document")
        elif not all(0 <= edge <= _MAX_TWIPS for edge in (top, right, bottom, left)):
            problems.add("page.margin", f"each margin must be between 0 and {_MAX_TWIPS // 20}pt in a Word document")
        elif width - left - right < _MIN_TEXT_TWIPS or height - top - bottom < _MIN_TEXT_TWIPS:
            problems.add("page.margin", "the margins leave no room for text on the page (page.size)")
    return PageGeometry(width=width, height=height, top=top, right=right, bottom=bottom, left=left,
                        header=max(0, min(720, top // 2)), footer=max(0, min(720, bottom // 2)),
                        landscape=width > height)


def _page_size(value: str, font_points: float, problems: _Problems) -> tuple[int, int]:
    """The page's width and height in twips. ``auto`` and an orientation alone
    take A4; a paper name is portrait unless it says ``landscape``; one length
    is a square, two are width and height. A page wider than it is tall is
    landscape."""
    default = _PAPER["a4"]
    tokens = value.lower().split()
    if not 1 <= len(tokens) <= 2:
        problems.add("page.size", "must be a paper size (A3 to A5, B4, B5, letter, legal, ledger), auto, an "
                                  "orientation, or one or two lengths")
        return default
    if tokens == ["auto"]:
        return default
    orientation = tokens[-1] if tokens[-1] in _ORIENTATIONS else None
    names = tokens[:-1] if orientation else tokens
    if not names:
        width, height = default
    elif len(names) == 1 and names[0] in _PAPER:
        width, height = _PAPER[names[0]]
    else:
        lengths = [_length(token, "page.size", font_points, problems) for token in names]
        if None in lengths or any(length <= 0 for length in lengths if length is not None):
            if None not in lengths:
                problems.add("page.size", "lengths must be greater than 0")
            return default
        if orientation:
            problems.add("page.size", "an orientation goes with a paper size, not with lengths")
        width = lengths[0] or 0
        height = lengths[1] if len(lengths) > 1 else width
        orientation = None  # the lengths say which way the page lies
    if (orientation == "landscape" and width < height) or (orientation == "portrait" and width > height):
        width, height = height, width
    return width, height


def _margins(value: str, font_points: float, problems: _Problems) -> tuple[int, int, int, int]:
    """Top, right, bottom and left in twips, from one to four lengths as in the
    CSS shorthand: what is missing repeats the opposite side."""
    tokens = value.split()
    if not 1 <= len(tokens) <= 4:
        problems.add("page.margin", "must be one to four lengths")
        return (1417,) * 4
    lengths = [_length(token, "page.margin", font_points, problems) for token in tokens]
    if None in lengths:
        return (1417,) * 4
    top, right, bottom, left = (lengths + lengths[1:2] * (4 - len(lengths)) if len(lengths) == 3
                                else lengths * (4 // len(lengths)))
    if min(top, right, bottom, left) < 0:
        problems.add("page.margin", "must not be negative")
    return top, right, bottom, left


# -- Colours, weights, fonts --------------------------------------------------------


def _color(value: str, key: str, problems: _Problems) -> str:
    """*value*, a CSS colour, as six upper-case hexadecimal digits."""
    text = value.strip().lower()
    if text.startswith("#"):
        digits = text[1:]
        if re.fullmatch(r"[0-9a-f]{3,4}", digits):
            return "".join(char * 2 for char in digits[:3]).upper()
        if re.fullmatch(r"(?:[0-9a-f]{6}|[0-9a-f]{8})", digits):
            return digits[:6].upper()
    elif text in _BASIC_COLORS:
        return _BASIC_COLORS[text]
    else:
        function = re.fullmatch(r"(rgba?|hsla?)\(([0-9a-z.%,/ +-]{1,60})\)", text)
        if function is not None:
            parts = [part for part in re.split(r"[ ,/]+", function.group(2).strip()) if part]
            if function.group(1).startswith("rgb") and len(parts) in (3, 4):
                channels = [_channel(part) for part in parts[:3]]
                if None not in channels:
                    return "".join(f"{channel:02X}" for channel in channels)  # type: ignore[str-format]
            elif function.group(1).startswith("hsl") and len(parts) in (3, 4):
                rgb = _hsl(parts)
                if rgb is not None:
                    return rgb
    problems.add(key, "is a colour the DOCX writer cannot read; write it as #rrggbb (or #rgb, rgb(), hsl(), or one "
                      "of black, silver, gray, white, maroon, red, purple, fuchsia, green, lime, olive, yellow, "
                      "navy, blue, teal, aqua, orange)")
    return "000000"


def _channel(part: str) -> int | None:
    try:
        number = float(part[:-1]) * 2.55 if part.endswith("%") else float(part)
    except ValueError:
        return None
    return round(min(255.0, max(0.0, number))) if math.isfinite(number) else None


def _hsl(parts: list[str]) -> str | None:
    try:
        hue_degrees = float(parts[0].removesuffix("deg"))
        if not (parts[1].endswith("%") and parts[2].endswith("%")):
            return None
        saturation = float(parts[1][:-1])
        lightness = float(parts[2][:-1])
    except ValueError:
        return None
    if not all(math.isfinite(number) for number in (hue_degrees, saturation, lightness)):
        return None
    hue = hue_degrees % 360 / 360
    saturation = min(100.0, max(0.0, saturation)) / 100
    lightness = min(100.0, max(0.0, lightness)) / 100
    red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
    return f"{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}"


def _bold(value: str, key: str, problems: _Problems) -> bool:
    text = value.strip().lower()
    if text in ("bold", "bolder"):
        return True
    if text in ("normal", "lighter"):
        return False
    if _NUMBER_RE.match(text):
        return float(text) >= 600
    problems.add(key, "is not a weight the DOCX writer can read (normal, bold, or a number)")
    return False


def _family(value: str) -> str:
    """The first family of a CSS ``font-family`` list: its name, unquoted; a
    generic family (``serif``, ``monospace``) as the font Word uses for it."""
    match = re.match(r"""\s*(?:"([^"]*)"|'([^']*)'|([^,]*))""", value)
    name = next((group for group in (match.groups() if match else ()) if group is not None), "").strip()
    if not name:
        return _DEFAULT_FAMILY
    return _GENERIC_FAMILIES.get(name.lower(), name)
