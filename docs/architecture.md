# Architecture

> Status: **draft, not implemented.** This describes the intended design. The decisions behind it
> are in [decision records](decisions/). Where this page and an accepted decision record disagree,
> the decision record wins, and this page should be fixed.

Read [Concepts](concepts.md) first for the vocabulary.

---

## The pipeline

```
 source .lgd ─┐
 answers ─────┤  (templates only)
              ▼
   ┌──────────────────────┐
   │ 1. Parse & validate  │  legaldown-validator: Document model + Core diagnostics
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │ 2. Assemble          │  only with an answers set (§15.7); otherwise template view
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │ 3. Build             │  source + Document → render tree (blocks, inlines, directive nodes)
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐  style template ─┐
   │ 4. Resolve           │ ◄────────────────┘  numbering, enumeration, anchors, references,
   └──────────┬───────────┘                     terms, fields, parties → resolved document
              ▼                                 + Rendering-level diagnostics
   ┌──────────────────────┐
   │ 5. Write             │  html │ text │ docx (later) │ pdf (later)
   └──────────┬───────────┘
              ▼
   RenderResult(output, diagnostics)
```

Each stage has a single job and hands an immutable value to the next one. Only stage 4 knows
about the style template's legal settings. Only stage 5 knows about file formats.

### 1. Parse and validate

This stage calls `legaldown.parse_document` and `legaldown.validate_document` from
`legaldown-validator`. The result is:

- the **metadata**: title, sides, parties, attachments, `field_types`, language
- the **section outline** and identifiers, including auto-generated ones (§5.3) and duplicate
  handling (§5.5)
- the **definitions** table (§7)
- the **Core diagnostics**

The renderer never reimplements any of this ([ADR 0002](decisions/0002-one-parser.md)).

### 2. Assemble (templates only)

Given an answers set, the template is assembled into a plain document first (§15.7), and that
document is rendered. Assembly is a text-to-text transformation that the specification places at
Core (the fixtures' `assembly/` cases), so it belongs in the core package. The renderer calls it
and does not own it. Without answers, stages 3 and 4 produce the template view (§15.8) instead.

Assembly depends on the core package supporting specification 0.2 first. See the
[roadmap](roadmap.md).

### 3. Build the render tree

This stage turns the document into a **render tree**: a format-neutral tree that is structurally
complete. Nested lists stay nested, inline formatting becomes nodes, and every directive is a
typed node that has not been resolved yet.

The current `legaldown.Document` model is designed for validation and round-trip editing, and it
loses some things a renderer needs:

- Nested list items are flattened into siblings.
- Continuation lines are joined.
- Inline text is kept as raw strings.

So the tree is built from two sources:

| From | What |
|---|---|
| `legaldown-validator` | Metadata, section identifiers and titles, definitions, directive lexing (`legaldown.directives`), and the §11.2 grammar |
| A CommonMark parser ([markdown-it-py](https://github.com/executablebooks/markdown-it-py)) | Block structure inside sections (nested lists, quotes, tables, code) and inline structure (emphasis, links, code spans) |

A small markdown-it plugin recognises `{{…}}` and `{#…}` and hands them to the validator's
lexer, so the directive grammar is defined in one place only.

The two parsers must agree on the outline. After building, the builder checks that the headings
it found match the validator's sections one-to-one (level, title, identifier). A mismatch is an
**internal error**, reported as a bug. It is never silently papered over.

This hybrid is a stopgap. The long-term fix is for the core package to expose what the renderer
needs; [ADR 0002](decisions/0002-one-parser.md) and the [roadmap](roadmap.md) list those
upstream changes.

### 4. Resolve

Resolution computes everything the specification leaves to render time. It runs as an ordered
set of small resolvers. Each one reads the tree plus the style template and records its results
for the next:

| Resolver | Spec | Produces |
|---|---|---|
| `scopes` | §13.8 | The main body and each attachment as numbering scopes, depending on the template's restart setting |
| `numbering` | §13.1 | A number for every heading under the active scheme (`decimal`, `legal-outline`, `mixed`, `none`) |
| `enumeration` | §13.2 | A label for every list item: (a) / (i) / (A), section-qualified decimals, or plain bullets. Also paragraph numbers when enabled. Ordered-list numbers in the source are ignored |
| `anchors` | §5.6, §5.7 | A collision-free output anchor for every section, item, paragraph, and definition, for example `def-` prefixes for definitions |
| `references` | §6.3, §13.3 | The designation and link for each `{{ref:}}`, including the item path, the `none` scheme (heading text), and cross-scope qualification. Otherwise `[BROKEN REF: id]`. Emits `ref-not-enumerated` |
| `attachments` | §6.4 | `{{attach:}}` resolves to the declared `title` (Rendering level reads no attachment file) |
| `terms` | §7.3, §13.4 | Display text (the `label` or the defined term) and a link to the definition, or `[UNDEFINED: id]` |
| `parties` | §3.6, §13.5 | Display text for `{{party:}}` and `{{side:}}`, with the label and `legal_name` fallbacks |
| `fields` | §10, §13.5 | Dates, money, durations, custom fields, and placeholders formatted for the locale, or their failure markers |
| `template_view` | §15.8 | Conditional units marked, every `{{choose:}}` phrase shown, drafting notes flagged |
| `unprocessed` | §17.5 | `[NOT PROCESSED: …]` markers, such as for `{{include:}}` |
| `signatures` | §2.2, §3.6 | Signature block nodes from frontmatter, when the template asks for them |

The output is the **resolved document**: the same tree, frozen, where every node carries its
final display text, number, and anchor. From here on, nothing needs the style template's legal
settings, the definitions table, or the specification.

Resolvers are pure functions over data, so each one can be tested in isolation with a
hand-built tree.

### 5. Write

A writer turns the resolved document into bytes. Writers follow one rule:

> **A writer contains no LegalDown rules.** If a writer needs to know what a `{{ref:}}` means,
> how numbering works, or what a failure marker says, the logic is in the wrong place. It
> belongs in a resolver.

| Writer | Milestone | Notes |
|---|---|---|
| `text` | v0.1 | Plain text (§13.6, OPTIONAL). Cheap to write, easy to diff. Serves as the **test oracle** for resolution (see [Testing](#testing)) |
| `html` | v0.1 | A single self-contained file with inline CSS generated from the style template. Semantic markup (`<section>`, `<ol>`, `<a href>`). Every output anchor is a real `id` |
| `docx` | v0.3 | python-docx. Open question: static numbers vs Word-native numbering and `REF` fields. See [ADR 0004](decisions/0004-html-first.md) |
| `pdf` | v0.4 | Engine not chosen yet. The leading option is HTML plus paged-media CSS through WeasyPrint. See [ADR 0004](decisions/0004-html-first.md) |

## The render tree

The tree is a set of frozen dataclasses. It is sketched here so the shape can be reviewed before
code exists. Names are provisional.

```
ResolvedDocument
├── meta: title, subtitle, language, locale, style name
├── title_block             (title, subtitle, parties per side)
├── preamble: [Block]       (§4.4; unnumbered, not referenceable)
├── body: [Section]
├── attachments: [AttachmentPart]   (§13.8; title + separator, or placeholder)
└── signatures: [SignatureBlock]

Section      level, number ("4.2" or None), title: [Inline], anchor, children: [Section], blocks: [Block]

Block        Paragraph(inlines, number?, anchor?)
             List(ordered, items: [ListItem])
             ListItem(label "(b)", anchor?, blocks: [Block])       ← nesting kept
             Quote(blocks) · DraftingNote(blocks) · Conditional(condition, label, blocks)
             Table(headers, rows, alignments) · CodeBlock(text) · Rule

Inline       Text · Emphasis · Strong · Code · Link(url) · LineBreak
             CrossRef(display, target_anchor)       ← {{ref:}}
             TermRef(display, target_anchor)        ← {{term:}}
             DefinedTerm(display, anchor)           ← "Term" {{def:}}
             Value(kind, display)                   ← date / money / duration / field / party / side / attach
             Blank(id, display)                     ← placeholder
             Choice(options)                        ← template view only
             Marker(text, rule)                     ← every failure marker
```

Before resolution, the directive nodes hold the parsed `legaldown.directives.Directive` and no
display text. After resolution, they hold display text and targets. The unresolved and resolved
variants are separate types, so a writer cannot receive an unresolved node.

## Package layout

```
legaldown-render/
├── pyproject.toml
├── src/legaldown_render/
│   ├── __init__.py          public API: render(), RenderOptions, RenderResult, load_style()
│   ├── api.py               pipeline orchestration
│   ├── options.py           RenderOptions
│   ├── diagnostics.py       Rendering-level rules; reuses the validator's Diagnostic type
│   ├── tree/                render tree node types (unresolved and resolved)
│   ├── build/               stage 3: blocks.py, inlines.py, markdown_it plugin, outline check
│   ├── resolve/             stage 4: one module per resolver (see the table above)
│   ├── locale/              date, money, and duration formatting
│   ├── style/               style template schema, loader, validation, built-in templates
│   │   └── builtin/         default.yaml, continental.yaml, …
│   ├── writers/
│   │   ├── base.py          Writer protocol
│   │   ├── text.py
│   │   ├── html/            writer.py, default.css
│   │   ├── docx/            (v0.3)
│   │   └── pdf/             (v0.4)
│   └── cli.py               `legaldown-render` command
├── tests/
│   ├── unit/                one file per resolver and writer
│   ├── golden/              source → expected output snapshots
│   └── conformance/         the specification's fixtures corpus
└── docs/
```

The import name is `legaldown_render`, not `legaldown.render`. The validator's `legaldown`
package is a regular package with its own `__init__.py`, so another distribution cannot safely
add a subpackage to it.

## Dependencies

| Dependency | Why | Kind |
|---|---|---|
| `legaldown-validator` | The parser, model, lexer, and Core validation | runtime, pinned to a compatible minor range |
| `markdown-it-py` | CommonMark block and inline structure | runtime |
| `babel` | Formatting dates, numbers, currencies, and units from CLDR data. See [ADR 0005](decisions/0005-locale-formatting.md) | runtime (proposed) |
| `python-docx` | DOCX writer | extra: `[docx]` |
| PDF engine (not chosen yet) | PDF writer | extra: `[pdf]` |

Everything in the base install is pure Python. Heavy or native dependencies go behind extras, so
installing the HTML renderer never needs Pango, LibreOffice, or a TeX installation.

## Public API (sketch)

```python
render(source: str, *, to: str = "html", style: str | Style = "default",
       answers: Mapping | None = None, options: RenderOptions | None = None) -> RenderResult

render_document(document: legaldown.Document, ...) -> RenderResult   # already parsed

RenderResult
    output: str | bytes                # bytes for docx and pdf
    diagnostics: list[Diagnostic]      # Core and Rendering, in source order
    ok: bool                           # no errors
```

The public API is `render` and `render_document`. The tree and resolvers are importable for
advanced use but are not covered by a stability promise before 1.0.

## Errors and diagnostics

- **Document problems** (a broken reference, an invalid date): render a failure marker, add a
  diagnostic, and continue. `strict=True` raises a `RenderRefused` error that carries the
  diagnostics, and produces no output.
- **Configuration problems** (an invalid style template, an unknown output format): raise before
  rendering starts, with a message that names the setting.
- **Internal inconsistencies** (the outline check in stage 3): raise `InternalError` and ask for a
  bug report. Never produce plausible-looking wrong output.

Diagnostics use the validator's `Diagnostic` type and stable rule ids. Rules that only a renderer
can evaluate, such as `ref-not-enumerated` (it depends on the style template), are emitted by
the resolver responsible for them.

## Security

- Raw HTML in the source is never emitted (§8.7).
- Every text node is escaped for its target format.
- Link URLs must use an allowed scheme (`http`, `https`, `mailto`, relative, fragment). Anything
  else, such as `javascript:`, renders as plain text with a Warning.
- At Rendering level, the only files read are the document and the chosen style template. Full
  level's file access will require a configured document root (§2.3).
- Style templates are trusted configuration, not document content. A template can contain CSS,
  so a hosted service must not accept templates from untrusted users without sandboxing them.

## Testing

| Layer | What it checks |
|---|---|
| Unit | Each resolver on hand-built trees: numbering schemes, enumeration depth, designations, locale formats, and every failure marker in §13.3–§13.5 |
| Golden | Every example in the specification repository rendered to `text` and `html` and compared byte for byte with a committed snapshot. Snapshots are regenerated on purpose, never automatically |
| Conformance | The specification's fixtures corpus. Invalid fixtures must render their failure markers and diagnostics; valid fixtures must render with no errors |
| Property | Rendering never raises on a document that parses, output is deterministic, and every output link points to an anchor that exists |

The **plain-text writer is the resolution oracle**. It prints numbers, designations, and display
text with no layout noise, so most tests assert on text output and only the HTML-specific tests
look at markup. The same idea could give the specification a format-neutral rendering fixtures
corpus. See the [roadmap](roadmap.md).

## Versioning and compatibility

- The package declares `SPEC_VERSION` and `CONFORMANCE_LEVEL = "rendering"`, like the validator.
- `CONFORMANCE.md` lists every Rendering requirement that is not met yet. A gap is never left
  implicit.
- CI runs against the pinned `legaldown-validator` release and also against its `main` branch,
  so upstream model changes surface before release.
- The style template format has its own `version` field and is validated when loaded.
