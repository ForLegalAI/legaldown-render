# Styles and preferences

A style template holds everything about how a document looks and nothing about what it says
(§13.7). The same source renders correctly with any style.

## Two kinds of settings

The renderer keeps two kinds of settings apart on purpose
([ADR 0006](decisions/0006-styles-and-options.md)):

| | Style template | Render options |
|---|---|---|
| Answers | How documents look | What this one job does |
| Examples | Numbering scheme, list enumeration, locale, labels, fonts, headings | Output format, strict mode, final check, full page or HTML fragment |
| Where | A YAML file, reused across documents and shared across a team | CLI flags or `RenderOptions` |
| Override per job | `--set key=value`, `--locale`, `overrides={...}` | n/a |

The document never holds presentation settings. Its `language` is only a hint, used when the
style sets no `locale` (§10.1) and to pick the built-in labels (§13.7).

## How a style is resolved

Values are layered. Each layer overrides the one before it, **field by field**:

1. **Built-in defaults.** Every setting has one, defined in `legaldown_render/style/model.py`.
   The `default` style is exactly these defaults.
2. **The `extends` chain.** A style names the style it builds on, which can be a built-in name or
   a path relative to the file. Chains are resolved base first, and cycles are rejected.
3. **The chosen style**: `--style NAME|PATH`, or `RenderOptions(style=...)`.
4. **Per-job overrides**: `--set numbering.scheme=mixed` or `overrides={"numbering.scheme": "mixed"}`,
   plus `--locale`. On the command line, text settings take the value as written
   (`--set placeholders.blank=[__]`). A value wrapped in one pair of matching quotes that does
   not contain that quote is unquoted; nothing else is interpreted, so `"Section" {designation}`
   or `C:\new` stay exactly as written. To keep quotation marks
   around a value, wrap it in the other kind (`--set 'references.format="{designation}"'`
   gives `{designation}`, and `--set "references.format='\"{designation}\"'"` gives
   `"{designation}"`). Other settings are read as YAML (`true`, `2`, `[...]`).

Mappings merge field by field, so `headings: {1: {align: center}}` keeps level 1's size and
weight. Lists, such as `enumeration.levels`, replace the list they override.

The result is **validated in full** before rendering starts. Unknown keys, wrong types, and
values outside an allowed set are all reported together, each with its key path, and suggestions
are offered for typos:

```
$ legaldown-render contract.lgd --set numbring.scheme=none
legaldown-render: Invalid style 'default':
  - numbring: unknown key (did you mean 'numbering'?)
```

To see the effective style, with every setting after all layers are applied, use
`legaldown-render --print-style --style continental --set locale=cs-CZ`. The output is itself a
valid style file.

## Built-in styles

| Name | Look |
|---|---|
| `default` | Decimal numbering (1. / 1.1 / 1.1.1), (a) / (i) / (A) list items, bold defined terms, a neutral serif page |
| `continental` | Numbered articles, section-qualified list items (2.1, 2.2), and numbered paragraphs, for "Article 5(2)" references |
| `outline` | Legal outline: I. / A. / 1. / a. |

## Reference

```yaml
version: 1                   # style format version
name: house
extends: continental         # built-in name or relative path; default: default

locale: cs-CZ                # formatting locale (§10.1); unset: the document's language

numbering:                   # §13.1
  scheme: decimal            # decimal | legal-outline | mixed | none
  levels:                    # optional; overrides the scheme level by level from level 1
    - { counter: upper-roman, label: "Article {n}", ref: "{n}" }

enumeration:                 # §13.2
  enabled: true              # false: plain bullets; refs to items fall back to the section
  levels:                    # by nesting depth, cycling deeper
    - { counter: lower-alpha, label: "({n})", ref: "({n})" }
    - { counter: lower-roman, label: "({n})", ref: "({n})" }
    - { counter: upper-alpha, label: "({n})", ref: "({n})" }
  ordered: renumber          # renumber (1., 2.) | enumerate (apply levels)

paragraphs:                  # §13.2, off by default
  numbered: false
  format: { counter: decimal, label: "{section}.{n}", ref: ".{n}" }

references:
  format: "{designation}"    # how {{ref:}} displays, e.g. "4.2(b)"

definitions:
  style: bold                # the defining occurrence: bold | italic | underline | small-caps | plain
  term_style: plain          # every {{term:}} reference

values:
  date: long                 # short | medium | long | full | a CLDR pattern such as "d. M. yyyy"
  money: symbol              # symbol ($10,000.00) | code (USD 10,000.00) | name (10,000.00 US dollars)
  duration: long             # long (12 months) | short | narrow

placeholders:
  blank: "[_____]"
  show_prompt: false         # show the question's prompt next to the blank (§15.8)

title_block: { parties: true, effective_date: true, version: false }
contents: { enabled: false, depth: 2, attachments: true }     # table of contents after the title block; depth 1-5
attachments: { render: placeholder, separator: page-break }   # placeholder | omit; page-break | rule | none
signatures: { enabled: true }                                 # the document's include_signatures: false wins
template_view: { choice_separator: " / ", empty_choice: "" }

labels:                      # every word the renderer generates; unset: built-in for the document language
  drafting_note: Drafting note
  condition: "Only if: {condition}"
  # identification_number, date_of_birth, address, represented_by, effective_date, version,
  # attachments, contents, attachment_file ("{file}"), signatures, signature_date, signature_place,
  # signature_name, signature_title

typography:
  font_family: 'Georgia, "Times New Roman", serif'
  font_size: 11pt
  line_height: "1.5"
  color: "#1a1a1a"
  link_color: "#1f4e8c"
  justify: false

headings:                    # per level 1–5
  1: { size: 1.15em, weight: "700", italic: false, transform: uppercase, align: left }

page: { size: A4, margin: 25mm }      # print stylesheet now; the PDF writer later
html:
  max_width: 46rem
  extra_css: ""              # appended to the stylesheet; trusted configuration only
```

### Level formats

Headings, list items, and paragraphs are all numbered with the same building block:

| Field | Meaning |
|---|---|
| `counter` | `decimal`, `lower-alpha`, `upper-alpha`, `lower-roman`, `upper-roman` |
| `label` | What the output shows next to the heading or item |
| `ref` | This level's piece of a **designation**, the text `{{ref:}}` renders |

Both templates can use `{n}` (this level's counter), `{path}` (the full designation up to this
level), and `{section}` (the containing section's designation, for items and paragraphs). For
example, decimal headings use `label: "{path}"` and `ref: ".{n}"`. Legal enumeration uses
`label: "({n})"` and `ref: "({n})"`, so the second item of Section 2.3 is shown as "(b)" and
referenced as "2.3(b)". Under the `none` scheme, designations are heading text, and item pieces
are set off by a space ("Termination (a)", §13.3).

### Built-in labels

Labels follow the document's `language`. English (`en`) and Czech (`cs`) are built in, and other
languages fall back to English. A style sets any label explicitly to override it, which is also
how a firm adds a language.

## Open questions

- Should styles be distributable as Python packages, discovered through entry points, so a firm
  can `pip install` its house style?
- The DOCX writer will need a `docx` section that maps onto Word styles in a reference document.
  Its shape is decided with that writer.
- Should `references.format` gain a generated prefix ("Section 4.2")? The word would have to come
  from `labels`, and it could double up with source text that already says "Section".
