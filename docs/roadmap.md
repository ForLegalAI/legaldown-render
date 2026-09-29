# Roadmap

> Milestones are in order but have no dates. Each one ends with a release to PyPI and an updated
> [`CONFORMANCE.md`](../CONFORMANCE.md).

## Upstream prerequisites

These changes belong in `legaldown-validator` (the core package) or in the specification, not
here.

| # | Where | Change | Status |
|---|---|---|---|
| U1 | validator | **Keep nested list structure** in the model ([#14](https://github.com/ForLegalAI/legaldown-validator/issues/14)) | Done in [validator#61](https://github.com/ForLegalAI/legaldown-validator/pull/61); the renderer nests lists from it (v0.2) |
| U2 | validator | **Source positions** (line numbers) on sections, blocks, and diagnostics ([#27](https://github.com/ForLegalAI/legaldown-validator/issues/27)) | Done for the validator's diagnostics in 0.3.0 ([validator#85](https://github.com/ForLegalAI/legaldown-validator/pull/85)); the CLI prints them. Still open: a block's line, for the renderer's own diagnostics ([#93](https://github.com/ForLegalAI/legaldown-validator/issues/93)) |
| U3 | validator | **Public API** ([#26](https://github.com/ForLegalAI/legaldown-validator/issues/26)) for what the renderer imports from submodules, and the validator's own **placed markers** and **template decision** | Mostly done in 0.3.0 ([validator#90](https://github.com/ForLegalAI/legaldown-validator/pull/90)): the renderer reads `placed_markers` and `is_template` from the result. Left in `validator_bridge.py`: `quote_content`, the fence helpers, the answers-file reader ([#93](https://github.com/ForLegalAI/legaldown-validator/issues/93)), and a drafting note's content ([#88](https://github.com/ForLegalAI/legaldown-validator/issues/88)). Until then, the dependency is pinned to one minor version |
| U1b | validator | **Parser gaps** the renderer inherits. Fixed in validator `main`: list markers (#21), tables (#22, #44), comments and HTML blocks (#23, #59), the signature-block cutoff (#24), empty comments (#28), indented code (#9, #41), empty list items (#46), content after a nested list (#64), nested marker changes (#65), headings in quotes and items (#78), raw-html (#40). hard breaks (#25). Still open: link reference definitions ([#92](https://github.com/ForLegalAI/legaldown-validator/issues/92)) | Mostly done; adopted with 0.3.0. Listed in `CONFORMANCE.md` |
| U4a | validator | **Specification 0.2** (templates, §15) | Done in 0.2.0 |
| U4b | validator | Template **assembly** with an answers set (§15.7, [#30](https://github.com/ForLegalAI/legaldown-validator/issues/30)) | Done in [validator#55](https://github.com/ForLegalAI/legaldown-validator/pull/55); the renderer uses it (v0.2) |
| U5 | specification | A **rendering fixtures corpus**: source + style settings → expected plain-text output | Not started. The plain-text writer is designed to be its oracle |

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

### v0.2 — Templates with answers (this release)

- Rendering with an answers set, through the core package's assembly (U4b)
- `--answers answers.yaml` on the CLI
- Nested lists, with designations such as "2.1(b)(i)" (U1)
- The validator's CommonMark block model: headings in items and quotes, hard line breaks, and
  diagnostics with line numbers
- Built on `legaldown-validator` 0.3.0; the first release on PyPI

### v0.3 — DOCX

- `docx` writer behind the `[docx]` extra
- To decide first: static numbers, or Word-native numbering with `REF` fields
  ([ADR 0004](decisions/0004-html-first.md))

### v0.4 — PDF

- Choose the engine ([ADR 0004](decisions/0004-html-first.md)). The leading option is WeasyPrint
  on the HTML writer; its print stylesheet already exists
- Running headers and footers, and page numbers, from the style's `page` settings

### Later — Full level

Includes, attachment contents with per-attachment numbering scopes and cross-scope designations
(§13.3, §13.8), amendments, and bilingual rendering. These need file access under a configured
document root (§2.3), which should come from the core package first.

## Not planned

- **Editing or round-tripping from output.** The source is the only document of record.
- **A JavaScript port.** A browser-side renderer for live preview may come later, as a separate
  package tested against the same fixtures.
