from __future__ import annotations

import pytest

from legaldown_render.resolve import Formatter, parse_locale
from legaldown_render.resolve.numbering import extend, fill, format_counter
from legaldown_render.style.model import Values


def formatter(tag: str, **values: str) -> Formatter:
    locale = parse_locale(tag)
    assert locale is not None
    return Formatter(locale, Values(**values))


@pytest.mark.parametrize(("value", "counter", "expected"), [
    (1, "lower-alpha", "a"), (26, "lower-alpha", "z"), (27, "lower-alpha", "aa"), (3, "upper-alpha", "C"),
    (4, "lower-roman", "iv"), (14, "upper-roman", "XIV"), (7, "decimal", "7"),
])
def test_counters(value: int, counter: str, expected: str) -> None:
    assert format_counter(value, counter) == expected


def test_fill_and_extend() -> None:
    assert fill("{section}.{n} {other}", n="2", section="5") == "5.2 {other}"
    assert extend("", ".1", textual=False) == "1"
    assert extend("2.1", "(b)", textual=False) == "2.1(b)"
    assert extend("Termination", "(a)", textual=True) == "Termination (a)"


def test_money_is_padded_never_rounded() -> None:
    en = formatter("en-US")
    assert en.money("10000", "USD") == "$10,000.00"
    assert en.money("1234.567", "EUR") == "€1,234.567"
    assert en.money("5", "JPY") == "¥5"
    assert en.money("1234.50", None) == "1,234.50"


def test_money_display_styles() -> None:
    assert formatter("en-US", money="code").money("10", "USD") == "USD 10.00"
    assert formatter("en-US", money="name").money("10", "USD") == "10.00 US dollars"


def test_locales() -> None:
    cs = formatter("cs-CZ")
    assert cs.date("2026-06-01") == "1. června 2026"
    assert cs.duration("12", "MO") == "12 měsíců"
    assert cs.money("10000", "CZK") == "10 000,00 Kč"
    assert formatter("de").duration("1", "Y") == "1 Jahr"
    assert formatter("en-GB", date="short").date("2026-06-01") == "01/06/2026"


def test_unknown_locale() -> None:
    assert parse_locale("xx-YY") is None
