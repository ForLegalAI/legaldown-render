# Roadmap

> Milestones are in order but have no dates. Each one ends with a release to PyPI and an updated
> [`CONFORMANCE.md`](../CONFORMANCE.md).

## Upstream prerequisites

These changes belong in `legaldown-validator` (the core package) or in the specification, not
here.

| # | Where | Change | Status |
|---|---|---|---|
| U1 | validator | **Keep nested list structure** in the model ([#14](https://github.com/ForLegalAI/legaldown-validator/issues/14)) | Open. Until then lists render with one level of items (ADR 0007); the resolver and writers already handle nesting |
| U2 | validator | **Source positions** (line numbers) on sections, blocks, and diagnostics ([#27](https://github.com/ForLegalAI/legaldown-validator/issues/27)) | Open. Diagnostics have no line numbers in either package |
| U3 | validator | **Public API** ([#26](https://github.com/ForLegalAI/legaldown-validator/issues/26)) for what the renderer imports from submodules (all in `validator_bridge.py`): the lexer, markers, value checks, conditions, `is_template`, the CommonMark fence and indentation helpers, the answers-file reader, `IDENTIFIER_RE`, `KNOWN_CURRENCIES`. Better still, expose the validator's own **placed markers** and **template decision**, so the renderer uses them instead of mirroring those rules | Partly done: the template decision is the validator's `is_template()` (not yet public). Until the rest is public, the dependency is pinned to one minor version |
| U1b | validator | **Parser gaps** the renderer inherits. List markers (#21), tables (#22), comments and HTML blocks (#23), the signature-block cutoff (#24), empty comments (#28), and indented code (#9) are fixed in [validator#49](https://github.com/ForLegalAI/legaldown-validator/pull/49). Still open: hard breaks ([#25](https://github.com/ForLegalAI/legaldown-validator/issues/25)), indented code in quotes and items ([#41](https://github.com/ForLegalAI/legaldown-validator/issues/41)), tables without a leading pipe ([#44](https://github.com/ForLegalAI/legaldown-validator/issues/44)), empty list items ([#46](https://github.com/ForLegalAI/legaldown-validator/issues/46)) | Partly done. The renderer adopts #49 when it is released. Listed in `CONFORMANCE.md` |
| U4a | validator | **Specification 0.2** (templates, §15) | Done in 0.2.0 |
| U4b | validator | Template **assembly** with an answers set (§15.7, [#30](https://github.com/ForLegalAI/legaldown-validator/issues/30)) | Done in [validator#55](https://github.com/ForLegalAI/legaldown-validator/pull/55); the renderer uses it (v0.2) |
| U5 | specification | A **rendering fixtures corpus**: source + style settings → expected plain-text output | Not started. The plain-text writer is designed to be its oracle |

## Milestones

### v0.1 — HTML and text, specification 0.2 (this release)

- The full pipeline: parse and validate, build, resolve, write
- All four numbering schemes; list enumeration; paragraph numbering; item and paragraph anchors
  with designations ("2.1(b)"), one list level until U1
- Every directive, with every failure marker
- Template view: conditions, `{{choose:}}`, drafting notes, alternatives sharing a number
- Title block, attachment placeholders, signature blocks
- A table of contents (`contents` style setting) and the final check (`--final`, §15.9)
- Style templates: layering, `extends`, `--set`, validation, and three built-in styles; labels in
  English, Czech, German, French, Polish, and Slovak
- `text` and `html` writers; the `legaldown-render` CLI
- Golden tests, and a conformance run over the specification's examples and fixtures

### v0.2 — Templates with answers

- Rendering with an answers set, through the core package's assembly (U4b): done
- `--answers answers.yaml` on the CLI: done
- Released together with the validator release that ships the CommonMark block model and assembly

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
