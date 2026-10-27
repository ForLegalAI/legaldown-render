"""The ``legaldown-render`` command.

Exit codes: 0 — rendered (document errors, if any, are shown as markers and
listed on stderr); 1 — not rendered: refused under ``--strict``, or the
document cannot be read (including input that is not UTF-8) or the output cannot
be written; 2 — a usage or style problem; 70 — an internal error (a bug to
report).

Documents and output are always UTF-8 (the HTML says ``<meta charset="utf-8">``),
never the console's or the locale's encoding.
"""
from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path
from typing import Any

from legaldown import AnswersError, load_answers

from . import __version__
from .api import RenderOptions, render
from .errors import DocumentError, InternalError, RenderRefused, StyleError
from .style import builtin_styles, dump_style, load_style, parse_override
from .writers import FORMATS, format_for_path

#: Extensions of document formats this renderer does not write. An output file
#: named so (``x.docx``) and no ``--format`` is a mistake to refuse: HTML under
#: that name would mislead. Any other extension (``contract.v2``,
#: ``report.2025-10-27``), or none, is just a name and keeps the HTML default.
_UNWRITTEN_EXTENSIONS = frozenset({".docx", ".doc", ".odt", ".rtf", ".pdf", ".md", ".markdown", ".epub", ".tex"})


def _setting(text: str) -> tuple[str, Any]:
    key, separator, value = text.partition("=")
    if not separator or not key.strip():
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got '{text}'")
    # Typed by the setting: text stays as written, anything else is YAML.
    try:
        return key.strip(), parse_override(key.strip(), value)
    except StyleError as error:
        # argparse shows only ArgumentTypeError (and TypeError/ValueError, with a generic message).
        raise argparse.ArgumentTypeError(str(error)) from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="legaldown-render",
        description="Render a LegalDown document to HTML or plain text.",
        epilog="Style settings: --style picks a template (built-in: "
               + ", ".join(builtin_styles())
               + ", or a .yaml file); --set overrides one setting, e.g. --set numbering.scheme=mixed. "
               "See every setting with --print-style.",
    )
    parser.add_argument("input", nargs="?", help="the .lgd file to render, or - for standard input")
    parser.add_argument("-o", "--output", help="output file (default: standard output). Without --format, "
                        ".html/.htm write HTML and .txt/.text plain text; any other name, with or without an "
                        "extension, writes HTML, except the extension of a format not written (.docx, .doc, "
                        ".odt, .rtf, .pdf, .md, .markdown, .epub, .tex), which needs --format")
    parser.add_argument("-f", "--format", choices=sorted(FORMATS),
                        help="output format (default: html, or text for --output *.txt/*.text)")
    parser.add_argument("-s", "--style", help="style template: a built-in name or a path to a .yaml file")
    parser.add_argument("--set", dest="overrides", action="append", default=[], type=_setting, metavar="KEY=VALUE",
                        help="override one style setting; repeatable. Text settings take the value as "
                             "written; one pair of matching outer quotes is removed (wrap the value in the "
                             "other kind of quote to keep quotation marks). Others read it as YAML, e.g. true or 2")
    parser.add_argument("--locale", help="formatting locale, e.g. en-US or cs-CZ (default: the style's, else the document language)")
    parser.add_argument("--fragment", action="store_true", help="HTML: write only the <article>, without page and stylesheet")
    parser.add_argument("--strict", action="store_true", help="refuse to render a document that has errors")
    parser.add_argument("--answers", metavar="FILE",
                        help="a YAML or JSON answers set (§15.7.1): the template is assembled with it and the "
                             "assembled document rendered")
    parser.add_argument("--final", action="store_true",
                        help="the document is meant for signature: a remaining blank, questions key, condition, "
                             "choice, or drafting note is an error (§15.9); with --strict, it is refused")
    parser.add_argument("-q", "--quiet", action="store_true", help="do not list diagnostics")
    parser.add_argument("--print-style", action="store_true",
                        help="print the effective style — the one --style, --set and --locale produce — as YAML, and exit")
    parser.add_argument("--list-styles", action="store_true", help="list the built-in styles, and exit")
    parser.add_argument("--version", action="version", version=f"legaldown-render {__version__}")
    return parser


def _configure_streams() -> None:
    """Make the standard streams safe for any content, whatever the console's
    encoding: the document and the style are UTF-8, so what is written to
    standard output is UTF-8 as well, and a diagnostic that quotes characters
    the console cannot show escapes them (``\\u2192``) instead of failing with a
    UnicodeEncodeError. Replaced streams without ``reconfigure`` (a test
    capture, a StringIO) are left alone."""
    if hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(ValueError, OSError):
            sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        with contextlib.suppress(ValueError, OSError):
            sys.stderr.reconfigure(errors="backslashreplace")


def _read_source(path: str) -> str:
    """The document at *path*, or standard input for ``-``, decoded as UTF-8
    from its bytes (never in the locale's encoding). Raises OSError, or
    UnicodeDecodeError for input that is not UTF-8."""
    if path == "-":
        buffer = getattr(sys.stdin, "buffer", None)
        # A replaced stdin without bytes (StringIO) already holds text.
        return buffer.read().decode("utf-8") if buffer is not None else sys.stdin.read()
    return Path(path).read_bytes().decode("utf-8")


def main(argv: list[str] | None = None) -> int:
    _configure_streams()
    parser = _parser()
    args = parser.parse_args(argv)
    overrides = dict(args.overrides)
    if args.locale:
        overrides["locale"] = args.locale

    if args.list_styles:
        print("\n".join(builtin_styles()))
        return 0
    if args.print_style:
        try:
            print(dump_style(load_style(args.style, overrides=overrides)), end="")
        except StyleError as error:
            print(f"legaldown-render: {error}", file=sys.stderr)
            return 2
        return 0
    if not args.input:
        parser.error("an input file is required")

    output_format = args.format
    if not output_format and args.output:
        file_name = Path(args.output).name
        output_format = format_for_path(file_name)
        if not output_format and Path(file_name).suffix.lower() in _UNWRITTEN_EXTENSIONS:
            parser.error(f"cannot tell the output format from '{args.output}'; use --format html or text")
    output_format = output_format or "html"
    name = "<stdin>" if args.input == "-" else args.input
    try:
        source = _read_source(args.input)
    except OSError as error:
        print(f"legaldown-render: cannot read {name}: {error.strerror or error}", file=sys.stderr)
        return 1
    except UnicodeDecodeError as error:
        print(f"legaldown-render: cannot read {name}: not valid UTF-8 "
              f"(byte 0x{error.object[error.start]:02x} at offset {error.start})", file=sys.stderr)
        return 1

    answers = None
    if args.answers:
        # Read by load_answers (§15.7.1). Unlike ``legaldown assemble``, the answers are not
        # coerced (Template.coerce): they must already have the §15.7.1 shapes.
        try:
            answers = load_answers(args.answers)
        except OSError as error:
            print(f"legaldown-render: cannot read {Path(args.answers)}: {error}", file=sys.stderr)
            return 1
        except AnswersError as error:
            print(f"legaldown-render: {error}", file=sys.stderr)
            return 1

    options = RenderOptions(format=output_format, style=args.style, overrides=overrides,
                            strict=args.strict, final=args.final, answers=answers, standalone=not args.fragment)
    try:
        result = render(source, options)
    except StyleError as error:
        print(f"legaldown-render: {error}", file=sys.stderr)
        return 2
    except RenderRefused as error:
        _report(name, error.diagnostics, quiet=args.quiet)
        print(f"legaldown-render: {error}", file=sys.stderr)
        return 1
    except DocumentError as error:
        print(f"{name}: error: {error}", file=sys.stderr)
        return 1
    except InternalError as error:
        print(f"legaldown-render: internal error: {error}", file=sys.stderr)
        return 70

    _report(name, result.diagnostics, quiet=args.quiet)
    if args.output:
        try:
            Path(args.output).write_text(result.output, encoding="utf-8")
        except OSError as error:
            print(f"legaldown-render: cannot write {args.output}: {error.strerror or error}", file=sys.stderr)
            return 1
    else:
        sys.stdout.write(result.output)
    return 0


def _report(name: str, diagnostics: list, *, quiet: bool) -> None:
    if quiet:
        return
    for diagnostic in diagnostics:
        # As the validator's CLI prints them: the line when the finding has one.
        where = f"{name}:{diagnostic.line}" if diagnostic.line else name
        print(f"{where}: {diagnostic.level}: [{diagnostic.rule}] {diagnostic.message}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
