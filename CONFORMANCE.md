# Conformance

`legaldown-render` 0.3.0 targets **Level 2 — Rendering** of the LegalDown specification **0.2**
(§17.3). Core parsing and validation come from `legaldown-validator` 0.4, which claims Level 1
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
| A heading in a list item or a quote | ✅ Shown as a bold line: it is not a section (§4.1), so it has no number or anchor |
| Comment stripping (§8.6) | ✅ Inline comments, and comment blocks across any number of lines. As in CommonMark, a comment block left unclosed runs to the end of the document |
| Templates with answers (§15.8) | ✅ Assembled first with `legaldown-validator`'s Assembly capability (§15.7), then rendered: `answers=` / `--answers answers.yaml`. A template that needs other files is refused (see below) |
| Template view without answers (§15.8) | ✅ Conditions marked with style-defined labels; every `{{choose:}}` phrase shown as `[a / b]`; drafting notes styled; alternatives share a number |
| `{{attach:}}` resolves to the declared title without reading the file | ✅ Linked to an attachment placeholder, or to the file when placeholders are omitted |

## SHOULD and MAY features

| Feature | Status |
|---|---|
| List enumeration (§13.2) | ✅ Configurable per depth; can be disabled. Ordered lists are always renumbered. Nested lists keep their structure, so a nested item is referenced as "2.1(b)(i)" |
| Paragraph numbering and section-qualified items (§13.2) | ✅ Style settings; the `continental` style uses both |
| Style templates in a separate file (§13.7) | ✅ YAML, layered and validated; see [docs/style-templates.md](docs/style-templates.md) |
| Signature blocks (§2.2) | ✅ One per party, with `legal_name` and each representative |
| Labels following the document language (§13.7) | ✅ Built in for `en`, `cs`, `de`, `fr`, `pl`, and `sk`; any label can be set by a style |
| A conditional unit's condition is shown (§15.8, MUST) | ✅ Also for a custom style: `labels.condition` must contain `{condition}`, or the style is refused |
| `ref-not-enumerated` Warning (§6.3, §16.3) | ✅ |
| `raw-html` Warning; raw HTML never emitted (§8.7) | ✅ Nothing is emitted: an HTML block is dropped whole, and an inline tag is dropped with its text kept. The Warning is the validator's, once per block or text that holds raw HTML other than comments |
| Automatic term recognition (§7.4, MAY) | ❌ Not supported |
| Links (§8.7) | ✅ Every link is a hyperlink in HTML and shows `text <url>` in plain text, whatever its scheme (`ftp:`, `sip:`, `urn:`, ...), except one whose URL is empty or uses `javascript:`, `vbscript:`, `file:`, or `data:`, which shows its text alone. A link with no text shows its URL; the text writer omits `<url>` for an autolink |
| Images and file-reference paths (§8.7, §2.3) | ✅ A relative path (`../` included, as §2.3 defines no default document root) renders `<img>` in HTML and its alt text in plain text. A path with a URI scheme (`data:` too), an absolute one (`/`, `\`, `//host`), or a Windows drive path is never fetched or embedded: the alt text is shown and the Error `path-not-relative` is reported, which `--strict` refuses. An empty path shows the alt text with no diagnostic |
| Plain-text layout (§13.6, optional) | ✅ `attachments.separator` (§13.8): `none` prints no `---`; `rule` and `page-break` both print one before the attachments heading and between consecutive attachments, so the heading stays with the first (text has no pages). One quote stays one quote (blank lines keep the `>`), and the blocks of a list item are set apart by blank lines and indented under the item's label, whatever its condition label. A pipe in a table cell is backslash-escaped, and the separator row marks column alignment (`:---`, `:---:`, `---:`). A label a style blanks drops its `label: ` prefix in the title block, party details and signature blocks (a signature row with neither label nor value is omitted); HTML keeps an empty `<dt>` so its grid layout and the `<dl>` stay valid |
| Extended tables through raw HTML (§9.2, MAY) | ❌ Not supported; the HTML table is dropped with the Warning |
| Visual blank for a placeholder (§13.5 rule 6) | ✅ `placeholders.blank`; a blank that is empty or only whitespace renders `[TBD: id]` |
| Question prompts next to blanks (§15.8, MAY) | ✅ `placeholders.show_prompt` |
| Final check (§15.9, SHOULD) | ✅ `--final` / `final=True`: the validator's `placeholder-unfilled` and `template-construct-present` Errors; with `--strict`, the document is refused |

## Known limitations

- **Assembly reads no other files.** A template with include fragments, LegalDown attachment
  files, or `translations` is refused when rendered with answers, as §17.6 requires below Full.
  A template with Errors is refused too, since §15.7.2 defines no output for one.
- **Line numbers.** The validator's diagnostics name their line, and the CLI prints it. The
  renderer's own (`render-ref-ambiguous`, `ref-not-enumerated`, `render-not-processed`) do not
  yet ([validator#93](https://github.com/ForLegalAI/legaldown-validator/issues/93)).
- **`path-not-relative` for images.** The renderer reports the Core rule `path-not-relative`
  (§2.3, §16.2) for an image path, because the validator (0.4) does not check image paths; it
  leaves its own report out when the validator names the same path. The Error has no line number.
  The containment rule (`path-outside-root`) is not checked: no document root is configured.
- **Nesting limit.** The validator reads lists 64 levels deep and quotes 16; deeper, an item's or
  quote's content is its text. The renderer follows it, and refuses (`DocumentError`) a document
  whose lists, quotes, and inline formatting (emphasis, links) together nest more than 100 levels,
  while building it and before resolving or writing it, so no document can exhaust the stack.
- **Comment-only items and quotes.** A list item whose blocks are all comments or raw HTML is
  left out (§8.6: the author removed it), and so is a list left with no items and a quote that
  holds only comments. Numbers and designations are those of the items that remain. An item with
  no content at all keeps its place (CommonMark), and so does an empty drafting note, whose label
  is the template construct (§15.6).
- **`{{term:}}` text.** A `{{term:}}` shows the term as rendered at its first definition in the
  body, with its Markdown resolved (`"A *b* c" {{def: abc}}` is "A b c"), not the term's raw
  source; `label=` overrides it. A definition not in the body (a template's other alternative)
  falls back to the validator's term text.
  A term that refers to itself, directly or through other terms (`"A {{term: a}}" {{def: a}}`), is
  rendered without looping: the inner reference shows its `label=`, else the definition id. The
  validator (0.4.0) does not report these cycles; the specification's `definition-circular`
  Warning (§16) is not emitted yet.
- **Ambiguous references.** Each list starts again at its first number, as contracts are usually
  drafted, so two lists in one section can both have an item "(a)", and a style can number a
  paragraph or an item like a subsection ("1.1"). A `{{ref:}}` to a section, paragraph, or list
  item whose designation another of them also reads as renders with a `render-ref-ambiguous`
  Warning. It is a hint: it is left out when the two can never appear together (alternatives,
  exclusive conditions), but the condition the reference itself stands under is not considered.
  Lists in quotes and drafting notes are not counted, and a second use of an identifier is left
  to the validator's `anchor-duplicate`.
- **Labels under the `none` scheme.** A style label that shows `{section}` (such as the default
  paragraph label `{section}.{n}`) shows the section's heading text there, while `{{ref:}}`
  writes the §13.3 designation ("Termination 1"), so the two can differ.
- **Malformed directives** (a §11.2 grammar violation) render their type's marker without a value,
  for example `[INVALID AMOUNT]`. The specification defines no marker for this case.
- **One parser.** The renderer builds from the validator's document model only
  ([ADR 0007](docs/decisions/0007-one-parser-validator-model.md)). Anchors, markers, and the
  template view therefore always agree with the validator's diagnostics. The price is that some
  structure renders as the validator's model holds it, until that model keeps more (roadmap
  U1b):
  - **Link reference definitions** (`[label]: url`) render as paragraphs, and the links that use
    them as text ([#92](https://github.com/ForLegalAI/legaldown-validator/issues/92)).

## Beyond this level (Full, §17.4)

These are not processed, and each renders visibly rather than being dropped:

- includes (§12)
- attachment contents and per-attachment numbering scopes (§13.8)
- cross-scope reference qualification (§13.3)
- amendment processing (§7.5)
- bilingual sets (§14)
- file existence checks
