# Conformance

`legaldown-render` 0.1.0 targets **Level 2 — Rendering** of the LegalDown specification **0.2**
(§17.3). Core parsing and validation come from `legaldown-validator` 0.2.0, which claims Level 1
— Core. Its own [CONFORMANCE.md](https://github.com/ForLegalAI/legaldown-validator/blob/main/CONFORMANCE.md)
lists the Core rules it covers.

It is checked against the specification's own examples and fixtures corpus (`tests/conformance/`):

- every document renders to HTML and to text
- every internal link resolves to an anchor that exists
- each invalid fixture renders its rule's failure marker

## Rendering requirements (§17.3)

| Requirement | Status |
|---|---|
| Section numbering generated at render time, configurable per job (§13.1) | ✅ `decimal`, `legal-outline`, `mixed`, `none`, and custom level formats; per job with `--set numbering.scheme=…` |
| Resolution of every §11.1 directive except `{{include:}}`, with every failure marker (§6.3, §7.3, §13.3–§13.5, §15.8) | ✅ |
| `{{include:}}` below Full (§17.5) | ✅ Renders `[NOT PROCESSED: include path]` with a Warning (`render-not-processed`) |
| Party and side display rules (§3.6) | ✅ |
| At least one output format (§13.6) | ✅ HTML (recommended) and plain text (optional) |
| Comment stripping (§8.6) | ✅ |
| Template view without answers (§15.8) | ✅ Conditions marked with style-defined labels; every `{{choose:}}` phrase shown as `[a / b]`; drafting notes styled; alternatives share a number |
| `{{attach:}}` resolves to the declared title without reading the file | ✅ Linked to an attachment placeholder, or to the file when placeholders are omitted |

## SHOULD and MAY features

| Feature | Status |
|---|---|
| List enumeration (§13.2) | ✅ Configurable per depth; can be disabled. Ordered lists are always renumbered |
| Paragraph numbering and section-qualified items (§13.2) | ✅ Style settings; the `continental` style uses both |
| Style templates in a separate file (§13.7) | ✅ YAML, layered and validated; see [docs/style-templates.md](docs/style-templates.md) |
| Signature blocks (§2.2) | ✅ One per party, with `legal_name` and each representative |
| Labels following the document language (§13.7) | ✅ Built in for `en` and `cs`; any label can be set by a style |
| `ref-not-enumerated` Warning (§6.3, §16.3) | ✅ |
| `raw-html` Warning; raw HTML never emitted (§8.7) | ✅ |
| Automatic term recognition (§7.4, MAY) | ❌ Not supported |
| Extended tables through raw HTML (§9.2, MAY) | ❌ Not supported; the HTML is dropped with the Warning |
| Question prompts next to blanks (§15.8, MAY) | ✅ `placeholders.show_prompt` |

## Known limitations

- **Templates with answers.** Assembly (§15.7) is a Core capability that `legaldown-validator`
  0.2.0 does not provide yet, so a template always renders as its template view.
- **Diagnostics have no line numbers**, because the validator's diagnostics do not carry them yet.
- **Malformed directives** (a §11.2 grammar violation) render their type's marker without a value,
  for example `[INVALID AMOUNT]`. The specification defines no marker for this case.
- **Nested lists** are parsed by markdown-it-py until the validator's model keeps them
  ([legaldown-validator#14](https://github.com/ForLegalAI/legaldown-validator/issues/14)). An
  outline check guarantees both parsers agree on the document's sections.

## Beyond this level (Full, §17.4)

These are not processed, and each renders visibly rather than being dropped:

- includes (§12)
- attachment contents and per-attachment numbering scopes (§13.8)
- cross-scope reference qualification (§13.3)
- amendment processing (§7.5)
- bilingual sets (§14)
- file existence checks
