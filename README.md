<div align="center">

# legaldown-render 🖨️

### The reference renderer for [LegalDown](https://github.com/ForLegalAI/LegalDown)

**Turn a LegalDown document into HTML now, with DOCX and PDF planned. Section numbers,
cross-references, defined terms, and formatted values are all generated at render time.**

Status: **design phase**. There is no code yet. Start with [the concepts](docs/concepts.md).

</div>

---

## What it will do

[LegalDown](https://github.com/ForLegalAI/LegalDown) documents never hardcode section numbers,
reference text, or formatted values. The source says `{{ref: payment-terms}}`, and the renderer
turns it into "Section 4.2" with a link to that section. This package is that renderer:

🔢 **Numbers** sections and list items under a configurable scheme (§13.1, §13.2).

🔗 **Resolves** cross-references, defined terms, parties, sides, and attachment references into
display text with links (§13.3, §13.4, §13.5).

🌍 **Formats** dates, money, and durations for a locale chosen at render time, not stored in the
document (§10).

🎨 **Applies a style template**, so the same source can render as a US-style contract or a
continental one without editing the text (§13.7).

🚩 **Never hides a problem.** Anything unresolved renders as a visible marker, such as
`[BROKEN REF: id]`, and is reported with the specification's stable rule id. Nothing is ever
dropped silently (§17.5).

📄 **Writes** HTML first, then DOCX, then PDF. Every output format comes from the same resolved
document, so a clause gets the same number in all of them.

It targets conformance **Level 2 — Rendering** of the specification (§17.3). Parsing and Core
validation come from [`legaldown-validator`](https://github.com/ForLegalAI/legaldown-validator),
the reference parser. This package does not reimplement the LegalDown grammar.

## Planned usage

> Everything in this section is illustrative. The API is not implemented yet and may change.

```bash
pip install legaldown-render                 # HTML
pip install "legaldown-render[docx,pdf]"     # later: DOCX and PDF

legaldown-render contract.lgd -o contract.html
legaldown-render contract.lgd -o contract.html --style continental --locale cs-CZ
legaldown-render contract.lgd -o contract.html --strict   # refuse to render on any error
```

```python
from legaldown_render import render

result = render(open("contract.lgd").read(), to="html", style="default")
for diagnostic in result.diagnostics:
    print(diagnostic.level, diagnostic.rule, diagnostic.message)
open("contract.html", "w").write(result.output)
```

## Documentation

| Document | What it covers |
|---|---|
| [Concepts](docs/concepts.md) | The ideas and vocabulary: what a renderer is responsible for, the principles, the terms used in the rest of the docs |
| [Architecture](docs/architecture.md) | The pipeline, the render tree, package layout, dependencies, error handling, testing |
| [Style templates](docs/style-templates.md) | Draft of the style template format |
| [Roadmap](docs/roadmap.md) | Milestones, and the changes needed in `legaldown-validator` and the specification first |
| [Decision records](docs/decisions/) | Why the main choices were made |

## Related repositories

| Repository | Role |
|---|---|
| [LegalDown](https://github.com/ForLegalAI/LegalDown) | The specification and its conformance fixtures |
| [legaldown-validator](https://github.com/ForLegalAI/legaldown-validator) | Reference parser, document model, serializer, and Core validator; this package depends on it |
| **legaldown-render** | Rendering (this repository) |

## License

[MIT](LICENSE)
