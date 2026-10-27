"""Locale formatting of field specs (§10, §13.5) with Babel's CLDR data
(docs/decisions/0005).

Values are stored canonically in the source — ISO dates, decimal amounts with
a period — and only their display varies by locale. Amounts are handled as
``Decimal`` from the source text and are never rounded or truncated (§10.3):
money is padded to its currency's minor units, and every digit written in
the source is kept (the fraction shows the larger of the two). A duration keeps every digit it is written with too,
and its plural follows the digits it displays (§10.5).
"""
from __future__ import annotations

import datetime
import decimal
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal

from babel import Locale, UnknownLocaleError
from babel.dates import format_date
from babel.numbers import (
    NumberPattern,
    format_currency,
    get_currency_name,
    get_currency_precision,
    get_currency_unit_pattern,
    parse_pattern,
)

from ..errors import InternalError
from ..style.model import Values

#: §10.5 duration units → CLDR unit ids.
DURATION_UNITS: dict[str, str] = {
    "S": "duration-second",
    "MIN": "duration-minute",
    "H": "duration-hour",
    "D": "duration-day",
    "W": "duration-week",
    "MO": "duration-month",
    "Y": "duration-year",
}

#: CLDR unit-pattern lengths, in the order a missing length is replaced.
_LENGTHS = ("long", "short", "narrow")

#: A letter directly touching a digit, on either side. CLDR's currency
#: spacing puts a no-break space between an alphabetic currency symbol and
#: the digits ("CZK 5.00", "5,00 Kč"); Babel's patterns do not apply it, so
#: it is applied here. A symbol that ends in a sign ("$", "R$", "CA$") has no
#: letter next to the digits and is left alone.
_LETTER_DIGIT = re.compile(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])")


def parse_locale(tag: str) -> Locale | None:
    """The Babel locale for a BCP 47 *tag* (``cs-CZ``, ``en``), or None."""
    try:
        return Locale.parse(tag.replace("-", "_"))
    except (UnknownLocaleError, ValueError, TypeError):
        return None


@dataclass(frozen=True)
class DurationText:
    """A formatted duration, and whether the locale had no duration pattern
    for its unit at all, so that English is shown. The formatter stays free
    of diagnostics: the resolver reports the fallback (``render-locale-fallback``)."""

    text: str
    english_fallback: bool


@contextmanager
def _exact(value: str) -> Iterator[None]:
    """A decimal context in which the amount *value* is never rounded (§10.3).

    The default context carries 28 significant digits: Babel's quantizing then
    rounds a longer fraction and raises ``InvalidOperation`` for an integer of
    28 digits or more. ``Decimal(value)`` itself is always exact; the
    precision here covers every digit of the source plus a margin for the
    digits formatting adds (padding to a currency's minor units). With it no
    operation on a valid amount can signal, so no trap is relaxed: one that
    fired would be a bug, and surfaces as ``InternalError``.
    """
    precision = max(28, len(value) + 8)
    context = decimal.Context(prec=precision, rounding=decimal.ROUND_HALF_EVEN,
                              Emin=decimal.MIN_EMIN, Emax=decimal.MAX_EMAX)
    with decimal.localcontext(context):
        yield


def _unit_patterns(locale: Locale, unit_id: str, length: str) -> dict[str, str]:
    """The CLDR patterns of *unit_id* at *length* in *locale*, by plural
    category (``one``, ``few``, ``other``, …), empty when there are none. The
    "per unit" form (``per``) is not a plural category and is left out.

    Babel 2.x has no public API for a pattern by plural category: ``format_unit``
    formats the number itself, rounding it to 3 decimals, and prints the unit
    id when a length has no pattern. ``Locale._data`` is the one place the
    patterns can be read, and it is read only here. Babel has already merged
    the data a regional locale inherits into it, so no parent is looked up.
    """
    lengths = locale._data.get("unit_patterns", {}).get(unit_id, {})  # noqa: SLF001
    patterns = lengths.get(length, {})
    return {category: pattern for category, pattern in patterns.items() if category != "per"}


def _plural_category(locale: Locale, value: Decimal) -> str:
    """The CLDR plural category of *value* in *locale*.

    CLDR's plural operands of a value are its integer and fraction digits read
    as integers, and Python refuses to convert a string of more than 4300 digits
    to an int (``ValueError``). Such a value is a non-integer or a very long
    integer whose category cannot be computed under that limit, and ``other`` is
    the category CLDR gives non-integers in nearly every locale, so ``other`` it
    is. The limit is not raised: ``sys.set_int_max_str_digits`` is process-wide.
    """
    try:
        return locale.plural_form(value)
    except ValueError:
        return "other"


def _duration_pattern(locale: Locale, unit_id: str, length: str, value: Decimal) -> tuple[str, bool]:
    """The CLDR pattern for a duration of *value*, and whether it is English
    for a locale that is not.

    A raw unit id (``duration-day``) is never the answer. The lookup goes:
    the requested length, then the other lengths (long, short, narrow); the
    same for the locale's language alone; then English. In each set of
    patterns the plural category of *value* is used, or ``other`` when the set
    has no pattern for that category. The category comes from the locale
    whose pattern is used, because it is that locale's grammar.
    """
    lengths = (length, *(other for other in _LENGTHS if other != length))
    candidates = [locale]
    language = parse_locale(locale.language)
    if language is not None and str(language) != str(locale):
        candidates.append(language)
    for candidate in candidates:
        category = _plural_category(candidate, value)
        for each in lengths:
            patterns = _unit_patterns(candidate, unit_id, each)
            pattern = patterns.get(category) or patterns.get("other")
            if pattern:
                return pattern, False
    english = Locale("en")
    category = _plural_category(english, value)
    for each in lengths:
        patterns = _unit_patterns(english, unit_id, each)
        pattern = patterns.get(category) or patterns.get("other")
        if pattern:
            return pattern, locale.language != "en"
    raise InternalError(f"Babel has no English pattern for the unit '{unit_id}'. This is a bug.")


class Formatter:
    """Formats values for one locale and one style's ``values`` settings."""

    def __init__(self, locale: Locale, values: Values) -> None:
        self.locale = locale
        self.values = values

    @property
    def tag(self) -> str:
        return str(self.locale).replace("_", "-")

    def date(self, value: str) -> str:
        """An ISO 8601 date (already validated) for display."""
        return format_date(datetime.date.fromisoformat(value), format=self.values.date, locale=self.locale)

    def number(self, value: str) -> str:
        """A decimal amount with the locale's separators, keeping every
        digit written after the decimal point."""
        with _exact(value):
            return self._number(value)

    def _number(self, value: str) -> str:
        decimals = len(value.partition(".")[2])
        pattern = parse_pattern(self.locale.decimal_formats[None].pattern)
        pattern.frac_prec = (decimals, decimals)
        return pattern.apply(Decimal(value), self.locale, decimal_quantization=False)

    def money(self, amount: str, currency: str | None) -> str:
        """A money amount (already validated), with its currency when given."""
        if not currency:
            return self.number(amount)
        with _exact(amount):
            return self._money(amount, currency)

    def _money(self, amount: str, currency: str) -> str:
        value = Decimal(amount)
        mode = self.values.money
        if mode == "name":
            source = self.locale.decimal_formats[None].pattern
        else:
            source = self.locale.currency_formats["standard"].pattern
            if mode == "code" and "¤¤" not in source:
                source = source.replace("¤", "¤¤")
        # Fraction digits shown: the currency's minor units, or every digit
        # written when there are more (§10.3). Babel's own quantizing would
        # drop a trailing zero beyond the minor units ("1.500" EUR), so the
        # digit count is fixed in the pattern, and currency_digits is off.
        pattern = parse_pattern(source)
        digits = max(get_currency_precision(currency), len(amount.partition(".")[2]))
        pattern.frac_prec = (digits, digits)
        if mode == "name":
            return self._money_name(value, currency, pattern)
        text = format_currency(value, currency, format=pattern, locale=self.locale, currency_digits=False,
                               format_type="standard", decimal_quantization=False)
        # CLDR spaces an alphabetic symbol or code from the digits, on
        # whichever side it stands ("USD 10,000.00", "10 000,00 CZK").
        return _LETTER_DIGIT.sub("\u00a0", text)

    def _money_name(self, value: Decimal, currency: str, pattern: NumberPattern) -> str:
        """*value* with the currency's name ("1,234.50 euros"), through Babel
        unless the plural category of *value* cannot be computed (see
        :func:`_plural_category`): Babel's own lookup of the unit pattern would
        then fail, so the ``other`` pattern and name are put together here, the
        way Babel does it (``_format_currency_long_name``)."""
        try:
            self.locale.plural_form(value)
        except ValueError:
            number = pattern.apply(value, self.locale, currency=currency, currency_digits=False,
                                   decimal_quantization=False)
            unit_pattern = get_currency_unit_pattern(currency, locale=self.locale)
            return unit_pattern.format(number, get_currency_name(currency, count=value, locale=self.locale))
        return format_currency(value, currency, format=pattern, locale=self.locale, currency_digits=False,
                               format_type="name", decimal_quantization=False)

    def duration(self, value: str, unit: str) -> str:
        """A duration (already validated) such as "12 months" or "12 měsíců"."""
        return self.format_duration(value, unit).text

    def format_duration(self, value: str, unit: str) -> DurationText:
        """A duration with every digit as written ("0.0001 days"), its plural
        chosen from those digits as CLDR defines it ("1.0 days" in English,
        "2,5 roku" in Czech), and whether English had to stand in for a
        locale without a pattern (see :func:`_duration_pattern`)."""
        with _exact(value):
            pattern, english = _duration_pattern(
                self.locale, DURATION_UNITS[unit], self.values.duration, Decimal(value))
            return DurationText(pattern.replace("{0}", self._number(value)), english)
