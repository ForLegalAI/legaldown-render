# 0007. One parser: build from the validator's document model

- Status: Accepted. Supersedes the "CommonMark structure from markdown-it-py" part of
  [0002](0002-one-parser.md)
- Date: 2026-09-23

## Context

[ADR 0002](0002-one-parser.md) took the LegalDown layer from `legaldown-validator` and, as a
stopgap, the Markdown block structure from markdown-it-py. That way nested lists could render
before the validator kept them
([ForLegalAI/legaldown-validator#14](https://github.com/ForLegalAI/legaldown-validator/issues/14)).

In practice the two parsers disagreed wherever their readings differ:

- a heading inside an HTML comment
- a `# Signature Block` heading
- the paragraphs of a multi-paragraph list item
- a list item that opens with a heading or a fenced code block

Each disagreement became a crash of the outline check, or an anchor, marker or template decision
that contradicted the validator's own diagnostics. Five review rounds went mostly into patching
these. Each fix mirrored another of the validator's rules in the renderer, and the next review
found another edge case.

## Decision

The render tree is built **from the validator's `Document` only**:

- **Block structure:** sections, blocks, list items, quotes, tables, and code all come from the
  validator's model.
- **Markers:** placed exactly where the validator's own `find_markers()` places them.
- **Template decision:** the validator's own formula over those markers.
- **Quote content, and code inside list items:** read with the validator's parser too.

markdown-it-py stays, in **inline mode only**, for the Markdown inside one block's text: emphasis,
links, code spans. It never decides what is a heading, a list, a paragraph, or a marker, so it
cannot disagree with the validator about any of them.

Everything the renderer takes from the validator beyond its public API is imported in one
module, `validator_bridge.py`. That module is the list for roadmap item U3.

## Consequences

- A whole class of bugs is gone by construction. There is no outline check that can crash, and
  rendered anchors, markers, and the template view always agree with the validator's
  diagnostics.
- **Limitations** until the validator's model improves (roadmap U1 and U2), listed in
  `CONFORMANCE.md`:
  - lists render with one level of items, so references to them read "2.1(c)", never
    "2.1(b)(i)"
  - table column alignment is not kept
  - a paragraph's line breaks are joined
  - constructs the validator reads unusually, such as a heading inside an HTML comment, render
    as the validator reads them
- One deliberate exception was added later: a comment left open in a paragraph ran on to its
  `-->` across the following blocks, because §8.6 requires every comment to be stripped. It was
  removed once the validator's model kept HTML blocks (validator #23), which is the path this
  decision expects: the fix lands in the validator, and the renderer drops its workaround.
  Table alignment came with the same change.
- When the validator keeps nested lists (#14), the renderer gains them with no parser of its
  own. The resolver and writers already handle nested lists.
- The build stage is smaller: about 100 lines of position rules and special cases are gone.
