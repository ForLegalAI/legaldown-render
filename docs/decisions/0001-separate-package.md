# 0001. A separate package and repository

- Status: Accepted
- Date: 2026-09-23

## Context

`legaldown-validator` is the reference parser, model, serializer, and Core validator. Its
headline promise is a single runtime dependency, PyYAML. A renderer needs a Markdown engine and
locale data, and eventually DOCX and PDF libraries, some of which need native system libraries.

The specification separates the concerns already. Its conformance levels are cumulative: Core,
then Rendering, then Full (§17). The validator claims Core.

## Decision

The renderer is its own distribution, `legaldown-render`, in its own repository, with the import
name `legaldown_render`. It depends on `legaldown-validator` with a compatible version range. It
claims the Rendering level and runs Core validation through the validator.

## Consequences

- Validator users (CI checks, LLM tooling, editors) never install rendering dependencies.
- Each package has a clear conformance claim: Core for the validator, Rendering for this package.
- The two packages release independently. Styling fixes do not force validator releases.
- The renderer is the first real consumer of the validator's public API, so gaps in that API show
  up here and get fixed upstream ([roadmap](../roadmap.md), U1–U3).
- Cost: a model change needs coordinated releases. Mitigations: a pinned minor range, CI against
  the validator's `main` branch, and both repositories testing against the specification's
  fixtures.
- The import name cannot be `legaldown.render`, because the validator's `legaldown` package is a
  regular package with its own `__init__.py`.
