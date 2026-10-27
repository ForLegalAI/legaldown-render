"""Value formatting (§10, §13.5): exact amounts, durations that keep their
written digits, no raw CLDR unit id, and spaced alphabetic currency symbols."""
from __future__ import annotations

import pytest

from legaldown_render import render
from legaldown_render.resolve import DURATION_UNITS, Formatter, parse_locale
from legaldown_render.resolve.values import DurationText
from legaldown_render.style.model import Values


def formatter(tag: str, **values: str) -> Formatter:
    locale = parse_locale(tag)
    assert locale is not None
    return Formatter(locale, Values(**values))


# -- R2: no crash, no rounding on long amounts (§10.3) -----------------------

@pytest.mark.parametrize("digits", [29, 40, 60])
def test_long_integers_format_exactly(digits: int) -> None:
    value = "1234567890" * 6
    value = value[:digits]
    grouped = f"{int(value):,}"
    en = formatter("en-US")
    assert en.number(value) == grouped
    assert en.money(value, "USD") == f"${grouped}.00"
    assert formatter("en-US", money="code").money(value, "USD") == f"USD\xa0{grouped}.00"
    assert formatter("en-US", money="name").money(value, "USD") == f"{grouped}.00 US dollars"


def test_long_fraction_keeps_every_digit() -> None:
    fraction = "1234567891" * 4
    en = formatter("en-US")
    assert en.number("0." + fraction) == "0." + fraction
    assert en.money("1." + fraction, "EUR") == "€1." + fraction
    assert en.number("1" * 29 + ".5") == f"{int('1' * 29):,}.5"


def test_long_duration_keeps_every_digit() -> None:
    assert formatter("en-US").duration("1" * 40, "D") == f"{int('1' * 40):,} days"
    assert formatter("en-US").duration("0." + "1" * 40, "D") == "0." + "1" * 40 + " days"


def test_long_amount_renders_end_to_end() -> None:
    source = "---\ntitle: T\n---\nPay {{money: " + "9" * 29 + "}} {{money: 12345678901234567890123456789.1234567890123456789}}.\n"
    output = render(source, format="text", locale="en-US").output
    assert f"{int('9' * 29):,}" in output
    assert "12,345,678,901,234,567,890,123,456,789.1234567890123456789" in output


# -- R3: durations keep their digits; the plural follows the displayed value -

@pytest.mark.parametrize(("tag", "value", "unit", "expected"), [
    ("en", "0.0001", "D", "0.0001 days"),
    ("en", "1.23456", "Y", "1.23456 years"),
    ("en", "1", "D", "1 day"),
    ("en", "1.0", "D", "1.0 days"),
    ("en", "12", "MO", "12 months"),
    ("cs", "1", "Y", "1 rok"),
    ("cs", "2", "Y", "2 roky"),
    ("cs", "5", "Y", "5 let"),
    ("cs", "2.5", "Y", "2,5 roku"),
    ("de", "1.50", "W", "1,50 Wochen"),
])
def test_duration_digits_and_plural(tag: str, value: str, unit: str, expected: str) -> None:
    assert formatter(tag).duration(value, unit) == expected


# -- R1: never a raw CLDR unit id ---------------------------------------------

@pytest.mark.parametrize("tag", ["pt-BR", "ja", "zh", "ko", "it", "tr", "nl", "cs", "de", "sk", "pl", "en"])
@pytest.mark.parametrize("length", ["long", "short", "narrow"])
@pytest.mark.parametrize("value", ["1", "2", "5", "1.5"])
def test_no_cldr_unit_id_is_ever_printed(tag: str, length: str, value: str) -> None:
    f = formatter(tag, duration=length)
    for unit in DURATION_UNITS:
        text = f.duration(value, unit)
        assert "duration-" not in text
        assert value.replace(".", f.locale.number_symbols["latn"]["decimal"]) in text


def test_pt_br_long_falls_back_to_the_short_pattern() -> None:
    pt = formatter("pt-BR")
    assert pt.duration("30", "D") == "30 dias"
    assert pt.duration("12", "MO") == "12 meses"
    assert pt.duration("1", "Y") == "1 ano"


def test_fallback_goes_to_the_other_length_first() -> None:
    # cs has a long and a narrow pattern: narrow is asked, narrow is used.
    assert formatter("cs", duration="narrow").duration("30", "D") == "30 d."
    # zh has only "short": long and narrow fall back to it, still Chinese.
    assert formatter("zh", duration="long").duration("30", "D") == "30天"
    assert formatter("zh", duration="narrow").duration("30", "D") == "30天"


def test_a_fallback_inside_the_locale_is_not_signalled() -> None:
    # pt-BR long has no pattern but short has: Portuguese, no English.
    assert formatter("pt-BR").format_duration("30", "D") == DurationText("30 dias", False)
    assert formatter("en").format_duration("1", "D") == DurationText("1 day", False)


def test_english_is_the_last_resort_and_is_signalled(monkeypatch: pytest.MonkeyPatch) -> None:
    from legaldown_render.resolve import values

    real = values._unit_patterns
    # A Czech locale without any duration pattern (Babel's data has none such).
    monkeypatch.setattr(values, "_unit_patterns",
                        lambda locale, unit, length: {} if locale.language == "cs" else real(locale, unit, length))
    f = formatter("cs")
    assert f.format_duration("5", "D") == DurationText("5 days", True)
    assert f.duration("1", "D") == "1 day"
    assert f.duration("1.5", "Y") == "1,5 years"  # the number keeps the locale's separator


def test_fallback_warning_is_emitted_once_by_the_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    from legaldown_render.resolve import values

    real = values._unit_patterns
    monkeypatch.setattr(values, "_unit_patterns",
                        lambda locale, unit, length: {} if locale.language == "cs" else real(locale, unit, length))
    source = ("---\ntitle: T\nlanguage: cs\n---\n"
              "Lhůta {{duration: 5, unit=D}} a {{duration: 6, unit=D}} a {{duration: 2, unit=MO}}.\n")
    result = render(source, format="text")
    fallbacks = [d for d in result.diagnostics if d.rule == "render-locale-fallback"]
    assert [d.message for d in fallbacks] == [
        "No CLDR pattern for 'duration-day' in 'cs'; English used.",
        "No CLDR pattern for 'duration-month' in 'cs'; English used.",
    ]
    assert all(d.level == "warning" for d in fallbacks)
    assert "5 days" in result.output


def test_normal_locales_emit_no_fallback_warning() -> None:
    for language in ("cs", "pt-BR", "ja", "en"):
        source = f"---\ntitle: T\nlanguage: {language}\n---\nX {{{{duration: 5, unit=D}}}}.\n"
        result = render(source, format="text")
        assert not [d for d in result.diagnostics if d.rule == "render-locale-fallback"], language


# -- R4: an alphabetic symbol is spaced from the digits -----------------------

@pytest.mark.parametrize(("tag", "currency", "expected"), [
    ("en", "CZK", "CZK\xa05.00"),
    ("en", "CHF", "CHF\xa05.00"),
    ("en", "USD", "$5.00"),
    ("en", "EUR", "€5.00"),
    ("pt-BR", "BRL", "R$\xa05,00"),
    ("en", "CAD", "CA$5.00"),
    ("cs", "CZK", "5,00\xa0Kč"),
])
def test_symbol_mode_spacing(tag: str, currency: str, expected: str) -> None:
    assert formatter(tag).money("5", currency) == expected


def test_symbol_after_the_digits_is_spaced_too() -> None:
    # Whatever the locale puts after the amount, a letter never touches a digit.
    import re

    for tag in ("en", "de", "fr", "cs", "pl", "sv", "ja", "ru", "pt-PT", "es", "nl", "it"):
        for currency in ("CZK", "CHF", "USD", "EUR", "SEK", "PLN", "XAF"):
            text = formatter(tag).money("5", currency)
            assert not re.search(r"[^\W\d_]\d|\d[^\W\d_]", text), (tag, currency, text)


def test_code_mode_still_spaced() -> None:
    assert formatter("en", money="code").money("5", "CZK") == "CZK\xa05.00"
    assert formatter("de", money="code").money("5", "CZK") == "5,00\xa0CZK"
