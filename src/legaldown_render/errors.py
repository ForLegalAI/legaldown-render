"""Errors raised by the renderer. Problems in the *document* are never
raised: they render as visible failure markers and are reported as
diagnostics. These are for everything else."""
from __future__ import annotations

from legaldown import Diagnostic


class RenderError(Exception):
    """Base class for every error the renderer raises."""


class StyleError(RenderError, ValueError):
    """A style template or override is invalid. ``problems`` lists each
    problem with its key path.

    It is a :class:`ValueError` as well as a :class:`RenderError`, so code
    that caught it as a ``ValueError`` keeps working. It lives here, with the
    other errors, and is re-exported by :mod:`legaldown_render.style`."""

    def __init__(self, source: str, problems: list[str]) -> None:
        self.source = source
        self.problems = problems
        details = "\n".join(f"  - {problem}" for problem in problems)
        super().__init__(f"Invalid style {source}:\n{details}")

    def __reduce__(self) -> tuple[type[StyleError], tuple[str, list[str]]]:
        # ``Exception`` pickles ``args``, which here is the one formatted message,
        # and the two-argument constructor would then fail to rebuild it
        # (multiprocessing would raise BrokenProcessPool instead of the error).
        return type(self), (self.source, self.problems)


class OptionsError(RenderError, ValueError):
    """The render options are invalid, e.g. an unknown output format. It is
    a :class:`ValueError` as well, as the bare ``ValueError`` it replaces
    was."""


class DocumentError(RenderError):
    """The document cannot be read at all, e.g. its frontmatter is not
    valid YAML."""


class RenderRefused(RenderError):
    """Rendering was refused: the document has errors and strict mode is on,
    or a template could not be assembled with its answers (§15.7).
    ``diagnostics`` holds every finding; ``reason`` is the message given when
    it was raised, or None for the strict-mode default."""

    def __init__(self, diagnostics: list[Diagnostic], reason: str | None = None) -> None:
        self.diagnostics = diagnostics
        self.reason = reason
        errors = sum(1 for diagnostic in diagnostics if diagnostic.level == "error")
        super().__init__(reason or f"Rendering refused in strict mode: the document has {errors} error(s).")

    def __reduce__(self) -> tuple[type[RenderRefused], tuple[list[Diagnostic], str | None]]:
        # See StyleError.__reduce__: the constructor does not take the message.
        return type(self), (self.diagnostics, self.reason)


class InternalError(RenderError):
    """The renderer reached a state that is a bug, such as a marker the
    validator placed where the builder cannot find it, a block kind the
    builder does not know, or an unresolved node reaching a writer. It never
    produces plausible-looking wrong output instead."""
