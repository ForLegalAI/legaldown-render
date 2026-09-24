# 0006. Style templates are data; render options are separate

- Status: Accepted
- Date: 2026-09-23

## Context

A renderer has many settings, and they differ in kind. Some describe a house style that a firm
applies to every document: numbering, enumeration, locale, typography. Others describe one job:
write HTML or text, refuse on errors, produce a page or a fragment. The specification asks for
style templates in a separate file, independent of the document (§13.7). It also asks for the
numbering scheme to be configurable per render job (§13.1).

## Decision

1. **Two layers.** A *style template* (YAML) holds presentation. *Render options* (CLI flags,
   `RenderOptions`) hold the job. Options never change how a document looks, so they are never
   stored in a style. A style never says what to output.
2. **Per-job style overrides** use one generic mechanism: dotted keys (`--set numbering.scheme=none`,
   `overrides={...}`). This satisfies §13.1 without a flag per setting. `--locale` is the one
   shorthand, because it is the most common override.
3. **Layering** runs from built-in defaults, through the `extends` chain, to the chosen style,
   and then to the overrides. Mappings merge field by field.
4. **Validated dataclasses.** The style model is a tree of frozen dataclasses with a default for
   every field. The loader checks the merged data against it and reports every problem with its
   key path before rendering starts.
5. **Numbering as data.** Heading schemes, list enumeration, and paragraph numbering share one
   *level format* (`counter`, `label`, `ref`). The four schemes of §13.1 are presets of it, and
   a house style can express "Article {n}" or "{section}.{n}" without new code.
6. **Labels by language.** Words the renderer generates come from `labels`. Unset labels fall
   back to built-in labels for the document's `language` (§13.7).

## Consequences

- One style file can be shared across a team and versioned in Git next to the documents.
- `--print-style` prints the effective style as a complete YAML file, which makes the layering
  inspectable and gives users a starting point for their own style.
- Adding a setting means adding a dataclass field with a default. Validation, `--set`, and
  `--print-style` pick it up automatically.
- The style format has its own `version`. A breaking change to it must bump that version, and the
  loader rejects formats it does not know.
