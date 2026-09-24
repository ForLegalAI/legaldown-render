<div align="center">

# legaldown-render 🖨️

### The reference renderer for [LegalDown](https://github.com/ForLegalAI/LegalDown)

**Turn a LegalDown document into HTML or plain text, with section numbers, cross-references,
defined terms, and formatted values all generated at render time.**

Targets specification **v0.2** · Conformance level **Rendering** · DOCX and PDF planned

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

📝 **Shows templates as templates.** Conditional clauses are marked, every `{{choose:}}` phrase is
shown, drafting notes are styled, and placeholders render as blanks (§15.8).

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
legaldown-render contract.lgd --style continental --locale cs-CZ -o smlouva.html
legaldown-render contract.lgd --set numbering.scheme=legal-outline --set enumeration.enabled=false
legaldown-render contract.lgd --set contents.enabled=true --set contents.depth=3   # table of contents
legaldown-render contract.lgd --strict                          # refuse if the document has errors
legaldown-render contract.lgd --final --strict                  # refuse if blanks or template constructs remain
legaldown-render --print-style --style continental              # every setting, as YAML
```

Diagnostics go to stderr, one per line with its rule id. The exit code is 0 when the document
rendered, even if it has errors (they show as markers), 1 when rendering was refused or the file
cannot be read, and 2 for an invalid style or setting.

## Python

```python
from legaldown_render import render

result = render(open("contract.lgd").read(), format="html", style="continental", locale="cs-CZ")
for diagnostic in result.diagnostics:
    print(diagnostic.level, diagnostic.rule, diagnostic.message)
open("contract.html", "w").write(result.output)
```

`render()` also accepts a `RenderOptions`. Problems in the document never raise. Only an invalid
style (`StyleError`), an unreadable document (`DocumentError`), or strict mode
(`RenderRefused`) do.

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

**Render options** say what one job does: the output format, strict mode, the final check, a full
page or an HTML fragment. Values layer in this order: built-in defaults, then the `extends` chain, then your
style, then per-job `--set` overrides. Every value is validated before rendering starts. See
[Styles and preferences](docs/style-templates.md).

Built-in styles are `default` (1. / 1.1, with (a) / (i) list items), `continental` (numbered
articles, 2.1-style items, numbered paragraphs), and `outline` (I. / A. / 1. / a.).

## Documentation

| Document | What it covers |
|---|---|
| [Concepts](docs/concepts.md) | What a renderer is responsible for, principles, and vocabulary |
| [Architecture](docs/architecture.md) | The pipeline, the render tree, the package layout, and testing |
| [Styles and preferences](docs/style-templates.md) | The style format, layering, and every setting |
| [Conformance](CONFORMANCE.md) | What the Rendering level requires, and what is covered |
| [Roadmap](docs/roadmap.md) | Milestones, and the changes needed upstream |
| [Decision records](docs/decisions/) | Why the main choices were made |

## Development

```bash
pip install -e ".[dev]"
pytest                       # unit, golden, and CLI tests
pytest --update-golden       # regenerate tests/golden/ after an intended output change
LEGALDOWN_SPEC_DIR=../LegalDown pytest -m conformance   # the specification's examples and fixtures
ruff check .
```

## License

[MIT](LICENSE)
