"""Locale formatting of field specs (§10, §13.5) with Babel's CLDR data
(docs/decisions/0005).

Values are stored canonically in the source — ISO dates, decimal amounts with
a period — and only their display varies by locale. Amounts are handled as
``Decimal`` from the source text and are never rounded or truncated (§10.3):
money is padded to its currency's minor units, and any further digits written
in the source are kept.
"""
from __future__ import annotations

import datetime
import re
from decimal import Decimal

from babel import Locale, UnknownLocaleError
from babel.dates import format_date
from babel.numbers import format_currency, parse_pattern
from babel.units import format_unit

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


def parse_locale(tag: str) -> Locale | None:
    """The Babel locale for a BCP 47 *tag* (``cs-CZ``, ``en``), or None."""
    try:
        return Locale.parse(tag.replace("-", "_"))
    except (UnknownLocaleError, ValueError, TypeError):
        return None


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
        decimals = len(value.partition(".")[2])
        pattern = parse_pattern(self.locale.decimal_formats[None].pattern)
        pattern.frac_prec = (decimals, decimals)
        return pattern.apply(Decimal(value), self.locale, decimal_quantization=False)

    def money(self, amount: str, currency: str | None) -> str:
        """A money amount (already validated), with its currency when given."""
        if not currency:
            return self.number(amount)
        value = Decimal(amount)
        if self.values.money == "name":
            return format_currency(value, currency, locale=self.locale, format_type="name",
                                   decimal_quantization=False)
        pattern = None
        if self.values.money == "code":
            standard = self.locale.currency_formats["standard"].pattern
            pattern = standard.replace("¤", "¤¤") if "¤¤" not in standard else standard
        text = format_currency(value, currency, format=pattern, locale=self.locale, decimal_quantization=False)
        if self.values.money == "code":
            # CLDR spaces a letter code from the digits ("USD 10,000.00");
            # Babel does not apply that rule, so it is applied here.
            text = re.sub(rf"^{currency}(?=\d)", f"{currency}\u00a0", text)
        return text

    def duration(self, value: str, unit: str) -> str:
        """A duration (already validated) such as "12 months" or "12 měsíců"."""
        return format_unit(Decimal(value), DURATION_UNITS[unit], length=self.values.duration, locale=self.locale)
