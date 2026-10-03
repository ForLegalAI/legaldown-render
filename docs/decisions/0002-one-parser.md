# 0002. One LegalDown parser

- Status: Accepted. Point 2 (CommonMark structure from markdown-it-py) and point 3 (the outline
  check) are superseded by [0007](0007-one-parser-validator-model.md)
- Date: 2026-09-23

## Context

If the renderer parsed LegalDown itself, two implementations could disagree about what a
document means. A document could pass validation and then render a different structure, with
anchors and definitions resolved differently from how they were checked. For a legal document
that is the worst kind of bug, because the output looks correct.

The validator's current `Document` model was built for validation and round-trip editing. It
cannot carry everything a renderer needs:

- Nested lists are flattened into sibling items.
- List continuation lines are joined.
- Inline content is kept as raw strings.
- Item and paragraph anchors (§5.7) are not modelled.
- Specification 0.2 (templates, §15) is not supported yet.

## Decision

1. **The LegalDown layer comes only from `legaldown-validator`.** That means frontmatter and
   metadata, heading identifiers (including §5.3 generation and §5.5 duplicates), definitions,
   and the directive grammar (§11.2, through `legaldown.directives`). The renderer never
   reimplements these.
2. **CommonMark structure comes from markdown-it-py for now.** It provides nested lists, quotes,
   tables, code, emphasis, and links. A plugin hands `{{…}}` and `{#…}` spans to the validator's
   lexer.
3. **The builder checks the two agree.** Its headings must match the validator's sections
   one-to-one (level, title, identifier). A mismatch raises an internal error. It is never
   ignored.
4. **Gaps are fixed upstream.** Nested lists, source positions, and public identifier and
   definition helpers go into the validator ([roadmap](../roadmap.md), U1–U3), not into
   workarounds here.

## Alternatives considered

- **Full CommonMark tree in the validator.** This is the cleanest long-term option. Today it would
  add a dependency to the validator or duplicate a CommonMark parser there. It should be revisited
  once U1–U3 have landed. If the core package grows a complete tree, step 2 can go away.
- **A standalone parser in the renderer.** Rejected because it creates the disagreement risk
  described above.

## Consequences

- The renderer depends on markdown-it-py.
- The outline check turns any disagreement between the two parsers into a loud bug report.
- Some renderer features wait for upstream changes. That is deliberate.
- 2026-10-03: since validator 0.4.0, the directive grammar and the other readings of source
  text come from its supported tooling modules, `legaldown.syntax` and `legaldown.grammar`.
  `legaldown.directives` is internal there.
