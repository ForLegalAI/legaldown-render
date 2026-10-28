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
   a path relative to the file (any path the process can read, `../` and absolute paths
   included; an untrusted style names a built-in only, see "Untrusted mode"). Chains are resolved base first, and cycles are rejected, however the files are
   spelled (`../dir/a.yaml`, a symbolic link).
3. **The chosen style**: `--style NAME|PATH`, or `RenderOptions(style=...)`.
4. **Per-job overrides**: `--set numbering.scheme=mixed` or `overrides={"numbering.scheme": "mixed"}`,
   plus `--locale`. On the command line, text settings take the value as written
   (`--set placeholders.blank=[__]`). A value wrapped in one pair of matching quotes that does
   not contain that quote is unquoted; nothing else is interpreted, so `"Section" {designation}`
   or `C:\new` stay exactly as written. To keep quotation marks
   around a value, wrap it in the other kind (`--set 'references.format="{designation}"'`
   gives `{designation}`, and `--set "references.format='\"{designation}\"'"` gives
   `"{designation}"`). Other settings are read as YAML (`true`, `2`, `[...]`); a value that is not
   valid YAML is an error naming the setting and the YAML problem. `version` and `extends` belong
   to the template file and cannot be overridden: choose the template with `--style`.

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
valid style file, and a self-contained one: every setting is written out and there is no `extends`,
so it loads from any directory.

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
  levels:                    # optional; overrides the scheme by number part: the first format for "2", the second for "2.1"
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
  date: long                 # short | medium | long | full | a CLDR pattern such as "d. M. yyyy" (checked when the style loads; an untrusted style names one of the four)
  money: symbol              # symbol ($10,000.00) | code (USD 10,000.00) | name (10,000.00 US dollars)
  duration: long             # long (12 months) | short | narrow

placeholders:
  blank: "[_____]"           # empty, or only whitespace/invisible characters: renders [TBD: id] (§13.5 rule 6)
  show_prompt: false         # show the question's prompt next to the blank (§15.8)

title_block: { parties: true, effective_date: true, version: false }
contents: { enabled: false, depth: 2, attachments: true }     # table of contents after the title block; depth 1-5
                                                              # counts number levels ("2.1" is 2); attachments listed last
attachments: { render: placeholder, separator: page-break }   # placeholder | omit; page-break | rule | none (plain text: `---` around attachments unless none)
signatures: { enabled: true }                                 # the document's include_signatures: false wins
template_view: { choice_separator: " / ", empty_choice: "" }

labels:                      # every word the renderer generates; unset: built-in for the document language
  drafting_note: Drafting note
  condition: "Only if: {condition}"
  # identification_number, date_of_birth, address, represented_by, effective_date, version,
  # attachments, contents, attachment_file ("{file}"), signatures, signature_date, signature_place,
  # signature_name, signature_title
  colon: ": "                # between a label and its value; French: no-break space, then ": "

typography:
  font_family: 'Georgia, "Times New Roman", serif'
  font_size: 11pt
  line_height: "1.5"
  color: "#1a1a1a"
  link_color: "#1f4e8c"
  justify: false

headings:                    # per level 1–5
  1: { size: 1.15em, weight: "700", italic: false, transform: uppercase, align: left }

page:
  size: A4                   # HTML print stylesheet and DOCX page setup; the PDF writer later
  margin: 25mm
  header: { left: "", center: "", right: "" }              # running header (DOCX; not yet HTML or PDF), see "Headers and footers"
  footer: { left: "", center: "{page} / {pages}", right: "" }

html:
  max_width: 46rem
  extra_css: ""              # raw CSS, appended to the stylesheet as written (see "Trusted input")

docx:                        # the DOCX writer only (see "DOCX")
  update_fields_on_open: true
```

### Level formats

Headings, list items, and paragraphs are all numbered with the same building block:

| Field | Meaning |
|---|---|
| `counter` | `decimal`, `lower-alpha`, `upper-alpha`, `lower-roman`, `upper-roman` |
| `label` | What the output shows next to the heading or item |
| `ref` | This level's piece of a **designation**, the text `{{ref:}}` renders |

Both templates can use `{n}` (this level's counter), `{path}` (the full designation up to this
level), and `{section}` (the containing section's designation, for items and paragraphs). In the
preamble, which has no section, `{section}` is empty and it is removed together with the `.` after
it, so `label: "{section}.{n}"` shows "1", not ".1". For example, decimal headings use
`label: "{path}"` and `ref: ".{n}"`. Legal enumeration uses `label: "({n})"` and `ref: "({n})"`, so the
second item of Section 2.3 is shown as "(b)" and
referenced as "2.3(b)". Under the `none` scheme, designations are heading text as rendered (references and terms in it resolved), and item pieces
are set off by a space ("Termination (a)", §13.3).

### Text templates and their fields

A text setting that substitutes `{field}` names is checked when the style is loaded. A field is a
name in braces that is an identifier (`{n}`, `{designation}`); any other braces (`{}`, `{1}`,
`{a b}`) are literal text. An identifier the setting does not accept is an error naming the
setting (a typo would otherwise be shown literally), and so is a missing required field, but only
where the setting is in use: the `ref` of `numbering.levels` is required to hold `{n}` unless
`numbering.scheme` is `none` (a reference then shows the heading text); the `ref` of
`enumeration.levels` only when lists are enumerated (`enumeration.enabled: true`, or `ordered:
enumerate`); `paragraphs.format.ref` only when `paragraphs.numbered` is true. A typo in a field is
flagged either way. Plain labels substitute nothing, so they are not checked: `{word}` in one is
just text.

| Setting | Accepted fields | Required |
|---|---|---|
| `numbering.levels[].label` | `{n}`, `{path}` | none |
| `numbering.levels[].ref` | `{n}` | `{n}`, unless `numbering.scheme: none` |
| `enumeration.levels[].label`, `paragraphs.format.label` | `{n}`, `{path}`, `{section}` | none |
| `enumeration.levels[].ref`, `paragraphs.format.ref` | `{n}`, `{section}` | `{n}`, when lists are enumerated / paragraphs are numbered |
| `references.format` | `{designation}` | `{designation}`, or every reference reads the same |
| `labels.condition` | `{condition}` | `{condition}`, as §15.8 requires the condition to be shown; an empty value is allowed and shows the bare condition |
| `labels.attachment_file` | `{file}` | none |
| `page.header.*`, `page.footer.*` (left, center, right) | `{page}`, `{pages}`, `{title}`, `{version}`, `{date}` | none: an empty text shows nothing |
| every other label | not checked: braces are text | none |

```
$ legaldown-render contract.lgd --set references.format=nope
legaldown-render: Invalid style 'default':
  - references.format: must contain {designation} — every reference would read the same (§13.3)
```

A `ref` is one piece of a designation, so it cannot show the whole `{path}`. A label left unset
takes the built-in one for the document language, which always passes.

### Headers and footers

`page.header` and `page.footer` are the running text at the top and bottom of every page, each as a
`left`, a `center` and a `right` text set at the left margin, in the middle and at the right margin. The
default footer is `{page} / {pages}` in the centre ("3 / 12"), and the default header is empty. A text
may use these fields, and any other `{name}` in braces is refused when the style loads:

| Field | Shows |
|---|---|
| `{page}` | the page number |
| `{pages}` | the number of pages |
| `{title}` | the document's title |
| `{version}` | the frontmatter's `version`; empty when the document has none |
| `{date}` | the frontmatter's `effective_date`, formatted as `values.date` says for the formatting locale; empty when the document has none |

`{version}` and `{date}` are the same text the title block shows for them (directives in them resolved
the same way, a valid date formatted as `values.date` says), whether or not `title_block` shows them
there. A text that is empty everywhere (the header by default) writes no header at all.

**Where it applies.** The **DOCX** writer writes them: `{page}` and `{pages}` are `PAGE` and `NUMPAGES`
fields that Word and LibreOffice count while laying out the pages (a viewer that lays out no pages shows
the cached "1", so the default footer reads "1 / 1" there), and the other three fields are text.
A single text is aligned; two or three are separated by tab stops at the middle and the right margin.
The **HTML** and plain-text writers do not read them yet; the PDF writer will (v0.6).

```yaml
page:
  header: { left: "{title}", right: "Version {version}" }
  footer: { center: "Page {page} of {pages}" }
```

### DOCX

The DOCX writer reads the same presentation settings as the HTML writer and converts them to what
Word has (docs/decisions/0008-docx-word-native.md). A setting Word cannot express is a **style error
when the format is `docx`**, reported before anything is rendered and naming every such setting; HTML
keeps accepting the same style.

| Setting | In the DOCX |
|---|---|
| `page.size` | the paper size of the page: `A3` to `A5`, `B4`, `B5`, `letter`, `legal`, `ledger`, with `landscape` or `portrait`; or one or two lengths; `auto` and an orientation alone are A4 |
| `page.margin` | one to four lengths (top, right, bottom, left, as in CSS); the header and footer sit half a margin from the edge, at most 1.27 cm |
| `typography.font_family` | the **first** family: `Georgia` for `'Georgia, "Times New Roman", serif'`; `serif`, `sans-serif` and `monospace` alone are Times New Roman, Arial and Courier New. Word falls back to its own substitute when the font is not installed |
| `typography.font_size`, `headings.N.size` | half-points (Word's unit for a font size, so a size is rounded to the nearest half-point: `11.3pt` is 11.5 pt); `headings.N.size` between 1 pt and 1638 pt, and `typography.font_size` between 1 pt and 1023 pt, because the title is 1.6 times the body |
| `typography.line_height` | a number is that many lines (`1.5`), `normal` is one line, a length is the least height of a line |
| `typography.color`, `typography.link_color` | `#rgb`, `#rrggbb` (an alpha part is dropped), `rgb()`, `hsl()`, or one of `black silver gray white maroon red purple fuchsia green lime olive yellow navy blue teal aqua orange`; any other colour name is an error, so write it as `#rrggbb` |
| `headings.N.weight` | bold from 600 up (`bold`, `bolder`), otherwise regular |

Lengths: `mm`, `cm`, `in`, `pt`, `pc` and `px` (96 to the inch) are absolute. **`em` and `rem`** are
relative to `typography.font_size`, which is itself relative to 12 pt when it is written in them, as in a
browser. **`%`, `ch` and `ex`** depend on a layout or a font that Word does not give the renderer, so
each is an error for DOCX: `page.margin: 5%` raises "page.margin: uses '%', which a Word document cannot
express". Units are not case-sensitive (`25MM`). Word's limits are enforced too, as style errors that never
quote the value: a page edge of 7.2 pt to 1584 pt (22 inches), a margin of 0 to 1584 pt, margins that leave
at least 36 pt (0.5 inch) of text in each direction, a line height of at most 1584 pt (or 100 lines), and a
number of more than 32 digits is no length at all. `html.max_width` and `html.extra_css` are never read.

The writer generates Word styles from the template (`Normal`, `heading 1` to `heading 5`, `Title`,
`Subtitle`, `Quote`, `toc 1` to `toc 5`, `Hyperlink`, `Table Grid`, and styles of its own for drafting
notes, code, defined terms, blanks, choices, failure markers and condition labels), so a change made to a
style in Word applies to every paragraph that uses it.

| Setting | Meaning |
|---|---|
| `docx.update_fields_on_open` | `true` (the default) asks Word to update every field when it opens the file: the cross-references, the table of contents and its page numbers. Word asks the reader once ("This document contains fields that may refer to other files. Update the fields?"). The fields hold the values the renderer computed either way, so a viewer that never updates fields (Word Online, Quick Look, Outlook's preview) shows the document right, but the table of contents without page numbers. LibreOffice and Word Online ignore the setting. `false` writes no such request |

The body of the DOCX is not complete yet (it is the title, headings and plain text, with no numbers; see
the [roadmap](roadmap.md)); the settings above are what is already in place.

### Trusted input

A style template, a `--set` value, and an `extends` path are the operator's, not the document
author's. A value placed into the stylesheet (`typography`, `headings`, `page`, `html.max_width`)
must not contain `/*`, `*/`, `</`, a backslash, a control character or a line break: the style is
refused, naming the setting (nothing is stripped). Other CSS is accepted as written, `calc()` and
`var()` included, and is not checked as CSS, so a value such as `12pt (` breaks the rest of the
stylesheet. `html.extra_css` is raw CSS and may not contain `</style` (in any case). `values.date`, if
it is not one of the four names, must be a CLDR date pattern that Babel can format; the loader tries it
on fixed dates, so a bad one (`GGGGGGG`) is a style error, not an exception at render time. NUL and
lone surrogates are refused in every value written into the stylesheet, `html.extra_css` included. `extends` and
`--style` read any file the process can read. Do not render with styles from users you do not
trust in this mode ([Security](architecture.md#security)).

### Untrusted mode

For a style that comes from somebody else, load it with `load_style(style, trusted=False)`, or render
with `RenderOptions(untrusted=True, document_root=...)` (`legaldown-render --untrusted --root DIR`):

- The style is a built-in name, a `Style`, a mapping, or YAML text (a string with a line break), never
  a path; `extends` may name only a built-in style (`default`, `continental`, `outline`).
- Each value that reaches the stylesheet must be a plain value of its kind:

  | Setting | Accepted |
  |---|---|
  | `typography.font_size`, `headings.N.size` | a length: `11pt`, `1.2em`, `0` (units `em rem ex ch pt pc px mm cm in %`) |
  | `typography.line_height` | `normal`, a number (`1.5`), or a length |
  | `typography.color`, `typography.link_color` | `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`, a colour name, `rgb()`/`rgba()`/`hsl()`/`hsla()` of plain numbers |
  | `headings.N.weight` | `normal`, `bold`, `bolder`, `lighter`, or 1 to 1000 |
  | `typography.font_family` | up to ten font names separated by commas; names of more than one word quoted |
  | `page.size` | `auto`, `portrait`, `landscape`, a paper size (`A3` to `A5`, `B4`, `B5`, `letter`, `legal`, `ledger`) with an optional orientation, or one or two lengths |
  | `page.margin` | one to four lengths |
  | `html.max_width` | `none` or a length |

  Functions (`calc()`, `var()`, `min()`), `!important` and anything else are refused. The same grammar
  holds for a `Style` object and for overrides.
- `html.extra_css` must be empty.
- `values.date` is `short`, `medium`, `long` or `full`, not a pattern.
- Every free-text setting (`name`, `labels.*`, `references.format`, `placeholders.blank`,
  `template_view.*`, `page.header.*`, `page.footer.*`, and the `label` and `ref` of `numbering`,
  `enumeration` and `paragraphs` levels) is
  at most 200 characters, since it is repeated wherever the output shows it. NUL and lone surrogates
  (which no output encoding can write) are refused in any text.
- YAML text is at most 64 KiB, may not use aliases, and a number in it is at most 32 characters.
  (A style, trusted or not, nests 32 levels at most.)
- An error names the setting and the problem and never quotes the style, a key or a path, so it can
  be shown to whoever sent the style. It lists 20 problems at most, then "and N more". Anything else
  the loader might raise on such a style is reported as "the style could not be read".
- `--untrusted --print-style` checks a style the same way (no `--root` is needed, nothing is rendered).
- The built-in styles and the examples in this document all pass.

### Built-in labels

Labels follow the document's `language`, not `locale` (which formats dates, money, and durations).
English (`en`), Czech (`cs`), German (`de`), French
(`fr`), Polish (`pl`), and Slovak (`sk`) are built in, and other languages fall back to English. A style sets any label explicitly to override it, which is also
how a firm adds a language.

## Open questions

- Should styles be distributable as Python packages, discovered through entry points, so a firm
  can `pip install` its house style?
- Should a DOCX start from a reference document (a `.dotx` or `.docx` with the firm's styles), as Pandoc
  does? The `docx` section is deliberately small for now: the writer generates its styles from the
  template, so a house style stays one YAML file.
- Should `references.format` gain a generated prefix ("Section 4.2")? The word would have to come
  from `labels`, and it could double up with source text that already says "Section".
