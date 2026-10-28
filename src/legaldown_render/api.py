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

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePath
from typing import Any

import yaml
from legaldown import AssemblyError, Diagnostic, Document, ValidationResult, parse, parse_template, validate

from .build import build_tree, normalize_source
from .diagnostics import by_line, key_line, repeats
from .errors import DocumentError, OptionsError, RenderRefused, StyleError
from .resolve import Formatter, parse_locale, resolve_tree
from .resolve.docx_plan import plan_docx
from .style import Style, load_style
from .style.docx import DocxSettings, docx_settings
from .tree import RenderTree, assert_resolved
from .writers import FORMATS, DocxWriter, HtmlWriter, TextWriter


@dataclass(frozen=True, slots=True)
class RenderOptions:
    """Settings for one render job."""

    #: ``html``, ``text`` or ``docx``.
    format: str = "html"
    #: A built-in style name, a path to a YAML style template, a Style, or (see
    #: :func:`~.style.load_style`) a mapping or YAML text.
    style: str | Path | Style | Mapping[str, Any] | None = None
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
    #: would load it. It is used as given, not coerced (``Template.coerce``):
    #: the answers must already have the §15.7.1 shapes, e.g. a money amount
    #: is a string in §10.3 format such as ``"5000.00"``, not a number.
    #: With answers, a template is assembled first and the assembled document
    #: is rendered (§15.8); without, a template renders as its template view.
    answers: Mapping[str, Any] | None = None
    #: HTML only: a complete page (True) or just the ``<article>`` (False).
    standalone: bool = True
    #: The document's file name, which every diagnostic of the result names as
    #: its ``file`` (§16.9), the validator's and the renderer's alike, as
    #: ``Document.filename`` does for the validator: the name without its
    #: directory (a directory given is dropped, whether it is written with ``/``
    #: or ``\``). ``render_file`` and the command line set it from the path
    #: they read; for text given to :func:`render` it is empty unless set.
    #: With ``answers``, the lines of the diagnostics are those of the
    #: assembled document (§15.8), not of the template file.
    filename: str = ""
    #: The document root (§2.3): a boundary directory that no file reference of
    #: the document (an ``{{include:}}``, ``attachments[].file``,
    #: ``amends.file``, ``supersedes.file``, a ``translations`` value, an image)
    #: may leave. A path that does is reported as ``path-outside-root``, an
    #: Error, and never used. None (the default) configures no root, as §2.3
    #: defines none: ``../`` is then valid, only a path that is not relative is
    #: reported. A renderer for documents it does not control SHOULD set one
    #: (§2.3). The check is lexical: nothing is read or resolved, and a symbolic
    #: link is not followed.
    document_root: str | Path | None = None
    #: Where the document is, to say where in the root it sits: the base of a
    #: path is the document's directory. Used only with ``document_root``,
    #: and without it the document is at the root, as text given to :func:`render`
    #: is. ``render_file`` and the command line set it from the path they read,
    #: and it names the file of the diagnostics when ``filename`` is empty. A
    #: document outside the root is an :class:`OptionsError`.
    document_path: str | Path | None = None
    #: The document and the style are somebody else's, as for a service that
    #: renders what its users upload. The renderer then requires a
    #: ``document_root`` (§2.3: an implementation that processes documents it
    #: does not control SHOULD set one; :class:`OptionsError` without), and
    #: loads the style with ``trusted=False`` (:func:`~.style.load_style`): no
    #: style file, ``extends`` only built-in styles, strict values, no raw CSS.
    #: False (the default) takes the style as the operator's own.
    untrusted: bool = False


@dataclass(frozen=True, slots=True)
class RenderResult:
    """What a render produced."""

    #: The rendered text: HTML or plain text. Empty for ``docx``, which is bytes
    #: (:attr:`binary`), not text.
    output: str
    #: Core (validator) and Rendering diagnostics, each with its stable rule id.
    diagnostics: list[Diagnostic]
    format: str
    #: The resolved tree, for tools that want more than the output text.
    tree: RenderTree
    style: Style
    #: The rendered file for a binary format (``docx``): the bytes of the whole
    #: file. None for ``html`` and ``text``.
    binary: bytes | None = None

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
    style or setting, :class:`OptionsError` for an unknown output format, an
    ``untrusted`` render without a ``document_root``, or a
    document outside its document root, :class:`DocumentError` for a document that cannot be
    read, and :class:`RenderRefused` in strict mode when the document has
    errors, or when a template cannot be assembled with ``answers``.
    """
    if options is None:
        options = RenderOptions(**settings)
    elif settings:
        raise TypeError("pass either a RenderOptions or keyword settings, not both")
    if options.format not in FORMATS:
        raise OptionsError(f"unknown output format '{options.format}' (known: {', '.join(FORMATS)})")
    if options.untrusted and options.document_root is None:
        raise OptionsError("an untrusted render needs a document_root (§2.3): name the directory that file "
                           "references may not leave")
    base = _document_base(options)
    name = options.filename.replace("\\", "/").rsplit("/", 1)[-1]
    if name != options.filename:
        options = replace(options, filename=name)
    if options.document_path is not None and not options.filename:
        options = replace(options, filename=PurePath(options.document_path).name)
    overrides = dict(options.overrides)
    if options.locale:
        overrides["locale"] = options.locale
    style = load_style(options.style, overrides=overrides, trusted=not options.untrusted)
    # A setting Word cannot express is a StyleError before any rendering starts (docs/decisions/0006).
    docx = docx_settings(style, trusted=not options.untrusted) if options.format == "docx" else None

    source = normalize_source(source)
    assembled: list[Diagnostic] = []
    if options.answers is not None:
        source, assembled = _assemble(source, options.answers, options.filename)
    document = _parse(source, options.filename)
    result = validate(document, final=options.final)
    diagnostics = assembled + list(result.diagnostics)
    if options.strict and any(d.level == "error" for d in diagnostics):
        raise RenderRefused(diagnostics)

    tree, extra = _resolve(document, result, style, base, options.untrusted)
    assert_resolved(tree)
    writer = _writer(options, style, docx)
    plan = None
    if isinstance(writer, DocxWriter):
        # What Word will draw differently from the other formats (docs/decisions/0008) is the planner's to report; its
        # findings are the renderer's, and are ordered with them.
        plan, planned = plan_docx(tree, file=document.filename, text_switch=writer.settings.reference_text_switch)
        extra = [*extra, *planned]
    # The renderer's findings that the validator's or the assembly's already
    # name (the same rule, level and message), wherever they are, are not repeated;
    # the rest follow in the order of their lines.
    diagnostics += [d for d in by_line(extra) if not repeats(d, diagnostics)]
    if options.strict and any(d.level == "error" for d in diagnostics):
        raise RenderRefused(diagnostics)

    if plan is not None:
        written: str | bytes = writer.write(tree, plan)
    else:
        written = writer.write(tree)
    if isinstance(written, bytes):
        return RenderResult("", diagnostics, options.format, tree, style, binary=written)
    return RenderResult(written, diagnostics, options.format, tree, style)


def render_file(path: str | Path, options: RenderOptions | None = None, /, **settings: Any) -> RenderResult:
    """Render the LegalDown file at *path* (see :func:`render`). Raises
    :class:`DocumentError` when the file is not valid UTF-8, as for any
    document that cannot be read at all; an unreadable file raises
    :class:`OSError`."""
    data = Path(path).read_bytes()
    try:
        source = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DocumentError(f"The document cannot be read: {path} is not valid UTF-8 "
                            f"(byte 0x{data[error.start]:02x} at offset {error.start})") from None
    if options is None:
        options = RenderOptions(**settings)
        settings = {}
    if not options.filename and not settings:
        options = replace(options, filename=Path(path).name)
    if options.document_path is None and not settings:
        options = replace(options, document_path=path)
    return render(source, options, **settings)


def _document_base(options: RenderOptions) -> str | None:
    """The directory of the document relative to the document root, ``/``-separated
    (``"."`` at the root), or None when no root is configured (§2.3). Lexical:
    the paths are made absolute against the working directory and compared as
    text, with no file system access. Raises OptionsError for an empty root or
    path, and for a document that is not inside the root (or, on Windows, is on
    another drive)."""
    if options.document_root is None:
        return None
    if os.fspath(options.document_root) == "":
        raise OptionsError("document_root is empty: name the directory file references may not leave")
    if options.document_path is not None and os.fspath(options.document_path) == "":
        raise OptionsError("document_path is empty: name the document's file, or leave it out")
    root = os.path.abspath(options.document_root)
    directory = root if options.document_path is None else os.path.dirname(os.path.abspath(options.document_path))
    try:
        relative = os.path.relpath(directory, root)
    except ValueError:  # Windows: another drive
        raise OptionsError(f"the document {options.document_path} is outside the document root "
                           f"{options.document_root}: it is on another drive") from None
    parts = PurePath(relative).parts
    if parts and parts[0] == "..":
        raise OptionsError(f"the document {options.document_path} is outside the document root "
                           f"{options.document_root}: the root must contain the document")
    return "/".join(parts) or "."


def _assemble(source: str, answers: Mapping[str, Any], filename: str = "") -> tuple[str, list[Diagnostic]]:
    """*source* assembled with *answers* (§15.7), and the assembly's
    Warnings (such as ``answer-unknown``). Raises RenderRefused when it
    cannot be assembled: the template has Errors, for which §15.7.2 defines
    no output, or the answers do (``answer-invalid``, ``answer-missing``),
    or the template needs files a renderer below Full does not read
    (includes, LegalDown attachments, translations; §17.6)."""
    findings = _named(validate(_parse(source, filename)).diagnostics, filename)
    errors = sum(1 for d in findings if d.level == "error")
    if errors:
        raise RenderRefused(findings, f"Assembly refused: the template has {errors} error(s), "
                                      "and only a template without errors can be assembled (§15.7.2).")
    try:
        # No resolve=: a template that needs other files is refused (§17.6).
        result = parse_template(source).form(answers).assemble()
    except AssemblyError as error:
        raise DocumentError(f"The template cannot be assembled: {error}") from error
    diagnostics = _named(result.diagnostics, filename)
    if not result.ok:
        errors = [d for d in diagnostics if d.level == "error"]
        raise RenderRefused(diagnostics, f"Assembly refused: {len(errors)} error(s) in the answers or "
                                         "the template (§15.7).")
    # The source was normalized, and assembly keeps its line breaks.
    return result.output, diagnostics


def _named(diagnostics: Iterable[Diagnostic], filename: str) -> list[Diagnostic]:
    """*diagnostics* with *filename* as the ``file`` of each one that names no
    file (§16.9): the assembly's findings are the document's, and the
    assembler does not know the file it was given."""
    return [replace(d, file=filename) if not d.file else d for d in diagnostics]


def _parse(source: str, filename: str = "") -> Document:
    try:
        return parse(source, filename=filename)
    except (ValueError, yaml.YAMLError) as error:
        raise DocumentError(f"The document cannot be read: {error}") from error


def _resolve(document: Document, result: ValidationResult, style: Style,
             base: str | None, untrusted: bool = False) -> tuple[RenderTree, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    # Raw HTML is never emitted; the validator reports it (raw-html, §8.7).
    tree = build_tree(document, result)
    locale = parse_locale(style.locale) if style.locale else None
    if style.locale and locale is None:
        shown = "" if untrusted else f" '{style.locale}'"
        raise StyleError("setting", [f"locale:{shown} is not a known locale (e.g. en-US, cs-CZ)"])
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
                # Document-level: at the frontmatter's `language` key.
                line=key_line(document, "language"),
                file=document.filename,
            ))
            locale = parse_locale("en")
    assert locale is not None
    resolved, found = resolve_tree(tree, document, result, style, Formatter(locale, style.values), base)
    return resolved, diagnostics + found


def _writer(options: RenderOptions, style: Style, docx: DocxSettings | None) -> HtmlWriter | TextWriter | DocxWriter:
    if options.format == "text":
        return TextWriter(style.attachments.separator)
    from . import __version__

    generator = f"legaldown-render {__version__}"
    if options.format == "docx":
        assert docx is not None
        return DocxWriter(style, generator=generator, settings=docx)
    return HtmlWriter(style, standalone=options.standalone, generator=generator)
