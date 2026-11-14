# The render tree

`render()` returns the resolved render tree as `RenderResult.tree`, next to the output. It is the
document after the resolve stage ([architecture](architecture.md)): every section, item and
paragraph has its final label, designation and anchor, every reference, term and value has its
display text, and every failure has its marker. The built-in writers draw only what it holds
([ADR 0003](decisions/0003-resolved-document.md)). A host that writes its own output format,
such as a PDF writer built on ReportLab, can do the same.

```python
from legaldown_render import render
from legaldown_render.tree import CrossRef, FailureMarker, Paragraph, Section, Text, assert_resolved

result = render(source, format="text")
tree = result.tree
assert_resolved(tree)  # the same check the built-in writers make
for section in tree.sections:
    print(section.level, section.label, section.anchor)
    for block in section.blocks:
        match block:
            case Paragraph(inlines=inlines):
                for inline in inlines:
                    match inline:
                        case Text(text=text): ...
                        case CrossRef(text=text, target=target): ...   # link text to #target
                        case FailureMarker(text=text): ...
                        case _: ...                                    # always have a fallback
```

`RenderTree` is also importable from `legaldown_render`. Everything else is in
`legaldown_render.tree`: the names in its `__all__` are public. The node types are frozen
dataclasses. The docstrings in [`tree.py`](../src/legaldown_render/tree.py) describe each field.

## What is in the tree

| Part | Names |
|---|---|
| The document | `RenderTree`, with `NumberingFormats` (and its `LevelFormat`s), `ContentsEntry`, `SideInfo`/`PartyInfo`, `AttachmentPart`, `SignatureParty` |
| Sections | `Section`, in `RenderTree.sections`: kept flat, in document order (`level` gives the hierarchy). It is not a `Block`; its `blocks` are |
| Blocks (`Block`) | `Paragraph`, `List`/`ListItem` (`ListFamily`), `Quote`, `DraftingNote`, `CodeBlock`, `Table`, `Rule` |
| Inlines (`ResolvedInline`) | `Text`, `SoftBreak`, `HardBreak`, `Emphasis`, `Strong`, `Code`, `Link`, `Image`, `CrossRef`, `TermRef`, `DefinedTerm`, `Value`, `Blank`, `Choice`, `FailureMarker` |
| Helpers | `assert_resolved`, `is_resolved`, `iter_inlines`, `iter_tree_inlines`, `plain_text`, `has_visible_content` |

What a writer draws, and where it comes from:

- **Labels and numbers.** `label` ("2.1", "(a)") on sections, items and numbered paragraphs, and
  `designation` on sections. A writer that numbers itself (as the DOCX writer does with Word
  numbering) follows `unit`, `number`, `parent_unit` and `shares_number_with`, and
  `RenderTree.formats`, the formats the resolver used.
- **Anchors and links.** `anchor` on sections, items, paragraphs, defined terms and attachments,
  and `RenderTree.attachments_anchor`. A `CrossRef.target` or a `TermRef.target` is an anchor of
  the same tree (without `#`). `Value.href` is `"#"` and an anchor for `{{attach:}}`, else a URL or
  file path. A `Link.href` is as the author wrote it, so it may be an in-document `#anchor` too.
  Anchors can contain `:` (`def:fee`, `ld:attachments`).
- **Text.** Every inline holds its display text; `plain_text()` joins it.
- **Conditions.** `condition_label` is the text to show for a conditional unit in the template
  view, or None.
- **Labels of the front and back matter.** `contents_label`, `attachments_label`,
  `signature_labels` (keys `date`, `place`, `name`, `title`, `heading`) and `colon`, in the
  document's language.

Fields that come straight from the source, `Section.identifier`, `condition`, and `anchor_id` on
items and paragraphs, are for information. A writer shows the resolved fields beside them
(`label`, `anchor`, `condition_label`) and does not interpret them. `line` on blocks is the source
line, for diagnostics.

The tree holds no output-format detail: an id prefix, a CSS class or a bookmark name is the
writer's. The DOCX writer, for instance, makes `ld_…` bookmarks from the anchors, and the HTML
writer's `html_id_prefix` changes its ids, not the tree.

The build stage also has two source inlines, `DirectiveSource` and `DefinitionSource`, which hold
LegalDown syntax. They are internal, and a tree from `render()` never holds one. The fields are
annotated with `Inline`, the build stage's union, which includes them. `ResolvedInline` is the
union of what a resolved tree can hold. `is_resolved()` says whether a tree holds a source inline,
and `assert_resolved()` raises `InternalError` if it does.

## Compatibility

Before 1.0, the tree follows the same rules as the rest of the API:

- **A patch release (0.6.x) never changes it** in a way that breaks a reader: no field or node type
  is removed, renamed or given another meaning.
- **A minor release (0.x) may**, and its release notes ([roadmap](roadmap.md)) list every change
  to the tree. Where it can, a renamed name keeps working, with a `DeprecationWarning`, until the
  next minor release.
- **Additions are not breaking**, in any release: a new field, with a default, and a new node type
  (a new inline or block, for a new LegalDown construct). So:
  - construct and match nodes **by keyword**, never by position. Fields are not always added at
    the end.
  - give every `match` on inlines and blocks a **fallback** case. `plain_text()` gives the text
    of an inline that a writer does not know.
- **Provisional.** These shapes may still change in a minor release without a deprecation
  period: `RenderTree.header`, `PartyInfo.details` and `SignatureParty.signatories` (tuples of
  tuples, which may become nodes), `RenderTree.signature_labels` (a `dict`, which may become an
  immutable mapping or a node; read it, never change it), and `Value.href` (which may separate
  internal targets from URLs, as `CrossRef.target` does).

The tree is what the resolver computed, not a format of its own: it is not serialized, and
nothing reads one back.
