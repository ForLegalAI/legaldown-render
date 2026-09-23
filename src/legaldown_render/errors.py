"""Errors raised by the renderer. Problems in the *document* are never
raised: they render as visible failure markers and are reported as
diagnostics. These are for everything else."""
from __future__ import annotations

from legaldown import Diagnostic


class RenderError(Exception):
    """Base class for every error the renderer raises."""


class DocumentError(RenderError):
    """The document cannot be read at all, e.g. its frontmatter is not a
    YAML mapping."""


class RenderRefused(RenderError):
    """Rendering was refused because the document has errors and strict
    mode is on. ``diagnostics`` holds every finding."""

    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        self.diagnostics = diagnostics
        errors = sum(1 for diagnostic in diagnostics if diagnostic.level == "error")
        super().__init__(f"Rendering refused in strict mode: the document has {errors} error(s).")


class InternalError(RenderError):
    """The renderer reached a state that is a bug, such as its parser and
    legaldown-validator disagreeing about the document. It never produces
    plausible-looking wrong output instead."""
