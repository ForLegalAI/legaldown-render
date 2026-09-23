# Roadmap

> Milestones are in order but have no dates. Each one ends with a release to PyPI and an updated
> `CONFORMANCE.md`.

## Before the first line of code: upstream prerequisites

The renderer depends on `legaldown-validator` (the core package) and on the specification. These
changes belong there, not here. They are listed in the order that unblocks the most work.

| # | Where | Change | Why the renderer needs it |
|---|---|---|---|
| U1 | validator | **Keep nested list structure** in the model. Today `- a` / `  - b` parse as two sibling items | Enumeration (a)/(i)/(A) (§13.2) and item anchors at any depth (§5.7). This also matters for Core validation of item anchors |
| U2 | validator | **Source positions** (line spans) on sections and blocks | Lets the build stage align the CommonMark tree with the validator's outline exactly, and gives diagnostics precise lines |
| U3 | validator | **Public helpers** for identifier generation (§5.3, §5.5) and definition resolution (§7.3) | The renderer must compute the same anchors and term targets the validator checks, never its own version of them |
| U4 | validator | **Specification 0.2** (templates, §15) and template **assembly** (§15.7) | The template view is a Rendering MUST (§17.3). Assembly is Core and belongs in the core package |
| U5 | specification | A **rendering fixtures corpus** (`fixtures/rendering/`): source + style settings → expected plain-text output | A neutral definition of correct numbering and designations that any renderer can be tested against, like the validation corpus |

U1–U3 block v0.1. U4 blocks v0.2. U5 is not blocking but makes conformance claims checkable.

## Milestones

### v0.1 — HTML, specification 0.1

The first usable renderer for single, non-template documents.

- Pipeline stages 1, 3, 4, and 5 ([architecture](architecture.md))
- All four numbering schemes, list enumeration, and a single numbering scope (attachments render
  as titles only, which is what Rendering level requires)
- Resolution of `{{ref:}}`, `{{term:}}`, `{{def:}}`, `{{party:}}`, `{{side:}}`, `{{attach:}}`,
  and every field spec, with every failure marker
- Title block, preamble, and basic signature blocks
- `text` and `html` writers
- `default` style template, the template loader, and validation
- `legaldown-render` CLI
- Golden tests over every example in the specification repository
- `CONFORMANCE.md` claiming Rendering at specification 0.1, with any gaps named

### v0.2 — Specification 0.2: templates

- Template view (§15.8): conditional units, `{{choose:}}`, drafting notes, template labels
- Rendering with an answers set (through the core package's assembly)
- Item and paragraph anchors with section-decimal items and paragraph numbering (§5.7, §13.2)
- `continental` and `outline` built-in templates

### v0.3 — DOCX

- `docx` writer behind the `[docx]` extra
- Decide first: static numbers, or Word-native numbering with `REF` fields so lawyers can keep
  editing in Word ([ADR 0004](decisions/0004-html-first.md))
- Reference-document styling (`docx.reference_docx`)

### v0.4 — PDF

- Choose the engine ([ADR 0004](decisions/0004-html-first.md)). WeasyPrint on the HTML writer is
  the leading option. The alternatives are Typst, or converting the DOCX output
- Page layout, running headers and footers, and page numbers from the style template

### Later — Full level

Includes, attachment contents with per-attachment numbering scopes (§13.8), amendments, and
bilingual rendering such as side-by-side columns. These need a configured document root and file
access (§2.3), which should come from the core package first.

## Not planned

- **Editing or round-tripping from output.** Output is one-way. The source is the only document
  of record.
- **A JavaScript port.** A browser-side renderer for live preview may be worth doing later. It
  would be a separate package tested against the same fixtures corpus.
