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
| Comment stripping (§8.6) | ✅ Including a comment across blank lines, up to the end of its section (see below) |
| Template view without answers (§15.8) | ✅ Conditions marked with style-defined labels; every `{{choose:}}` phrase shown as `[a / b]`; drafting notes styled; alternatives share a number |
| `{{attach:}}` resolves to the declared title without reading the file | ✅ Linked to an attachment placeholder, or to the file when placeholders are omitted |

## SHOULD and MAY features

| Feature | Status |
|---|---|
| List enumeration (§13.2) | ✅ Configurable per depth; can be disabled. Ordered lists are always renumbered. Lists have one level until the validator keeps nesting (see below) |
| Paragraph numbering and section-qualified items (§13.2) | ✅ Style settings; the `continental` style uses both |
| Style templates in a separate file (§13.7) | ✅ YAML, layered and validated; see [docs/style-templates.md](docs/style-templates.md) |
| Signature blocks (§2.2) | ✅ One per party, with `legal_name` and each representative |
| Labels following the document language (§13.7) | ✅ Built in for `en`, `cs`, `de`, `fr`, `pl`, and `sk`; any label can be set by a style |
| `ref-not-enumerated` Warning (§6.3, §16.3) | ✅ |
| `raw-html` Warning; raw HTML never emitted (§8.7) | ✅ No tag is ever emitted. Text between an HTML block's tags renders as text until the validator's model recognises HTML blocks (see below) |
| Automatic term recognition (§7.4, MAY) | ❌ Not supported |
| Extended tables through raw HTML (§9.2, MAY) | ❌ Not supported; the tags are dropped with the Warning, and the cell text renders as paragraph text |
| Question prompts next to blanks (§15.8, MAY) | ✅ `placeholders.show_prompt` |
| Final check (§15.9, SHOULD) | ✅ `--final` / `final=True`: the validator's `placeholder-unfilled` and `template-construct-present` Errors; with `--strict`, the document is refused |

## Known limitations

- **Templates with answers.** Assembly (§15.7) is a Core capability that `legaldown-validator`
  0.2.0 does not provide yet, so a template always renders as its template view.
- **Diagnostics have no line numbers**, because the validator's diagnostics do not carry them yet.
- **Malformed directives** (a §11.2 grammar violation) render their type's marker without a value,
  for example `[INVALID AMOUNT]`. The specification defines no marker for this case.
- **One parser.** The renderer builds from the validator's document model only
  ([ADR 0007](docs/decisions/0007-one-parser-validator-model.md)). Anchors, markers, and the
  template view therefore always agree with the validator's diagnostics. The price is that some
  structure renders as the validator's model holds it, until that model keeps more
  ([legaldown-validator#14](https://github.com/ForLegalAI/legaldown-validator/issues/14),
  roadmap U1–U2):
  - **Nested lists** render as one level of items, so an item is referenced as "2.1(c)", never
    "2.1(b)(i)" ([#14](https://github.com/ForLegalAI/legaldown-validator/issues/14), [#16](https://github.com/ForLegalAI/legaldown-validator/issues/16)). The resolver and writers already handle nesting.
  - **`*` and `+` bullets and `1)` ordered lists** render as one paragraph ([#21](https://github.com/ForLegalAI/legaldown-validator/issues/21)).
  - **Tables:** column alignment is not kept, and an escaped pipe (`\|`) or a pipe inside a
    code span splits a cell ([#22](https://github.com/ForLegalAI/legaldown-validator/issues/22)). Rows are padded or trimmed to the header's width.
  - **Line breaks** within a paragraph are joined, and a backslash hard break shows as a literal
    backslash. Link reference definitions (`[label]: url`) are not resolved
    ([#25](https://github.com/ForLegalAI/legaldown-validator/issues/25)).
  - **A heading inside a multi-line HTML comment** is a section: the comment ends at the heading,
    and its closing `-->` shows as text. In an **HTML block**, every tag is dropped and reported (`raw-html`), but the
    text between the tags renders as paragraph text ([#23](https://github.com/ForLegalAI/legaldown-validator/issues/23)).
  - **A comment across blank lines** is stripped up to its `-->` (the one place the renderer
    reads past the model), but the model still holds its content as blocks. So a `{#id}` inside
    it is still a valid target for the validator, and a reference to it renders
    `[BROKEN REF: id]` with no Error. When the comment ends inside a quote, code block, or table,
    the rest of that block renders as plain paragraph text ([#23](https://github.com/ForLegalAI/legaldown-validator/issues/23)).
  - **After an empty comment** (`<!-->`, `<!--->`), a directive before a later `-->` on the same
    line is shown as its source text, unresolved and unchecked ([#28](https://github.com/ForLegalAI/legaldown-validator/issues/28)).
  - **Everything after a `# Signature Block {#signature-block}` heading** is dropped
    ([#24](https://github.com/ForLegalAI/legaldown-validator/issues/24)).
  - **Indented code blocks** are read as paragraphs ([#9](https://github.com/ForLegalAI/legaldown-validator/issues/9)).

## Beyond this level (Full, §17.4)

These are not processed, and each renders visibly rather than being dropped:

- includes (§12)
- attachment contents and per-attachment numbering scopes (§13.8)
- cross-scope reference qualification (§13.3)
- amendment processing (§7.5)
- bilingual sets (§14)
- file existence checks
