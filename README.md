<div align="center">

# legaldown-render 🖨️

### The reference renderer for [LegalDown](https://github.com/ForLegalAI/LegalDown)

**Turn a LegalDown document into HTML, plain text or a Word document, with section numbers, cross-references,
defined terms, and formatted values all generated at render time.**

Targets specification **v0.2** · Conformance level **Rendering** · DOCX in progress, PDF planned

</div>

---

## What it does

[LegalDown](https://github.com/ForLegalAI/LegalDown) documents never hardcode section numbers,
reference text, or formatted values. The source says `{{ref: late-payment}}`, and the renderer
turns it into "4.2(b)" with a link. This package is that renderer:

🔢 **Numbers** sections, list items, and paragraphs under a configurable scheme (§13.1, §13.2).
References to items resolve to designations such as "2.1(b)".

🔗 **Resolves** cross-references, defined terms, parties, sides, and attachment references into
display text with links (§13.3–§13.5).

🌍 **Formats** dates, money, and durations for a locale chosen at render time. A Czech locale
gives "1. června 2026", "10 000,00 Kč", and "12 měsíců", and amounts are never rounded (§10).

🎨 **Applies a style template**, so the same source renders as a US-style contract or a
continental one without editing the text (§13.7).

📝 **Shows templates as templates, or fills them in.** Without answers, conditional clauses are
marked, every `{{choose:}}` phrase is shown, drafting notes are styled, and placeholders render as
blanks. With an answers set, the template is assembled and the finished document rendered (§15.8).

🚩 **Never hides a problem.** Anything unresolved renders as a visible marker, such as
`[BROKEN REF: id]`, and is reported with the specification's stable rule id. Nothing is dropped
silently (§17.5).

Parsing and Core validation come from
[`legaldown-validator`](https://github.com/ForLegalAI/legaldown-validator), the reference parser.
This package never reimplements the LegalDown grammar.

## Install

```bash
pip install legaldown-render
```

Python 3.11 or newer. The dependencies are all pure Python: `legaldown-validator`,
`markdown-it-py`, `babel`, and `pyyaml`.

## Command line

```bash
legaldown-render contract.lgd -o contract.html                  # HTML page
legaldown-render contract.lgd -o contract.txt                   # plain text (format from extension)
legaldown-render contract.lgd -o contract.docx                  # a Word document (in progress, see below)
legaldown-render contract.lgd --style continental --locale cs-CZ -o smlouva.html   # Czech dates, money, durations
legaldown-render contract.lgd --set numbering.scheme=legal-outline --set enumeration.enabled=false
legaldown-render contract.lgd --set contents.enabled=true --set contents.depth=3   # table of contents
legaldown-render contract.lgd --strict                          # refuse if the document has errors
legaldown-render contract.lgd --root . --strict                 # file references may not leave this directory
legaldown-render upload.lgd --untrusted --root uploads/ --style continental   # a document you did not write
legaldown-render contract.lgd --final --strict                  # refuse if blanks or template constructs remain
legaldown-render template.lgd --answers answers.yaml -o nda.html # fill in a template, then render it
legaldown-render --print-style --style continental              # every setting, as YAML
```

`--root DIR` sets the document root (§2.3): a file reference that leaves it (an `{{include:}}`
path, an attachment's, `amends`, `supersedes`, a translation's, an image's) is the Error
`path-outside-root`, and the file is not used. Paths that are not relative (a URI scheme, an absolute
path, a Windows drive) are the Error `path-not-relative` with or without a root. Nothing is ever
read or fetched, and the check is lexical, so symbolic links are not followed. The document must be
inside DIR (exit 2 otherwise); standard input counts as at DIR itself. Set a root when you render documents
you do not control: without one, `../` is allowed (§2.3 defines no default).

`--locale` sets how dates, money, and durations are formatted. The words the renderer generates
(such as "Represented by" or "Signatures") follow the document's `language` (§13.7), whatever the locale.

Diagnostics go to stderr, one per line, as `file:line: level: [rule] message` (the line is that of
the block or heading they are in, or of the frontmatter key they are about; see CONFORMANCE.md).
The exit code is 0 when the document
rendered, even if it has errors (they show as markers), 1 when rendering was refused (strict mode,
or a template that cannot be assembled with its answers) or the document or answers file cannot be
read (a document that is not UTF-8 included) or the output file cannot be written, 2 for an invalid
style, setting, or usage (a document outside `--root` too), and 70 for an internal error (a bug to report).

Documents are read as UTF-8, and output is UTF-8 with LF line endings, on standard output and in
`-o` files, whatever the console's encoding and the platform's newline. The command reconfigures
the standard streams only when it runs as the program; calling `legaldown_render.cli.main([...])`
from Python leaves `sys.stdout` and `sys.stderr` as they are.
Without `--format`, `-o` takes the format from the file extension: `.html` and `.htm` write HTML,
`.txt` and `.text` plain text, `.docx` a Word document. `-o -` writes to standard output. The extension of a document format this renderer does not write
(`.doc`, `.odt`, `.rtf`, `.pdf`, `.md`, `.markdown`, `.epub`, `.tex`, in any case) is a usage
error (exit 2) unless `--format` says what to write. The extension is the text after the last dot,
also in a name that starts with one (`.pdf`), and trailing dots and spaces are ignored (`x.pdf.`,
`"x.odt "`). Any other name, with any other extension or none (`contract`, `contract.v2`,
`report.2025-10-27`, `.out`), is just a name and writes HTML.

## Python

```python
from legaldown_render import render

with open("contract.lgd", encoding="utf-8") as source:
    result = render(source.read(), format="html", style="continental", locale="cs-CZ")
for diagnostic in result.diagnostics:
    print(diagnostic.level, diagnostic.rule, diagnostic.message)
with open("contract.html", "w", encoding="utf-8", newline="\n") as output:
    output.write(result.output)
```

`render()` also accepts a `RenderOptions`. Pass `answers={...}` to render a template filled in.
Each diagnostic has its `line` and its `file`: `render_file()` and the command name the file (its
name, without the directory); for text given to `render()`, pass `filename=` (a directory in it is dropped, `/` or `\`).
`document_root=` is the `--root` of the command line, and `document_path=` says where the document
is in it (`render_file()` sets it from its path; text given to `render()` is at the root); a
document outside the root raises `OptionsError`. For example, a hosting service that renders
uploaded text:
`render(text, document_root="/srv/docs/tenant-1")`.
Problems in the document never raise. Only an invalid style (`StyleError`), an unknown output
format or a document outside its root (`OptionsError`), an unreadable document (`DocumentError`), strict mode, or a template that
cannot be assembled with its answers (`RenderRefused`) do. All of them are `RenderError`s, and
`StyleError` and `OptionsError` are also `ValueError`s. They can be pickled, so they cross process
boundaries (`multiprocessing`, `concurrent.futures`) intact. `overrides=` takes dotted settings and
may not contain `version` or `extends`, so `overrides=legaldown_render.style.style_to_dict(style)` must drop those two keys.

## DOCX (in progress)

`-o contract.docx` (or `--format docx`, or `render(text, format="docx")`) writes a Word document. The
file is bytes: the command writes it to a file or, with `-o -` (or no `-o` when standard output is not a
terminal), to standard output, and refuses to write it to a terminal; `--fragment` is a usage error for it; `render()` returns it as `result.binary` (`result.output` is empty). The writer needs no
dependency beyond the base package.

The design is that **in the Word file every number, cross-reference, the table of contents and the page
numbers are live Word features**, not text: numbering definitions and fields, with the values the renderer
computed as their cached results, so a lawyer who inserts a clause gets the numbers, references and contents
updated by Word
([ADR 0008](https://github.com/ForLegalAI/legaldown-render/blob/main/docs/decisions/0008-docx-word-native.md)).

**What is there today** is the package and the body writer, not yet the numbering: a valid `.docx` (checked
against the ISO/IEC 29500 schemas, and opened in LibreOffice in CI) with styles generated from the style template
(`Normal`, `heading 1` to `heading 5`, `Title`, `Quote`, `toc 1` to `toc 5`, `Hyperlink`, ...), the page size
and margins, a running header and footer from `page.header` and `page.footer` (the default footer is "3 / 12"
as live page fields), the document's title and language, `w:updateFields` so Word refreshes fields when it
opens the file (`docx.update_fields_on_open`), and deterministic output. The body writer lays out headings,
paragraphs, lists (with the numbering definitions, bookmarks and `REF` fields a plan names), quotes, drafting
notes, code, tables with a repeated header row, rules, links, defined terms and the template view's labels and
choices. **The file is still interim:** until the planner that decides the numbering is wired in, it carries
**no numbers, no cross-reference fields, and no title block, contents, attachments or signature blocks**, and
every render reports the Warning `render-docx-interim` to say so. Do not send such a file as a contract. Settings Word
cannot express (a `%` length, say) are an error for DOCX only. See
[Styles and preferences](https://github.com/ForLegalAI/legaldown-render/blob/main/docs/style-templates.md#docx)
and the [roadmap](https://github.com/ForLegalAI/legaldown-render/blob/main/docs/roadmap.md).

## Rendering documents you did not write

Document source never becomes HTML or CSS: raw HTML is not emitted, every text and attribute is
escaped, and a link is made only for an allow-listed scheme. What a service that renders its users'
uploads must add is a boundary for files and a style it does not take on trust:

```python
from legaldown_render import render, RenderOptions

result = render(source, RenderOptions(untrusted=True, document_root="/srv/uploads/acme", style="continental"))
```

`untrusted=True` (`--untrusted` on the command line) does two things:

- It requires a `document_root` (`--root DIR`), so that no file reference can leave the tree the
  document belongs to (§2.3); without one `render` raises `OptionsError` and the command exits 2.
- It loads the style untrusted (`load_style(..., trusted=False)`): a built-in name, a mapping or YAML
  text, never a file; `extends` only a built-in; only plain CSS values (no `calc()`, no `var()`);
  no `html.extra_css`; `values.date` one of `short`, `medium`, `long`, `full`; every label and format
  at most 200 characters; YAML text of at most 64 KiB with no aliases and nested at most 32 levels.
  Errors name the setting and never quote the style or a path, and list at most 20 problems.
  `--untrusted --print-style` checks a style the same way, without needing `--root`. See [Untrusted mode](https://github.com/ForLegalAI/legaldown-render/blob/main/docs/style-templates.md#untrusted-mode).

Without the option a style is the operator's own: it may be a file and may carry raw CSS, though a
value that could leave its place in the stylesheet (`/*`, `</`, a backslash, a control character) or
`</style` in `html.extra_css` is refused in every mode. Even so, choose the style yourself
rather than passing on what a user sends. `--untrusted` does not limit what the document can ask of
the machine beyond file references (nothing is read at Rendering level) or how large it may be:
set your own limits on size and time.

## Styles and preferences

A **style template** says how documents look: numbering, list enumeration, locale, labels,
typography. It is a YAML file you can share across a team:

```yaml
version: 1
extends: continental
locale: cs-CZ
numbering:
  levels:
    - { counter: decimal, label: "Článek {n}", ref: "{n}" }
definitions: { style: small-caps }
typography: { font_family: "Georgia, serif", justify: true }
```

**Render options** say what one job does: the output format, strict mode, the final check, the
answers to a template, a full page or an HTML fragment. Values layer in this order: built-in
defaults, then the `extends` chain, then your style, then per-job `--set` overrides. Every value is validated before rendering starts. See
[Styles and preferences](https://github.com/ForLegalAI/legaldown-render/blob/main/docs/style-templates.md).

Built-in styles are `default` (1. / 1.1, with (a) / (i) list items), `continental` (numbered
articles, 2.1-style items, numbered paragraphs), and `outline` (I. / A. / 1. / a.).

## Documentation

| Document | What it covers |
|---|---|
| [Concepts](https://github.com/ForLegalAI/legaldown-render/blob/main/docs/concepts.md) | What a renderer is responsible for, principles, and vocabulary |
| [Architecture](https://github.com/ForLegalAI/legaldown-render/blob/main/docs/architecture.md) | The pipeline, the render tree, the package layout, and testing |
| [Styles and preferences](https://github.com/ForLegalAI/legaldown-render/blob/main/docs/style-templates.md) | The style format, layering, and every setting |
| [Conformance](https://github.com/ForLegalAI/legaldown-render/blob/main/CONFORMANCE.md) | What the Rendering level requires, and what is covered |
| [Roadmap](https://github.com/ForLegalAI/legaldown-render/blob/main/docs/roadmap.md) | Milestones, and the changes needed upstream |
| [Decision records](https://github.com/ForLegalAI/legaldown-render/blob/main/docs/decisions/) | Why the main choices were made |

## Development

```bash
pip install -e ".[dev]"
pytest                       # unit, golden, and CLI tests
python tests/schemas/fetch.py && pytest -m ooxml_schema   # DOCX parts against the OOXML schemas (needs lxml)
pytest -m libreoffice        # DOCX opened in LibreOffice (needs soffice and python3-uno; skipped without)
pytest --update-golden       # regenerate tests/golden/ after an intended output change
LEGALDOWN_SPEC_DIR=../LegalDown pytest -m conformance   # the specification's examples and fixtures
ruff check .
```

Releases go to PyPI from a GitHub Release; see [PUBLISHING.md](https://github.com/ForLegalAI/legaldown-render/blob/main/.github/PUBLISHING.md).

## License

[MIT](https://github.com/ForLegalAI/legaldown-render/blob/main/LICENSE)
