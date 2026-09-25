"""The ``legaldown-render`` command.

Exit codes: 0 — rendered (document errors, if any, are shown as markers and
listed on stderr); 1 — not rendered: refused under ``--strict``, or the
document cannot be read; 2 — a usage or style problem; 70 — an internal
error (a bug to report).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .api import RenderOptions, render
from .errors import DocumentError, InternalError, RenderRefused
from .style import StyleError, builtin_styles, dump_style, load_style, parse_override
from .validator_bridge import read_answers
from .writers import FORMATS, format_for_path


def _setting(text: str) -> tuple[str, Any]:
    key, separator, value = text.partition("=")
    if not separator or not key.strip():
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got '{text}'")
    # Typed by the setting: text stays as written, anything else is YAML.
    return key.strip(), parse_override(key.strip(), value)


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
    parser.add_argument("-o", "--output", help="output file (default: standard output); its extension sets the format")
    parser.add_argument("-f", "--format", choices=sorted(FORMATS), help="output format (default: from --output, else html)")
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


def main(argv: list[str] | None = None) -> int:
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

    output_format = args.format or (format_for_path(args.output) if args.output else None) or "html"
    name = "<stdin>" if args.input == "-" else args.input
    try:
        source = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
    except OSError as error:
        print(f"legaldown-render: cannot read {name}: {error.strerror or error}", file=sys.stderr)
        return 1

    answers = None
    if args.answers:
        # Read as legaldown-validator's `legaldown assemble` reads it (§15.7.1).
        answers, failure = read_answers(Path(args.answers))
        if failure:
            print(f"legaldown-render: {failure}", file=sys.stderr)
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
        Path(args.output).write_text(result.output, encoding="utf-8")
    else:
        sys.stdout.write(result.output)
    return 0


def _report(name: str, diagnostics: list, *, quiet: bool) -> None:
    if quiet:
        return
    for diagnostic in diagnostics:
        print(f"{name}: {diagnostic.level}: [{diagnostic.rule}] {diagnostic.message}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
