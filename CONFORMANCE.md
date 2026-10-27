# Conformance

`legaldown-render` 0.3.0 targets **Level 2 — Rendering** of the LegalDown specification **0.2**
(§17.3). Core parsing and validation come from `legaldown-validator` 0.4, which claims Level 1
— Core. Its own [CONFORMANCE.md](https://github.com/ForLegalAI/legaldown-validator/blob/main/CONFORMANCE.md)
lists the Core rules it covers.

It is checked against the specification's own examples and fixtures corpus (`tests/conformance/`):

- every document renders to HTML and to text
- every internal link resolves to an anchor that exists
- each invalid fixture renders its rule's failure marker

## Rendering requirements (§17.3)

| Requirement | Status |
|---|---|
| Section numbering generated at render time, configurable per job (§13.1) | ✅ `decimal`, `legal-outline`, `mixed`, `none`, and custom level formats; per job with `--set numbering.scheme=…` |
| Resolution of every §11.1 directive except `{{include:}}`, with every failure marker (§6.3, §7.3, §13.3–§13.5, §15.8) | ✅ |
| `{{include:}}` below Full (§17.5) | ✅ Renders `[NOT PROCESSED: include path]` with a Warning (`render-not-processed`); the path is checked as a file reference (§2.3) |
| Party and side display rules (§3.6) | ✅ |
| At least one output format (§13.6) | ✅ HTML (recommended) and plain text (optional) |
| A heading in a list item or a quote | ✅ Shown as a bold line: it is not a section (§4.1), so it has no number or anchor |
| Comment stripping (§8.6) | ✅ Inline comments, and comment blocks across any number of lines. As in CommonMark, a comment block left unclosed runs to the end of the document |
| Templates with answers (§15.8) | ✅ Assembled first with `legaldown-validator`'s Assembly capability (§15.7), then rendered: `answers=` / `--answers answers.yaml`. A template that needs other files is refused (see below) |
| Template view without answers (§15.8) | ✅ Conditions marked with style-defined labels; every `{{choose:}}` phrase shown as `[a / b]`; drafting notes styled; alternatives share a number |
| `{{attach:}}` resolves to the declared title without reading the file | ✅ Linked to an attachment placeholder, or to the file when placeholders are omitted |

## SHOULD and MAY features

| Feature | Status |
|---|---|
| List enumeration (§13.2) | ✅ Configurable per depth; can be disabled. Ordered lists are always renumbered. Nested lists keep their structure, so a nested item is referenced as "2.1(b)(i)" |
| Paragraph numbering and section-qualified items (§13.2) | ✅ Style settings; the `continental` style uses both. A preamble item has no section, so its label is "1", not ".1", as its designation is (`{section}` and the `.` after it are dropped) |
| Style templates in a separate file (§13.7) | ✅ YAML, layered and validated; see [docs/style-templates.md](docs/style-templates.md) |
| Signature blocks (§2.2) | ✅ One per party, with `legal_name` and each representative |
| Labels following the document language (§13.7) | ✅ Built in for `en`, `cs`, `de`, `fr`, `pl`, and `sk`; any label can be set by a style |
| A conditional unit's condition is shown (§15.8, MUST) | ✅ Also for a custom style: `labels.condition` must contain `{condition}`, or the style is refused |
| `ref-not-enumerated` Warning (§6.3, §16.3) | ✅ |
| `raw-html` Warning; raw HTML never emitted (§8.7) | ✅ Nothing is emitted: an HTML block is dropped whole, and an inline tag is dropped with its text kept. The Warning is the validator's, once per block or text that holds raw HTML other than comments (see "Text after a comment", below) |
| Automatic term recognition (§7.4, MAY) | ❌ Not supported |
| Links (§8.7) | ⚠️ Partial, a documented deviation for safety with untrusted documents: links with schemes outside the allow-list render as text with a `render-link-refused` Warning. A link is a hyperlink in HTML and shows `text <url>` in plain text when its URL is relative, a fragment, or has one of the schemes `http`, `https`, `mailto`, `tel`, `ftp`, `ftps`, `sftp`, `sip`, `sips`, `sms`, `urn`, `geo`, `news` (an allow-list, judged as a browser reads the URL). Any other scheme (`javascript:`, `file:`, `data:`, protocol handlers such as `ms-msdt:`, `vscode:`, `search-ms:`, and pseudo-schemes such as `C:\x` or `localhost:8080`), or an empty URL, is not a link: its text is shown alone, and the Warning `render-link-refused` is reported for a non-empty URL. A link with no text shows its URL as written; the text writer omits `<url>` for an autolink |
| Images and file-reference paths (§8.7, §2.3) | ✅ Every file reference (`{{include:}}`, `attachments[].file`, `amends.file`, `supersedes.file`, a `translations` value, an image) must be relative, and within the document root when one is configured (`--root DIR`, `document_root=`; see "File references", below). A path with a URI scheme (`data:` too), an absolute one (`/`, `\`, `//host`), a Windows drive path, or one with a control character is the Error `path-not-relative`; a path that leaves the root is the Error `path-outside-root`; `--strict` refuses both. Such an image shows its alt text and is never fetched or embedded; an `{{attach:}}` shows its text with no link; an include marker and an attachment's path stay as text, and no file is ever read. A relative path (`../` included, as §2.3 defines no default root) renders `<img>` in HTML and its alt text in plain text. An empty image path shows the alt text with no diagnostic |
| Plain-text layout (§13.6, optional) | ✅ `attachments.separator` (§13.8): `none` prints no `---`; `rule` and `page-break` both print one before the attachments heading and between consecutive attachments, so the heading stays with the first (text has no pages). One quote stays one quote (blank lines keep the `>`), and the blocks of a list item are set apart by blank lines and indented under the item's label, whatever its condition label; an item that opens with a nested list, table, code block or quote has its label on a line of its own, so that block stays valid. A pipe in a table cell is backslash-escaped, and the separator row marks column alignment (`:---`, `:---:`, `---:`). A label a style blanks drops its `label: ` prefix in the title block, party details and signature blocks (a signature row with neither label nor value is omitted); HTML keeps an empty `<dt>` so its grid layout and the `<dl>` stay valid |
| Extended tables through raw HTML (§9.2, MAY) | ❌ Not supported; the HTML table is dropped with the Warning |
| Visual blank for a placeholder (§13.5 rule 6) | ✅ `placeholders.blank`; a blank that is empty, or only whitespace or invisible format characters (U+200B, U+FEFF), renders `[TBD: id]` |
| Question prompts next to blanks (§15.8, MAY) | ✅ `placeholders.show_prompt` |
| Final check (§15.9, SHOULD) | ✅ `--final` / `final=True`: the validator's `placeholder-unfilled` and `template-construct-present` Errors; with `--strict`, the document is refused |

## Known limitations

- **Assembly reads no other files.** A template with include fragments, LegalDown attachment
  files, or `translations` is refused when rendered with answers, as §17.6 requires below Full.
  A template with Errors is refused too, since §15.7.2 defines no output for one.
- **Line numbers (§16.9).** Every diagnostic names its line, the validator's and the renderer's
  own (`render-ref-ambiguous`, `ref-not-enumerated`, `render-not-processed`,
  `render-link-refused`, `path-not-relative`, `path-outside-root`, `render-term-limit`, `render-term-too-long`,
  `render-designation-cycle`, `render-designation-limit`, `render-locale-fallback`), and the CLI prints it as `file:line:`. The renderer's line is the
  **first line of the block it is in**, as `Document.line_of` gives it, whatever line the
  directive itself is on: a paragraph's, a table's header row, a quote's first `>`, a code
  block's opening fence, a heading's (a section title); for a list item, the line of **its marker**
  (nested items too), also for the blocks the item holds; a block inside a quote has the quote's
  line, since the validator addresses nothing deeper. A `{{ref:}}` or `{{term:}}` paragraph is
  lifted by the validator and written back, so the renderer cannot place the directive in it; the
  validator's own diagnostics for a directive can therefore name a later line of a paragraph than
  the renderer's do, until [validator#93](https://github.com/ForLegalAI/legaldown-validator/issues/93).
  Findings about frontmatter are at the line of their key: the `language` key for
  `render-locale-fallback` of the document's language, and the `title`, `subtitle`, `version`,
  `effective_date`, `sides[].label`, a field of a party (`legal_name`, `label`, `address`,
  `identification_number`, `date_of_birth`, a custom field, a representative's `name` or `title`:
  each at its own key, or where the source writes the value, as with a `<<` merge; the party's key
  where it writes none) or `attachments[].title` for a directive in that value
  (also where the body shows it, as `{{attach:}}` or `{{party:}}` do). A finding in a term's text
  is at the term's definition, whichever reference asks for it first; `render-term-limit` is at the
  definition being worked out when the limit was reached; `render-designation-cycle` is at the
  heading it closes on, and `render-designation-limit` at the first heading that reached a limit
  (or, for a reference longer than the cap, at the reference). The same finding (rule, level and
  message) is reported once, at the lowest line it is seen at: the same refused link, or the same
  non-relative image path, on two lines is one diagnostic, at the earlier of them, whichever the
  renderer reached first (it works out the body before the title block). The renderer's own
  diagnostics are listed in the order of their lines (none last), after the validator's. A document with no source to
  name a line of (built from a dict) reports no line. With `answers`, the lines are those of the
  assembled document (§15.8), not of the template file.
- **File references (§2.3).** The renderer reports `path-not-relative` and `path-outside-root`
  (§2.3, §16.2) for every file reference itself, because the validator (0.4) checks neither rule.
  It leaves its own report out when the validator names the same path at the same line (or no
  line), and keeps it for a path the validator did not name, or the same path at another line.
  A path with both problems (`/../x`) is `path-not-relative`. The reports are at the line of the
  block holding the `{{include:}}` or image, or of the frontmatter key (`attachments[].file`,
  `amends.file`, `supersedes.file`, `translations.<language>`), like the rest.
  The checks are lexical, as §2.3 says: no path is resolved against the file system, no symbolic
  link is followed, and no file is read or its existence checked (that is Full, §17.4).
  The containment rule runs only where a root is set, as §2.3 says (it defines no default root,
  and a renderer for documents it does not control SHOULD set one): `--root DIR`, or
  `RenderOptions.document_root`. The root is the boundary, and the base of a path is the directory
  of the document relative to it, so `../shared/a.lgd` is within the root from `<root>/contracts/x.lgd`
  and outside it from `<root>/x.lgd`. `render_file` and the command line say where the document is
  (`RenderOptions.document_path`); text given to `render()`, or standard input, is at the root. A
  document outside the root, or on another Windows drive, is an `OptionsError` (exit 2).
  How a path is read: a backslash is a separator, so `..\..\x` leaves the root as `../../x` does
  (the renderer's reading; a leading `\` is not relative anyway); `..` followed by dots and
  spaces (`.. `, `...`) is `..`, and `.` followed by spaces is `.` (Windows reads them so);
  tabs and newlines inside a scheme, and spaces and control characters at either end, are ignored
  as a browser ignores them. A control character left inside a path (NUL, ESC, any other of Unicode
  category Cc), after the decoding below and what is ignored above, makes it `path-not-relative`;
  a newline at the end of a YAML block scalar is ignored. Percent-encoding is
  decoded once, only for what is used as a URL, an image's source and the link `{{attach:}}` would
  make to an attachment's file: there `%2e%2e/` is `../`. A path in the frontmatter or an
  `{{include:}}` is a file name, read as written, where `%2e%2e` is a directory with that name.
  An attachment's `file` is read as a URL only where it becomes a link (`attachments.render: omit`
  with `{{attach:}}`), so a percent-encoded traversal in it is reported only in that mode.
  A relative file name with a colon before any `/` reads as a URI scheme (§2.3), so it is
  `path-not-relative`: write `./report:v2.png`.
  `supersedes` as a plain string describes the superseded document (§3.2) and is not a path.
  Markdown links (`[text](/etc/passwd)`, §8.7) are not file references and stay governed by the
  link allow-list (`render-link-refused`): they are links the reader may follow, not files the
  renderer would read.
- **Nesting limit.** The validator reads lists 64 levels deep and quotes 16; deeper, an item's or
  quote's content is its text. The renderer follows it, and refuses (`DocumentError`) a document
  in which the enclosing lists and quotes plus one text's inline formatting (emphasis, links) nest
  more than 100 levels, while building it and before resolving or writing it, so no document can
  exhaust the stack. The count is per text: a defined term's own text (`"A *b*" {{def: x}}`) is
  built separately, so the emphasis inside the term does not add to the emphasis around it. Each
  is checked against the limit with the enclosing lists and quotes, and their sum can exceed 100.
- **Comment-only items and quotes.** A list item whose blocks are all comments or raw HTML is
  left out (§8.6: the author removed it), and so is a list left with no items and a quote that
  holds only comments. Numbers and designations are those of the items that remain. An item with
  no content at all keeps its place (CommonMark), and so does an empty drafting note, whose label
  is the template construct (§15.6).
- **Text after a comment.** A comment that starts a line is a CommonMark HTML block, which ends at
  the line holding `-->`. Anything after the `-->` on that line belongs to the block and is dropped
  with it, with the validator's `raw-html` Warning: `<!-- c --> text` renders nothing (the lines
  after it are ordinary paragraphs again). A line holding only the comment is dropped with no
  Warning, and a comment inside a line of text (`before <!-- c --> after`) is dropped alone,
  keeping the text around it (§8.6).
- **Directives in a link or image destination.** A destination is not text: `[x](#{{ref: a}})`,
  `<https://e.com/{{ref: a}}>` and `![i](p/{{field: x}}.png)` are not resolved. The `href` or `src`
  holds the directive's source, percent-encoded (`#%7B%7Bref:%20a%7D%7D`). The validator still reads
  the directive, so a broken `{{ref:}}` there is reported (`ref-broken`), and the visible text of
  an autolink shows it resolved, unlike its target. Directives in link text and alt text are
  resolved.
- **Table cells.** A row with more cells than the header has loses the extra cells, as GFM
  specifies, with no diagnostic from the validator or the renderer; a shorter row is padded with
  empty cells.
- **Parties and `{{party:}}`.** The title block lists each party as an identification block, by
  `legal_name` (then `label`, then `name`), and the signature blocks use `legal_name` (§3.6). The
  text uses `label`, falling back to `legal_name` (§3.6): "the Seller" in a sentence, "Acme
  Corporation" in the title block.
- **`{{term:}}` text.** A `{{term:}}` shows the term as rendered at its first definition in the
  body, with its Markdown, `{{ref:}}`s and placeholders resolved as at the definition
  (`"A *b* c" {{def: abc}}` is "A b c", `"Fee per {{ref: h}}"` is "Fee per 1"), not the term's raw
  source; `label=` overrides it. A definition not in the body (a template's other alternative)
  falls back to the validator's term text.
  A term that refers to itself, directly or through other terms (`"A {{term: a}}" {{def: a}}`), is
  rendered without looping: the inner reference shows its `label=`, else the definition id. A term text
  worked out inside a cycle depends on which terms are open, so it is kept with the open terms it
  depends on and reused only where those are open; a text with no cycle is worked out once per term,
  also in a heading read before the targets are known (§13.3). Terms that name each other can still
  ask for more different texts than any cache holds, so the extra work is bounded: a term's first text
  is free, and past 200,000 inline nodes resolved inside later texts of the same term (nested ones
  included), 32 terms open inside one another, or 60 inline levels (emphasis, links, terms) nested
  inside an open term, a `{{term:}}` shows its `label=` or definition id and the Warning
  `render-term-limit` is reported, once. The nesting limit only applies where one unit is read
  inside another (inside an open term, or while designations are worked out, below): a term in a
  single block, however deeply formatted (the builder allows 100 levels), is never cut by it. Any number of terms that do not name each
  other resolve in full and never reach the limit. A term text is capped at 1,000 characters
  of plain text, checked as it is built (no real defined term comes near it; a chain of terms that each
  read the next twice would otherwise ask for a text of 2^n words): a longer one is shown by its
  definition id in `{{term:}}` (a `label=` still wins), and the Warning `render-term-too-long` names the
  id, once. The
  validator (0.4.0) does not report these cycles; the specification's `definition-circular`
  Warning (§16) is not emitted yet.
- **Ambiguous references.** Each list starts again at its first number, as contracts are usually
  drafted, so two lists in one section can both have an item "(a)", and a style can number a
  paragraph or an item like a subsection ("1.1"). A `{{ref:}}` to a section, paragraph, or list
  item whose designation another of them also reads as renders with a `render-ref-ambiguous`
  Warning. It is a hint: it is left out when the two can never appear together (alternatives,
  exclusive conditions), but the condition the reference itself stands under is not considered.
  Lists in quotes and drafting notes are not counted, and a second use of an identifier is left
  to the validator's `anchor-duplicate`.
- **Designations under the `none` scheme (§13.3).** A `{{ref:}}` shows the target's heading as
  rendered: the references and terms in the heading resolved, as plain text with white space
  collapsed (`# About {{ref: ov}}` is "About Overview"), then the item path or paragraph number for
  an item or paragraph ("Termination (a)"). A heading that refers back to itself, directly or
  through other headings or terms, reads as its text without references where the circle closes
  (`render-designation-cycle`). Headings read through more than 32 headings, nested more than 60
  inline levels while headings and terms are read through each other, or longer than 1,000
  characters once resolved are designated by their text as written, without the references in them
  (`render-designation-limit`), and a reference shows at most the first 1,000 characters of a
  designation. A heading in a cycle or past a cap, or one whose terms reach into one, may read
  differently from the designation references show; the Warning says so. Alternative sections
  sharing an identifier (§15.4) keep their own heading text, and a reference to the identifier
  shows the first alternative's.
- **Labels under the `none` scheme.** A style label that shows `{section}` (such as the default
  paragraph label `{section}.{n}`) shows the section's designation there (its heading text, as
  above), while `{{ref:}}` to an item writes the §13.3 path after it ("Termination 1"), so the two
  can differ.
- **Malformed directives** (a §11.2 grammar violation) render their type's marker without a value,
  for example `[INVALID AMOUNT]`. The specification defines no marker for this case.
- **One parser.** The renderer builds from the validator's document model only
  ([ADR 0007](docs/decisions/0007-one-parser-validator-model.md)). Anchors, markers, and the
  template view therefore always agree with the validator's diagnostics. The price is that some
  structure renders as the validator's model holds it, until that model keeps more (roadmap
  U1b):
  - **Link reference definitions** (`[label]: url`) render as paragraphs, and the links that use
    them as text ([#92](https://github.com/ForLegalAI/legaldown-validator/issues/92)).

## Beyond this level (Full, §17.4)

These are not processed, and each renders visibly rather than being dropped:

- includes (§12)
- attachment contents and per-attachment numbering scopes (§13.8)
- cross-scope reference qualification (§13.3)
- amendment processing (§7.5)
- bilingual sets (§14)
- file existence checks
