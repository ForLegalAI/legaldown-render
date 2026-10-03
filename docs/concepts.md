# Concepts

This page covers what the renderer is responsible for, the principles behind its design, and the
vocabulary the other documents use. [Architecture](architecture.md) explains how it is built.

Section numbers such as §13.1 refer to the
[LegalDown specification](https://github.com/ForLegalAI/LegalDown/blob/main/spec/legaldown-spec.md).

---

## What a renderer is

A LegalDown source file describes structure and meaning, not appearance. It has no section
numbers, no reference text, no formatted dates, and no list letters. The renderer produces all of
these. Its inputs are:

- **one document**: the `.lgd` source
- **a style template**: how the output looks, including the numbering scheme, list enumeration,
  locale, fonts, and page layout (§13.7)
- **render options**: per-job settings such as the output format, a style override, and strictness
- **an answers set** (optional, templates only): the values used to assemble a template before
  rendering (§15.7)

Its outputs are the **rendered file** (HTML, later DOCX and PDF) and a list of **diagnostics**.

The source never changes during rendering. Given the same source, style template, and options,
the renderer always produces the same output.

## Scope: the Rendering conformance level

The specification defines three cumulative conformance levels (§17):

| Level | Adds | Owner |
|---|---|---|
| 1 — Core | Parse and validate a single document | `legaldown-validator` |
| 2 — Rendering | Numbering, resolving directives, output formats, template view | **this package** |
| 3 — Full | Anything that reads other files: includes, attachment contents, amendments, bilingual sets | later, in whichever package owns file access |

At Rendering level (§17.3) the renderer MUST:

- generate section numbering, configurable per render job (§13.1)
- resolve and render every directive except `{{include:}}`, including every bracketed failure
  marker (§6.3, §7.3, §13.3–§13.5, §15.8)
- apply the party and side display rules (§3.6)
- produce at least one of PDF, DOCX, or HTML (§13.6)
- strip comments (§8.6)
- render the template view when a template is rendered without answers, and assemble it first
  when it is rendered with answers (§15.8)

List enumeration (§13.2), style templates (§13.7), and signature blocks (§2.2) are SHOULD. We
plan to support all three, because a legal renderer without them is not usable in practice.

A construct beyond this level, such as `{{include:}}`, is never dropped. It renders as
`[NOT PROCESSED: include schedules/pricing.lgd]` with a Warning (§17.5).

## Principles

**1. There is one parser.** The LegalDown grammar (frontmatter, headings and anchors,
identifiers, directives, definitions) belongs to `legaldown-validator`. This package calls it and
never reimplements it. If the validator and the renderer disagreed about what a document means,
a document could pass validation and still render wrongly. See
[ADR 0002](decisions/0002-one-parser.md).

**2. Legal meaning is resolved once, before any format is written.** Numbering, references,
terms, and field formatting are computed a single time into a format-neutral *resolved document*.
Writers for HTML, DOCX, and PDF only lay that document out. This is why "Section 4.2(b)" is the
same in every format, and why adding a format does not mean reimplementing §13. See
[ADR 0003](decisions/0003-resolved-document.md).

**3. Nothing is dropped silently.** Every failure renders as a visible marker, such as
`[BROKEN REF: id]` or `[UNDEFINED: id]`, and is reported as a diagnostic with a stable rule id. A
`label` never hides a failure (§11.5). By default the renderer still renders a document that has
errors, because a draft with visible markers is useful. Under `--strict` it refuses instead.

**4. Content and presentation stay separate.** Everything visual comes from the style template,
including numbering schemes, locale, and the labels used in the template view. The same source
must render correctly with any compatible template (§13.7). Locale is a render-time setting and
is never read from the document; `language` is only a hint (§10.1).

**5. Output is deterministic.** Identical inputs produce byte-identical output: no timestamps,
no random ids, stable anchor names, stable ordering. This makes golden-file tests possible and
lets rendered output be diffed in Git just like the source.

**6. It is safe with documents you did not write.** Raw HTML in the source is never emitted
(§8.7). All text is escaped for the target format. Link URLs are checked against an allow-list of
schemes. At Rendering level the renderer reads no files other than the document and the chosen
style template.

## Vocabulary

| Term | Meaning |
|---|---|
| **Source** | The LegalDown text of one document |
| **Document model** | The typed model `legaldown-validator` parses the source into (`legaldown.Document`) |
| **Render tree** | This package's format-neutral tree of blocks and inlines, built from the source and document model. It is structurally complete: nested lists, inline formatting, and directives are all nodes |
| **Resolution** | The stage that computes everything the specification leaves to render time: numbers, designations, link targets, display text, formatted values, markers |
| **Resolved document** | The render tree after resolution. It is immutable and holds no unresolved LegalDown semantics. It is the only input to writers |
| **Writer** | A backend that turns the resolved document into a file format (`html`, `text`, later `docx` and `pdf`). A writer contains no LegalDown rules, only layout |
| **Style template** | The configuration file that controls presentation (§13.7). See [style templates](style-templates.md) |
| **Numbering scheme** | How headings are numbered: `decimal`, `legal-outline`, `mixed`, or `none` (§13.1) |
| **Enumeration** | How list items are labelled at each nesting level, for example (a) / (i) / (A) (§13.2) |
| **Designation** | The rendered name of a reference target: "4.2", "4.2(b)(ii)", "Termination (a)" under the `none` scheme, or "Schedule A: Services, Section 2" across attachment scopes (§13.3) |
| **Numbering scope** | A region that is numbered independently: the main body, or each attachment when the style template restarts numbering (§13.8) |
| **Anchor** | A link target in the output. Section, item, and paragraph anchors share one namespace in the source (§5.6). The renderer maps them, together with definition anchors, into the output's single anchor space without collisions, by prefixing definition anchors with `def:`, a character no identifier can contain |
| **Failure marker** | The visible bracketed text the specification requires in place of something that did not resolve, for example `[BROKEN REF: id]` or `[INVALID DATE: value]` |
| **Diagnostic** | A finding with a stable rule id, a severity, and a message. The validator's carry their source line; the renderer's own do not yet. It uses the same type and rule ids as the validator, plus the rules only a renderer can evaluate, such as `ref-not-enumerated` |
| **Template view** | How a template is rendered without answers: conditional units marked, every `{{choose:}}` phrase shown, drafting notes styled distinctly (§15.8) |
