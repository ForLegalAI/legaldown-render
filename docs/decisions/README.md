# Decision records

Short records of the decisions that shape this package: the context, the choice, and what it
costs. A record is never rewritten after it is accepted. A later record supersedes it instead.

| # | Decision | Status |
|---|---|---|
| [0001](0001-separate-package.md) | A separate package and repository, depending on `legaldown-validator` | Accepted |
| [0002](0002-one-parser.md) | One LegalDown parser: the core package; CommonMark structure from markdown-it-py for now | Accepted |
| [0003](0003-resolved-document.md) | Resolve all legal semantics once, into a format-neutral resolved document | Accepted |
| [0004](0004-html-first.md) | Output formats in order: text + HTML, then DOCX, then PDF | Accepted (PDF engine open) |
| [0005](0005-locale-formatting.md) | Locale formatting with Babel (CLDR) | Accepted |
| [0006](0006-styles-and-options.md) | Style templates are data; render options are separate | Accepted |

## Template

```markdown
# NNNN. Title

- Status: Proposed | Accepted | Superseded by NNNN
- Date: YYYY-MM-DD

## Context
## Decision
## Consequences
```
