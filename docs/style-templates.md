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
  blank: "[_____]"           # empty, or only whitespace/invisible characters: renders [TBD: id] (§13.5 rule 6);
                             # {id}, {name} ("Purchase price"), {prompt} label each blank: "[{name}]" (see "Blanks")
  show_prompt: false         # show the question's prompt next to the blank (§15.8)

title_block: { parties: true, effective_date: true, version: false }
contents: { enabled: false, depth: 2, attachments: true }     # table of contents after the title block; depth 1-5
                                                              # counts number levels ("2.1" is 2); attachments listed last
attachments: { render: placeholder, separator: page-break }   # placeholder | omit; page-break | rule | none (plain text: `---` around attachments unless none)
signatures: { enabled: true, page_break_before: false }      # the document's include_signatures: false wins;
                                                              # page_break_before: a new page (HTML print, DOCX)
template_view: { choice_separator: " / ", empty_choice: "" }

labels:                      # every word the renderer generates; unset: built-in for the document language
  drafting_note: Drafting note
  condition: "Only if: {condition}"
  # condition_true, condition_false, condition_is, condition_is_not: unset (see "Condition labels")
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

elements:                    # typography of single elements, HTML and DOCX; unset (null): as drawn by default
  quote: { font_family: null, font_size: null, color: null, line_height: null,
           space_before: null, space_after: null, indent: null }
  list: { font_family: null, font_size: null, color: null, line_height: null, item_spacing: null }
  table: { font_family: null, font_size: null, color: null }          # table cells
  parties: { font_family: null, font_size: null, color: null }        # the parties in the title block
  signatures: { font_family: null, font_size: null, color: null }
  defined_term: { font_family: null, font_size: null, color: null }   # "Fee" {{def: fee}}  (see "Elements")

html:
  max_width: 46rem
  extra_css: ""              # raw CSS, appended to the stylesheet as written (see "Trusted input" and "Embedded HTML")

docx:                        # the DOCX writer only (see "DOCX")
  update_fields_on_open: true
  reference_text_switch: documented   # off | documented | guess: what a cross-reference may expect of Word's \t switch
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
| `labels.condition_true`, `labels.condition_false` | `{question}`, `{condition}` | `{question}` or `{condition}`; an empty value shows the bare condition |
| `labels.condition_is`, `labels.condition_is_not` | `{question}`, `{answer}`, `{condition}` | `{question}` or `{condition}`, and `{answer}` or `{condition}`; an empty value shows the bare condition |
| `placeholders.blank` | `{id}`, `{name}`, `{prompt}` | none; any other braces are text (`{TBD}` renders as written) |
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

### Blanks

`placeholders.blank` is the text of every blank (§13.5 rule 5). Three fields label each blank instead of
drawing the same line everywhere:

| Field | Is | `{{placeholder: purchase-price}}` |
|---|---|---|
| `{id}` | the placeholder id | `purchase-price` |
| `{name}` | the id as words: hyphens become spaces, the first letter upper case | `Purchase price` |
| `{prompt}` | the declared question's `prompt` (§15.2), stripped; without one, `{name}` | `Price, excluding VAT` |

`blank: "[{name}]"` renders `[Purchase price]`. Any other braces are text. A blank's `note=` is never shown:
the specification keeps notes out of rendered output (§10.1, §13.5 rule 4). §13.5 rule 6 is judged on the filled
text: a blank whose text fills to nothing visible (`"{prompt}"` with a prompt of a zero-width space) renders
`[TBD: id]`. `{prompt}` is written on one line. After a partial assembly (an answers set that leaves blanks
open), the assembled document no longer declares the questions, so `{prompt}` shows `{name}` there. The text appears wherever the
blank does, also in a document title, a `{title}` in a page header, and a reference to a heading that holds the
blank under `numbering.scheme: none`. In HTML a blank with fields may wrap, as words do (`ld-blank-label`); the
plain one stays on one line. `placeholders.show_prompt` still adds `(prompt)` after the blank, so with
`blank: "[{prompt}]"` it shows the prompt twice.

### Condition labels

In the template view (§15.8) a conditional unit shows a label with its condition. `labels.condition`
(`"Only if: {condition}"`) shows the condition as written: `vat`, `!vat`, `forum:courts`, `!forum:courts`. A
style may give each of the four shapes of condition a label of its own:

| Label | Condition | Example | Shows |
|---|---|---|---|
| `labels.condition_true` | `q` | `"Only if: {question}"` | Only if: vat |
| `labels.condition_false` | `!q` | `"Only if not: {question}"` | Only if not: vat |
| `labels.condition_is` | `q:v` | `"Only if: {question} = {answer}"` | Only if: forum = State courts |
| `labels.condition_is_not` | `!q:v` | `"Only if: {question} ≠ {answer}"` | Only if: forum ≠ State courts |

`{question}` is the question id, `{answer}` the label the question gives the value in `choices` (the value id when
the label is blank), `{condition}` the condition as written. A shape whose label is unset takes
`labels.condition`, so a style that sets only `labels.condition` renders as before, and no built-in language sets
these four: they change nothing unless a style asks. A condition the validator rejects (`condition-invalid`) always
takes `labels.condition`. Each label must name its question (and the choice ones the answer), or show `{condition}`;
that `condition_false` and `condition_is_not` actually say "not" is the style's to get right, as no check can read
the words. With `extends`, a label set in the parent applies to the child. An untrusted style may use each field once in each condition label (`labels.condition` too) and in
`placeholders.blank`, so that it cannot multiply a long prompt or choice label. `{answer}` is written on one line,
and it is the value id when the choice's label is blank or another choice of the question has the same label, so that
two conditions never read alike.

### Elements

`elements.*` set the type of single elements, in HTML and DOCX alike (the plain-text writer has none). Every
field is unset by default, which keeps what the writers draw; a style changes only what it sets.

| Element | Fields | HTML | DOCX (Word style) |
|---|---|---|---|
| `quote` | `font_family`, `font_size`, `color`, `line_height`, `space_before`, `space_after`, `indent` | `.ld-quote` (`margin-top`, `margin-bottom`, `padding-left`) | `Quote`; `indent` is the left indent of everything the quote holds |
| `list` | `font_family`, `font_size`, `color`, `line_height`, `item_spacing` (the space after each item) | `.ld-list`, `.ld-list > li` | `List Paragraph`: the paragraphs of a list item, unless it is in a quote or a drafting note, where it keeps that style |
| `table` | `font_family`, `font_size`, `color` | `.ld-table` | `Table Text`, the paragraphs in the cells |
| `parties` | `font_family`, `font_size`, `color` | `.ld-parties` | `Party Side`, `Party Name`, `Party Details` (the header rows keep `Title Block Text`) |
| `signatures` | `font_family`, `font_size`, `color` | `.ld-signatures` | `Signature Side`, `Signature Text`, `Signature Line` |
| `defined_term` | `font_family`, `font_size`, `color` | `.ld-defined` (the defining occurrence, and its text where a heading's copy shows it) | `Defined Term` (`definitions.style` still sets its emphasis) |

```yaml
elements:
  quote: { font_size: 10pt, indent: 2em, space_after: 1em }
  list: { item_spacing: 3pt }
  table: { font_family: Arial, font_size: 9pt }
signatures: { page_break_before: true }
```

The values are CSS, held to the same grammars as `typography` in an untrusted style. A list's and a table's
indents are the numbering's and the page's, so there is no indent for them, and no spacing for tables, parties
and signatures, which are tables in Word. In DOCX `em` in an element is the element's own font size when it
sets one (`indent: 2em` of a 10pt quote is 20pt), `rem` the body's; `%`, `ch` and `ex` are an error for DOCX, as
everywhere, a space or an indent is 0 or more, and a quote's indent must leave at least 113.4pt (4 cm) of the text
width, the least room the DOCX body leaves any paragraph (a quote nested in a quote is held at that limit too). An
empty value is refused: leave the field out, or write `null`, to keep the default (or what the style extends).

`signatures.page_break_before` starts the signature blocks on a new page: in HTML in print (`break-before:
page`), in DOCX with an empty paragraph one point high that breaks the page before the signature table.

Where the formats differ, as their models do:

- **Spacing.** In HTML `item_spacing` is the space after each list item and a quote's `space_before` and
  `space_after` are around the whole quote. In DOCX they are paragraph properties of the style, so they apply to
  each paragraph that has it: each paragraph of an item, each paragraph of a quote (as the default spacing of a
  quote always has).
- **Nesting.** In HTML an element's settings reach what it holds: a table or a quote in a list item takes the
  list's font and colour unless it sets its own, and a relative `font_size` (`em`, `%`) is relative to the element
  it is in (a table of `0.8em` in a list item of `0.8em` is 0.64em; a quote or a list in one of its kind keeps its
  size rather than scaling it again). In DOCX each paragraph has the one style of its kind (a table in a list item
  is `Table Text`, not `List Paragraph`), a `font_size` in `em` is the body's, and a list in a quote keeps the
  quote's style. Sizes in `pt`, set on each element that holds text, read alike in both, except for a list in a
  quote, which is a list in HTML and a quote in DOCX.

The DOCX always names `List Paragraph`, `Table Text` and `Party Details`, set or not, so a lawyer can restyle list
items, table cells and the parties' details in Word.

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
The **HTML** and plain-text writers do not read them yet; the PDF writer will (v0.9).

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
`Subtitle`, `Quote`, `List Paragraph`, `toc 1` to `toc 5`, `Hyperlink`, `Table Grid`, and styles of its own for
drafting notes, code, table text (`Table Text`), the title block, the parties (`Party Side`, `Party Name`,
`Party Details`) and the signatures, defined terms, blanks, choices, failure markers and condition labels; see
"Elements"), so a change made to a
style in Word applies to every paragraph that uses it.

| Setting | Meaning |
|---|---|
| `docx.update_fields_on_open` | `true` (the default) asks Word to update every field when it opens the file: the cross-references, the table of contents and its page numbers. Word asks the reader once ("This document contains fields that may refer to other files. Update the fields?"). The fields hold the values the renderer computed either way, so a viewer that never updates fields (Word Online, Quick Look, Outlook's preview) shows the document right, but the table of contents without page numbers. LibreOffice and Word Online ignore the setting. `false` writes no such request |
| `docx.reference_text_switch` | What a cross-reference may expect of Word's `\t` switch (`REF bookmark \n \t`), which leaves the words of a number out. A reference is built from `REF` fields so that it stays right when a clause is inserted; where the reference shows less than the number Word draws (`Section {designation}` for a number `Article I`, `čl. {designation}` for `Čl. 1`), the switch leaves the words out and the `references.format` text goes back in as literal text. LibreOffice ignores the switch (and shows the whole number there, which the render reports once as `render-docx-reference-word-only`). Microsoft documents it with one example (`Section 1.01` gives `1.01`: the words around the number go, a delimiter between its counters stays); what Word does with a delimiter in the text around the counters is not documented. **`documented`** (the default) uses the switch only where that example settles the result: words before the first counter or after the last go, a delimiter between counters stays (words between counters are not documented: only `guess` drops them), and a label with a delimiter around its number (`(a)`, `a)`, `1:`, `No. 1`) is not asked for it. **`off`** never uses it: a reference that needs it is fixed text, reported (`render-docx-reference`). **`guess`** also expects Word to leave out a period after the letters that lead a number (`No. 1`, `Čl. 1`) and words between counters (`Part 1 of 2` gives `12`): the checklist in `.github/PUBLISHING.md` says how to check what your Word does, and a reference that is wrong in Word is fixed by setting `documented` |

Settings that were already part of the template gain a DOCX meaning:

| Setting | In the DOCX |
|---|---|
| `contents.enabled`, `contents.depth` | A live `TOC \o "1-<depth>" \h \z \u` field after the title block, with the contents' entries as its cached result (`toc 1` to `toc 5` paragraphs, a dotted right tab at the text width, a `PAGEREF` page number Word fills in when it updates the field). The contents label is a `Contents Heading` paragraph: no outline level, no number, so never an entry |
| `contents.attachments` | The attachments heading and each attachment title are paragraphs of the styles `Attachments Heading` and `Attachment Title`, which carry an outline level, so Word's TOC lists them (`\u`); without it they are body text for the contents. At `contents.depth: 1` the titles are listed with the headings (the first level), not under the attachments heading |
| `attachments.separator` | `page-break`: a page break before the attachments part and between attachments (the paragraph property "page break before", so no empty paragraph or blank page is left); `rule`: a line above each attachment; `none`: nothing |
| `signatures.enabled` | Borderless tables with a signing line, kept together on one page as far as one page allows. They follow the last attachment, as in the HTML. A name, title, date or place row whose label is blanked (`labels.signature_*`) and whose value is empty is left out, as the plain-text writer leaves it out |
| `title_block.parties`, `title_block.effective_date`, `title_block.version` | The header rows and the parties, as borderless tables |

The numbering and wording settings are the same as in the other formats; what they become in the file:

| Setting | In the DOCX |
|---|---|
| `numbering.*` | The multilevel list the heading styles are linked to: `counter` is the level's number format, `label` its level text (`{n}` the level's own counter, `{path}` the counters of the levels above it joined as the label says). A label with a literal `%` before a digit or a counter cannot be written as a level text and is a style error for DOCX. With `scheme: none` the headings are not numbered |
| `enumeration.*`, `paragraphs.*` | The lists of items and the numbered paragraphs, one numbering definition for each kind. A label that shows `{section}` can be live only on the heading list's next level, and is otherwise written without it and reported (`render-docx-section-label`) |
| `references.format`, `numbering.levels[].ref`, `enumeration.levels[].ref` | The literal text around the `REF` fields of a cross-reference and between them; the cached result of the field is what Word draws. See `docx.reference_text_switch` |
| `definitions.style`, `definitions.term_style` | The character styles of a defining occurrence and of a `{{term:}}` reference |
| `typography.justify` | The justification of `Normal` |
| `locale`, `values.*`, `placeholders.*`, `template_view.*`, `labels.*` | As in the other formats: dates, amounts, blanks, choices and labels are written as text (they are not numbers Word can compute) |
| `html.max_width`, `html.extra_css` | Not read |

The styles for this matter (`Title Block Text`, `Party Side`, `Party Name`, `Attachments Heading`, `Attachment Title`,
`Attachment File`, `Signature Side`, `Signature Text`, `Signature Line`) are Word styles like the others: a lawyer who
changes one in Word changes every paragraph that uses it.

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

### Embedded HTML

For HTML embedded in a host page (`--fragment`, `standalone=False`), the CSS is
`legaldown_render.writers.stylesheet(style, standalone=False)`: every rule is under `.ld-document`, and
`typography`, `headings`, `html.max_width` and the colours apply there as on a complete page. `page.size` and
`page.margin` (the `@page` rule) are not written, and neither is the page's margin or the document's padding: the
host's page and container own them. `html.extra_css` is appended as written, not scoped: a bare selector in it
reaches the host page, an override of a base rule applies only when written under `.ld-document`
(`.ld-document .ld-title { … }`, not `.ld-title { … }`), and `:root` variables in it do not reach the document.
Give the sheet a `<style>` element of its own, so that a value that does not parse (`12pt (`) cannot take the host's
rules with it. See the README's "Python" section for `scope=`.

### Untrusted mode

For a style that comes from somebody else, load it with `load_style(style, trusted=False)`, or render
with `RenderOptions(untrusted=True, document_root=...)` (`legaldown-render --untrusted --root DIR`):

- The style is a built-in name, a `Style`, a mapping, or YAML text (a string with a line break), never
  a path; `extends` may name only a built-in style (`default`, `continental`, `outline`).
- `placeholders.blank` and the condition labels (`labels.condition`, `condition_true`, `condition_false`,
  `condition_is`, `condition_is_not`) use each of their fields at most once.
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
