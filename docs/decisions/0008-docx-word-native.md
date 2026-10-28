# 0008. DOCX: Word-native numbering and fields, written as OOXML directly

- Status: Accepted. Decides the DOCX questions [0004](0004-html-first.md) left open (the numbering
  question, and the python-docx extra); the PDF engine stays open there
- Date: 2026-10-28

## Context

Lawyers who receive a DOCX edit it. A clause inserted in Word must renumber the clauses after it,
and a cross-reference to "Section 2.3(b)" must follow the clause it points at. Numbers written as
text are right on the day of export and wrong after the first edit. The same holds for the table of
contents and its page numbers, and for the page numbers in a footer.

[0003](0003-resolved-document.md) keeps one set of numbers for every format: the resolver
decides "2.3(b)", and a writer only lays it out. The question for DOCX is how a writer can lay out a
number that Word will keep recomputing without giving up that guarantee.

## Decision

1. **Word-native numbering and fields. Static numbers are not an option.** Heading numbers, list
   enumeration and paragraph numbers are numbering definitions; cross-references, the table of
   contents and page numbers are fields. The renderer still computes every value. It writes it only
   as the cached result of a field, or as the input from which a numbering definition is built.
   Nothing Word can compute is written as literal text. The document reads correctly before any
   field is updated, and stays correct when a lawyer inserts a clause and updates fields.
2. **OOXML is written directly**, with the standard library only (`zipfile`, `xml.etree`), not with
   python-docx.
   - python-docx has no API for numbering definitions, fields, bookmarks, internal hyperlinks, the
     table of contents or the order of the settings, so all of that would be raw XML in any case.
   - Its starting template brings Letter paper, about 160 styles and stale metadata that would
     have to be removed.
   - A writer of our own gives schema order, byte-for-byte determinism and no new dependency, so
     DOCX is part of the base package and 0004's `[docx]` extra is not needed.
   - Validity becomes ours to guarantee, which CI checks against the ISO/IEC 29500 schemas.
3. **The render tree carries format-neutral numbering structure** (0003): for each numbered unit a
   stable id, its counter value, its level format and its parent unit. The resolver stays the only
   place where numbering rules live.
4. **A DOCX planner maps the tree to Word.** It decides lists, levels, numbering instances,
   bookmarks, how each cross-reference is composed of fields, and the cached values, and it reports
   the diagnostics where Word cannot do what the renderer does. The writer takes the tree and
   the plan, contains no rules and emits nothing.
5. **Cached results are what Word will compute.** They come from a model of Word's numbering in the
   planner, not from the renderer's designations. The cached table of contents comes from the same
   model, not from the tree's contents list. Where Word's value would differ from the renderer's
   (letter counters past 26, roman numerals from 4000), the planner warns. A field is never replaced
   by its text.
6. **Configuration problems raise before rendering** ([0006](0006-styles-and-options.md)). A style
   setting Word cannot express is a `StyleError` when the format is `docx`, not a diagnostic: a
   length in `%`, `ch` or `ex`, a colour the writer cannot read, a numbering label with a literal
   `%` before a digit (which a level text cannot escape). HTML keeps accepting all of them.
   Lengths in `em` and `rem` are converted through `typography.font_size`.
7. **Word-equivalence is guaranteed for documents without Errors.** For example `heading-skip`
   is a Core Error, so a document that renders has headings whose level is the count the resolver
   already clamped, and the writer uses `Heading1` to `Heading5` as they stand.

Also decided here, because every later part rests on them:

- **Page furniture.** `page.header` and `page.footer` are style settings, each with a left, a
  centre and a right text. They substitute `{page}`, `{pages}`, `{title}`, `{version}` and `{date}`.
  The default footer is `{page} / {pages}` centred, and the default header is empty. `{page}` and
  `{pages}` are `PAGE` and `NUMPAGES` fields. The PDF writer will read the same settings.
- **`docx.update_fields_on_open`** (default true) writes `w:updateFields`. Word then asks once and
  updates the cross-references and the table of contents with its page numbers. LibreOffice and Word
  Online ignore it.
- **Compatibility mode 15**, so Word does not open the file in Compatibility Mode. The settings are in
  the order the schema requires (`updateFields` follows `characterSpacingControl` and precedes
  `compat`), and contain no revision ids.
- **Safety.** One sanitiser is applied to every text, attribute, field instruction, bookmark
  name, relationship and property value. It drops the characters XML cannot carry. Without it
  `xml.etree` writes a control character raw and a lone surrogate as a character reference, and Word
  reports the file as unreadable. The untrusted mode and its style caps apply unchanged.
- **Deterministic output.** Fixed part order, relationship ids by order of use, fixed ZIP times,
  attributes and compression, no timestamps in the properties.

### Cross-references

A cross-reference is a `REF` field to a bookmark on the target, with the switch `\n` (the paragraph
number, no trailing period) and `\h` (a link). The renderer's designation ("2.3(b)", "I.A.1.a(i)") is
covered by segments: literals, and one such field for each numbered unit on the target's path whose
label contributes. A `{path}` label needs one field on the target. An `{n}` label needs one field per
ancestor, joined by the literals the reference templates give. Only when no cover exists does the
planner fall back to one field on the target, with a warning. Under the `none` scheme a reference is
a `REF` to the heading text.

### Rejected alternatives

- **python-docx** (0004's choice), for the reasons in point 2.
- **Static numbers.** Rejected by point 1. It would also make the DOCX the one format whose numbers
  could disagree with the document after an edit, which is the reason to give lawyers a DOCX at all.
- **`\w` and `\r` for cross-references.** Word joins the numbers of the levels itself, and its rules
  produce "2.(a)" for an item of section 2 and "I.A.1.a.(i)" for an outline, not the designation the
  style asks for. The renderer composes the designation from `\n` fields instead.
- **List items that need the section number ("5.1", "5.2" under `{section}`) on reserved deeper
  levels of the heading list.** Word's levels are one hierarchy, so reserving levels 6 to 9 for items
  changes how the headings above them count.
- **`STYLEREF "Heading 1" \n` with a `SEQ` field** for the section prefix of those items. It works
  only in Word, cannot be tested in LibreOffice, and gives no number when the lawyer presses Enter in
  the middle of a list. The items instead go on the heading list's next level, which numbers them
  "%1.%2" and restarts them for each top-level list with a numbering instance that overrides the start.
- **Dropping the section prefix** from such labels. It changes the label the style asks for.

## Consequences

- A DOCX lawyers can edit: insert a clause, update fields (Word does it on open by default), and the
  numbers, references, table of contents and page numbers follow.
- Some restarts cannot be kept live in Word and are documented and warned about, not prevented: a
  nested list of another family than its parent, list items under `{section}` labels, and paragraph
  numbering in a section the user adds. A lawyer who adds a parent item restarts such a list by hand.
- Viewers that never update fields (Word Online, Quick Look, the Google Docs import, Outlook's
  preview) show the cached results, which are right, but the table of contents without page numbers.
- Verification is part of the design: every part is validated against the schemas, the documents are
  opened in LibreOffice (the oracle available in CI, which updates fields and reports the text), and
  the same documents are checked by hand in Word before a release. LibreOffice ignores `\t`,
  `lvlRestart`, `SEQ \s` and `w:updateFields`, so those differences are normalised in the comparison.
- Cost: an OOXML writer of our own, with its schema checks, instead of a library. The numbering
  planner is the bulk of the work.
- Style template additions: `page.header`, `page.footer` and `docx.update_fields_on_open`
  ([style-templates.md](../style-templates.md)). The HTML writer does not read the first two yet.
