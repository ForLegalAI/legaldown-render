"""The rendering pipeline and its public API (docs/architecture.md).

Preferences are split in two, on purpose:

* the **style template** says how documents look — numbering, enumeration,
  locale, labels, typography. It is a reusable YAML file (``--style``), with
  single values overridable per job (``--set key=value``, ``overrides``);
* **render options** say what this one job does — the output format, strict
  mode, the final check, the answers to a template, a full HTML page or a
  fragment. They never change how a document
  looks, so they are not part of the style.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from legaldown import AssemblyError, Diagnostic, Document, ValidationResult, parse, parse_template, validate

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
    #: The final check (§15.9): the document is meant for signature, so a
    #: remaining blank (``placeholder-unfilled``) or template construct
    #: (``template-construct-present``) is an Error. With ``strict``, such a
    #: document is refused.
    final: bool = False
    #: An answers set (§15.7.1): question id to answer, as YAML or JSON
    #: would load it. With answers, a template is assembled first and the
    #: assembled document is rendered (§15.8); without, a template renders
    #: as its template view.
    answers: Mapping[str, Any] | None = None
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
    errors, or when a template cannot be assembled with ``answers``.
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
    assembled: list[Diagnostic] = []
    if options.answers is not None:
        source, assembled = _assemble(source, options.answers)
    document = _parse(source)
    result = validate(document, final=options.final)
    diagnostics = assembled + list(result.diagnostics)
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


def _assemble(source: str, answers: Mapping[str, Any]) -> tuple[str, list[Diagnostic]]:
    """*source* assembled with *answers* (§15.7), and the assembly's
    Warnings (such as ``answer-unknown``). Raises RenderRefused when it
    cannot be assembled: the template has Errors, for which §15.7.2 defines
    no output, or the answers do (``answer-invalid``, ``answer-missing``),
    or the template needs files a renderer below Full does not read
    (includes, LegalDown attachments, translations; §17.6)."""
    findings = validate(_parse(source)).diagnostics
    errors = sum(1 for d in findings if d.level == "error")
    if errors:
        raise RenderRefused(findings, f"Assembly refused: the template has {errors} error(s), "
                                      "and only a template without errors can be assembled (§15.7.2).")
    try:
        # No resolve=: a template that needs other files is refused (§17.6).
        result = parse_template(source).form(answers).assemble()
    except AssemblyError as error:
        raise DocumentError(f"The template cannot be assembled: {error}") from error
    if not result.ok:
        errors = [d for d in result.diagnostics if d.level == "error"]
        raise RenderRefused(result.diagnostics, f"Assembly refused: {len(errors)} error(s) in the answers or "
                                                "the template (§15.7).")
    # The source was normalized, and assembly keeps its line breaks.
    return result.output, list(result.diagnostics)


def _parse(source: str) -> Document:
    try:
        return parse(source)
    except (ValueError, yaml.YAMLError) as error:
        raise DocumentError(f"The document cannot be read: {error}") from error


def _resolve(document: Document, result: ValidationResult, style: Style) -> tuple[RenderTree, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    # Raw HTML is never emitted; the validator reports it (raw-html, §8.7).
    tree = build_tree(document, result)
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
