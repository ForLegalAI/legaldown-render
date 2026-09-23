# 0005. Locale formatting with Babel

- Status: Proposed
- Date: 2026-09-23

## Context

Dates, money, and durations are stored canonically in the source and formatted at render time for
the active locale (§10, §13.5). Doing this correctly takes real locale data:

- date patterns ("1 June 2026", "June 1, 2026", "1. června 2026")
- decimal and grouping separators
- currency symbols, their placement, and minor units
- plural rules for durations ("1 month", "12 months", "2 měsíce", "5 měsíců")

## Decision (proposed)

Use [Babel](https://babel.pocoo.org/). It is pure Python and ships CLDR data.

- `format_date` for dates
- `format_currency` for money, padded to the currency's minor units and never rounded (§10.3).
  Values are handled as `Decimal` from the source string.
- `format_unit` for durations, with CLDR plural rules
- CLDR's ISO 4217 data to check currency codes

All formatting goes through the `locale/` module, so the dependency sits behind one interface.

## Alternatives considered

- **Built-in tables for a few locales.** Small, but wrong for everyone else, and plural rules are
  easy to get wrong.
- **Python's `locale` module.** Depends on the host system, is process-global, and is not
  deterministic across machines. Rejected.

## Consequences

- The base install grows by Babel's wheel. It is pure Python.
- Output depends on the CLDR version, so Babel is pinned to a minor range and golden tests catch
  changes when it is upgraded.
