# Style templates (draft)

> Status: **draft for discussion.** The keys below are a proposal. Nothing is implemented, and the
> format will be versioned before its first release.

A style template holds everything about how a document looks and nothing about what it says
(§13.7). The same source must render correctly with any compatible template.

## Principles

- **YAML, validated on load.** Templates are YAML files with a `version` field. Unknown keys are
  errors, so a typo cannot be ignored silently. Loading reports every problem, with its key path.
- **Layered.** Values merge in this order, each overriding the one before:
  1. the built-in `default` template
  2. the chosen template, or the template named in its `extends`
  3. per-job overrides (CLI flags or `RenderOptions`)

  A template only needs to state what it changes.
- **Semantic first, per format second.** The shared part (numbering, enumeration, locale,
  labels, attachments) drives resolution, so it applies to every output format. Format sections
  (`html`, `docx`, `pdf`) only affect layout in that writer.
- **Language-neutral labels.** The renderer never hardcodes text such as "Section", "Schedule",
  or "Drafting note". Any word it has to generate comes from `labels`, so a template in Czech
  produces Czech output.

## Sketch

```yaml
version: 1
name: continental
extends: default

locale: cs-CZ                # formatting locale (§10.1); not read from the document

numbering:
  scheme: decimal            # decimal | legal-outline | mixed | none (§13.1)
  suffix: "."                # "1." vs "1"
  attachments: restart       # restart | continue (§13.8)

enumeration:                 # §13.2
  enabled: true
  levels: ["(a)", "(i)", "(A)"]
  ordered_lists: renumber    # renumber | enumerate (apply the levels above)
  first_level: letters       # letters | section-decimal (5.1, 5.2, …)

paragraphs:
  numbered: false            # top-level paragraph numbering (§13.2); off by default

references:
  # How a designation is written. {number} is the target's designation, {title} its heading text.
  format: "{number}"
  cross_scope: "{scope_title}, {number}"     # §13.3, across attachment scopes

values:
  money: { display: symbol } # symbol | code | name
  date: { style: long }      # short | medium | long | full, or a CLDR pattern

placeholders:
  blank: "[_____]"           # §13.5

attachments:
  separator: page-break      # page-break | rule | none
  non_legaldown: placeholder # placeholder | omit (§13.8)

signatures:
  enabled: true              # document metadata `include_signatures: false` still wins
  layout: side-by-side       # side-by-side | stacked

labels:                      # every word the renderer generates
  drafting_note: "Poznámka pro zpracovatele"
  conditional: "Pouze pokud: {condition}"

html:
  font_family: "Georgia, serif"
  font_size: 11pt
  max_width: 48rem
  headings:
    1: { size: 1.25em, weight: 700, transform: uppercase }
    2: { size: 1.1em, weight: 700 }
  extra_css: ""              # appended last; trusted configuration only

docx:                        # v0.3
  reference_docx: null       # optional .docx whose styles are reused

pdf:                         # v0.4
  page: { size: A4, margins: 25mm }
  header: "{title}"
  footer: "{page} / {pages}"
```

## Built-in templates (planned)

| Name | Look |
|---|---|
| `default` | Decimal numbering, (a)/(i)/(A) lists, `en-US` locale, neutral serif typography |
| `continental` | Decimal numbering with section-decimal list items and numbered paragraphs, the "čl. 5 odst. 2" convention |
| `outline` | Legal outline: I. / A. / 1. / a. |

## Open questions

- Should `references.format` allow a generated prefix ("Section 4.2") at all? That brings back a
  language-specific word. It would have to come from `labels`, and it could clash with source
  text that already says "Section {{ref: x}}".
- How far should the `docx` section map onto Word styles? Should it name styles in a reference
  document, or define them inline?
- Should templates be distributable as Python packages, discovered through entry points, so firms
  can `pip install` their house style?
