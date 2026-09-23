"""The rendering pipeline and its public API (docs/architecture.md).

Preferences are split in two, on purpose:

* the **style template** says how documents look — numbering, enumeration,
  locale, labels, typography. It is a reusable YAML file (``--style``), with
  single values overridable per job (``--set key=value``, ``overrides``);
* **render options** say what this one job does — the output format, strict
  mode, a full HTML page or a fragment. They never change how a document
  looks, so they are not part of the style.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from legaldown import Diagnostic, Document, ValidationResult, parse_document, validate_document

from .build import build_tree, normalize_source
from .errors import DocumentError, RenderRefused
from .resolve import Formatter, parse_locale, resolve_tree
from .style import Style, StyleError, load_style
from .tree import RenderTree, assert_resolved
from .writers import FORMATS, HtmlWriter, TextWriter


@dataclass(frozen=True, slots=True)
class RenderOptions:
    """Settings for one render job."""

    #: ``html`` or ``text``.
    format: str = "html"
    #: A built-in style name, a path to a YAML style template, or a Style.
    style: str | Path | Style | None = None
    #: Per-job style overrides by dotted key, e.g. ``{"numbering.scheme": "none"}``.
    overrides: Mapping[str, Any] = field(default_factory=dict)
    #: Shorthand for ``overrides={"locale": ...}``: the formatting locale.
    locale: str | None = None
    #: Refuse to render (raise RenderRefused) when the document has errors,
    #: instead of rendering them as visible failure markers.
    strict: bool = False
    #: HTML only: a complete page (True) or just the ``<article>`` (False).
    standalone: bool = True


@dataclass(frozen=True, slots=True)
class RenderResult:
    """What a render produced."""

    output: str
    #: Core (validator) and Rendering diagnostics, each with its stable rule id.
    diagnostics: list[Diagnostic]
    format: str
    #: The resolved tree, for tools that want more than the output text.
    tree: RenderTree
    style: Style

    @property
    def ok(self) -> bool:
        """True when the document has no errors."""
        return not any(diagnostic.level == "error" for diagnostic in self.diagnostics)


def render(source: str, options: RenderOptions | None = None, /, **settings: Any) -> RenderResult:
    """Render LegalDown *source*.

    Settings are a :class:`RenderOptions` or the same fields as keywords::

        render(text, format="html", style="continental", locale="cs-CZ")

    Problems in the document never raise: they render as failure markers and
    are listed in ``diagnostics``. Raises :class:`StyleError` for an invalid
    style or setting, :class:`DocumentError` for a document that cannot be
    read, and :class:`RenderRefused` in strict mode when the document has
    errors.
    """
    if options is None:
        options = RenderOptions(**settings)
    elif settings:
        raise TypeError("pass either a RenderOptions or keyword settings, not both")
    if options.format not in FORMATS:
        raise ValueError(f"unknown output format '{options.format}' (known: {', '.join(FORMATS)})")
    overrides = dict(options.overrides)
    if options.locale:
        overrides["locale"] = options.locale
    style = load_style(options.style, overrides=overrides)

    source = normalize_source(source)
    document = _parse(source)
    result = validate_document(document)
    diagnostics = list(result.diagnostics)
    if options.strict and any(d.level == "error" for d in diagnostics):
        raise RenderRefused(diagnostics)

    tree, extra = _resolve(document, result, style)
    diagnostics += [d for d in extra if d not in diagnostics]
    if options.strict and any(d.level == "error" for d in diagnostics):
        raise RenderRefused(diagnostics)

    assert_resolved(tree)
    output = _writer(options, style).write(tree)
    return RenderResult(output, diagnostics, options.format, tree, style)


def render_file(path: str | Path, options: RenderOptions | None = None, /, **settings: Any) -> RenderResult:
    """Render the LegalDown file at *path* (see :func:`render`)."""
    return render(Path(path).read_text(encoding="utf-8"), options, **settings)


def _parse(source: str) -> Document:
    try:
        return parse_document(source)
    except (ValueError, yaml.YAMLError) as error:
        raise DocumentError(f"The document cannot be read: {error}") from error


def _resolve(document: Document, result: ValidationResult, style: Style) -> tuple[RenderTree, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    tree, raw_html = build_tree(document, result)
    if raw_html:
        diagnostics.append(Diagnostic(
            rule="raw-html",
            level="warning",
            message=f"{raw_html} raw HTML construct(s) in the body are not rendered; HTML does not render "
                    f"portably (§8.7).",
        ))
    locale = parse_locale(style.locale) if style.locale else None
    if style.locale and locale is None:
        raise StyleError("setting", [f"locale: '{style.locale}' is not a known locale (e.g. en-US, cs-CZ)"])
    if locale is None:
        # No locale set: the document's language is the hint (§10.1).
        locale = parse_locale(tree.language)
        if locale is None:
            diagnostics.append(Diagnostic(
                rule="render-locale-fallback",
                level="info",
                message=f"No formatting locale is set and the document language '{tree.language}' is not a "
                        f"known locale; values are formatted for 'en'. Set one with --locale or the style's "
                        f"'locale'.",
            ))
            locale = parse_locale("en")
    assert locale is not None
    resolved, found = resolve_tree(tree, document, result, style, Formatter(locale, style.values))
    return resolved, diagnostics + found


def _writer(options: RenderOptions, style: Style) -> HtmlWriter | TextWriter:
    if options.format == "text":
        return TextWriter()
    from . import __version__

    return HtmlWriter(style, standalone=options.standalone, generator=f"legaldown-render {__version__}")
