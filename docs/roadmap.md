# Roadmap

> Milestones are in order but have no dates. Each one ends with a release to PyPI and an updated
> [`CONFORMANCE.md`](../CONFORMANCE.md).

## Upstream prerequisites

These changes belong in `legaldown-validator` (the core package) or in the specification, not
here.

| # | Where | Change | Status |
|---|---|---|---|
| U1 | validator | **Keep nested list structure** in the model ([#14](https://github.com/ForLegalAI/legaldown-validator/issues/14)) | Done in [validator#61](https://github.com/ForLegalAI/legaldown-validator/pull/61); the renderer nests lists from it (v0.2) |
| U2 | validator | **Source positions** (line numbers) on sections, blocks, and diagnostics ([#27](https://github.com/ForLegalAI/legaldown-validator/issues/27)) | Done for the validator's diagnostics in 0.3.0 ([validator#85](https://github.com/ForLegalAI/legaldown-validator/pull/85)); the CLI prints them. 0.4.0 makes a block's, a list item's and a heading's line public (`Document.line_of`, `Document.layout()`) and the line of a frontmatter key (`Document.source_map`). The renderer uses them: every diagnostic it reports names its file and line (v0.4, §16.9). Still open: the line of a directive within a block, so the renderer reports the first line of the block ([#93](https://github.com/ForLegalAI/legaldown-validator/issues/93)) |
| U3 | validator | **Public API** ([#26](https://github.com/ForLegalAI/legaldown-validator/issues/26)) for what the renderer imports from submodules, and the validator's own **placed markers** and **template decision** | Done. Mostly in 0.3.0 ([validator#90](https://github.com/ForLegalAI/legaldown-validator/pull/90)): `placed_markers` and `is_template` on the result. The rest in 0.4.0: quote and drafting-note content, code content, and the answers-file reader ([#93](https://github.com/ForLegalAI/legaldown-validator/issues/93), [#88](https://github.com/ForLegalAI/legaldown-validator/issues/88)). The reader is on `legaldown`, the rest in the tooling modules `legaldown.syntax` and `legaldown.grammar`. The renderer adopted it and dropped `validator_bridge.py`. The dependency stays pinned to one minor version, since before 1.0 a minor release may change the API |
| U1b | validator | **Parser gaps** the renderer inherits. Fixed in validator 0.3.0: list markers (#21), tables (#22, #44), comments and HTML blocks (#23, #59), the signature-block cutoff (#24), empty comments (#28), indented code (#9, #41), empty list items (#46), content after a nested list (#64), nested marker changes (#65), headings in quotes and items (#78), raw-html (#40), hard breaks (#25). Still open: link reference definitions ([#92](https://github.com/ForLegalAI/legaldown-validator/issues/92)) | Mostly done; adopted in v0.2 (validator 0.3.0). Listed in `CONFORMANCE.md` |
| U4a | validator | **Specification 0.2** (templates, §15) | Done in 0.2.0 |
| U4b | validator | Template **assembly** with an answers set (§15.7, [#30](https://github.com/ForLegalAI/legaldown-validator/issues/30)) | Done in [validator#55](https://github.com/ForLegalAI/legaldown-validator/pull/55); the renderer uses it (v0.2) |
| U5 | specification | A **rendering fixtures corpus**: source + style settings → expected plain-text output | Not started. The plain-text writer is designed to be its oracle |
| U6 | validator | **Link reference definitions** (`[label]: url`) in the document model; they render as paragraphs today ([#92](https://github.com/ForLegalAI/legaldown-validator/issues/92)) | Open. Listed in `CONFORMANCE.md` |
| U7 | validator | **`path-not-relative` and `path-outside-root`** (§2.3, §16.2) are not implemented in 0.4 | Open. Meanwhile the renderer reports both for every file reference itself (v0.4), and leaves its report out where the validator names the same path |
| U8 | validator | **`definition-circular`** (§16) is not implemented in 0.4 | Open. The renderer renders terms that refer to themselves without looping and warns with its own `render-term-limit` and `render-designation-cycle`, not the specification's rule |
| U9 | validator | **Table rows wider than the header** lose their extra cells inside the validator, with no diagnostic | Open. Listed in `CONFORMANCE.md` |

## Milestones

### v0.1 — HTML and text, specification 0.2

- The full pipeline: parse and validate, build, resolve, write
- All four numbering schemes; list enumeration; paragraph numbering; item and paragraph anchors
  with designations ("2.1(b)")
- Every directive, with every failure marker
- Template view: conditions, `{{choose:}}`, drafting notes, alternatives sharing a number
- Title block, attachment placeholders, signature blocks
- A table of contents (`contents` style setting) and the final check (`--final`, §15.9)
- Style templates: layering, `extends`, `--set`, validation, and three built-in styles; labels in
  English, Czech, German, French, Polish, and Slovak
- `text` and `html` writers; the `legaldown-render` CLI
- Golden tests, and a conformance run over the specification's examples and fixtures

### v0.2 — Templates with answers

- Rendering with an answers set, through the core package's assembly (U4b)
- `--answers answers.yaml` on the CLI
- Nested lists, with designations such as "2.1(b)(i)" (U1)
- The validator's CommonMark block model: headings in items and quotes, hard line breaks, and
  diagnostics with line numbers
- Built on `legaldown-validator` 0.3.0; the first release on PyPI

### v0.3 — Validator 0.4.0 (released)

- Built on `legaldown-validator` 0.4.0 and only its public API (`legaldown`, `legaldown.syntax`,
  `legaldown.grammar`); `validator_bridge.py` is removed (U3)
- A deprecated validator API fails the tests, so a release never calls what the next minor
  version removes
- Rendered output is unchanged, except that a comment-only quote past the validator's nesting
  depth no longer renders an empty paragraph

### v0.4 — Hardening (next release)

Rendering what other people send, and reporting problems where they are. No new output format.
[`CONFORMANCE.md`](../CONFORMANCE.md) has the exact behaviour of each item.

- **Values.** Durations never print a CLDR unit id; long amounts are not rounded; every written
  fraction digit is kept (`"1.500"` EUR is "€1.500"); a fraction beyond Python's 4300-digit limit
  still renders
- **CLI and API.** Documents are read as UTF-8; output is UTF-8 with LF line endings; problems
  with the document, style or options end as a message and an exit code, not a traceback.
  `OptionsError` for unusable options; it and `StyleError` are `RenderError`s
- **Links (§8.7).** A scheme allow-list: any other link renders as its text with the Warning
  `render-link-refused`
- **File references (§2.3).** Every one is checked, not only images: `path-not-relative`, and
  `path-outside-root` where a document root is set (`RenderOptions.document_root` and
  `document_path`, CLI `--root`). The checks are lexical; no file is read
- **Untrusted mode.** `RenderOptions.untrusted` / `--untrusted` requires a document root and loads
  the style untrusted (built-in names, plain CSS values only, size limits). Whatever the mode, a
  style value that could break out of the stylesheet is refused
- **Diagnostics with a place (§16.9).** Every renderer diagnostic names its file and line (the
  block's, a list item's marker, a heading's, or a frontmatter key's), and the CLI prints
  `file:line:` (U2)
- **`none` scheme (§13.3).** A reference's designation is the target's rendered heading
- **Defined terms.** Terms that refer to themselves render without looping; a work budget, a depth
  limit and a text cap (`render-term-limit`, `render-term-too-long`) bound the rest, and so do
  designations (`render-designation-cycle`, `render-designation-limit`)
- **Plain-text writer.** Faithful to the document's structure (quotes, list items with several
  blocks, tables, attachment separators), as it is meant to be the oracle of the fixtures corpus
  (U5)
- **Style templates.** `{field}` names in text settings are validated (`labels.condition` must
  contain `{condition}`), `extends` cycles are detected, and `--print-style` dumps are
  self-contained
- **Project.** CI also runs on Windows, and publishing to PyPI waits for CI

### v0.5 — DOCX

- `docx` writer behind the `[docx]` extra
- To decide first: static numbers, or Word-native numbering with `REF` fields
  ([ADR 0004](decisions/0004-html-first.md))

### v0.6 — PDF

- Choose the engine ([ADR 0004](decisions/0004-html-first.md)). The leading option is WeasyPrint
  on the HTML writer; its print stylesheet already exists
- Running headers and footers, and page numbers, from the style's `page` settings

### Later — Full level

Includes, attachment contents with per-attachment numbering scopes and cross-scope designations
(§13.3, §13.8), amendments, and bilingual rendering. These need file access under a configured
document root (§2.3). The renderer can now be given a root (v0.4: `document_root`, `--root`), which
bounds a reference but reads no file; reading files should still come from the core package first.

## Not planned

- **Editing or round-tripping from output.** The source is the only document of record.
- **A JavaScript port.** A browser-side renderer for live preview may come later, as a separate
  package tested against the same fixtures.
