# 0003. Resolve once, into a format-neutral document

- Status: Accepted
- Date: 2026-09-23

## Context

Most of §13 is independent of the output format: numbering, enumeration, designations, reference
qualification across attachment scopes, term and party display text, locale formatting, and
failure markers. If each writer implemented it, HTML, DOCX, and PDF could each number or
designate the same clause differently, and every new format would reimplement §13.

## Decision

The pipeline has an explicit **resolve** stage. It turns the render tree into an immutable
**resolved document**, where every node carries its final number, designation, display text,
output anchor, and any failure marker. Writers take only the resolved document, and they contain
no LegalDown rules.

Unresolved and resolved directive nodes are separate types, so passing an unresolved tree to a
writer is a type error, not a runtime surprise.

## Consequences

- "4.2(b)" is identical in every format by construction.
- A new format is purely layout work.
- Resolution is tested once, mostly through the plain-text writer, which works as its oracle.
- Anchor disambiguation for single-namespace formats (§5.6) is decided once, in the `anchors`
  resolver.
- Cost: formats with their own dynamic numbering, such as Word fields or CSS counters, get static
  numbers by default. If DOCX needs Word-native numbering ([0004](0004-html-first.md)), the
  resolved document keeps the structure (levels, list nesting, target anchors) so a writer can
  choose to emit fields, but the resolved numbers remain the reference.
- 2026-10-03: the resolved/unresolved check is made at runtime, not by the types: `Inline` holds
  both kinds, and `assert_resolved` raises `InternalError` if an unresolved node reaches a writer.
  Anchors are made by the resolver itself (`Resolver._anchor`, where the first unit to take a
  name keeps it); there is no separate `anchors` resolver.
