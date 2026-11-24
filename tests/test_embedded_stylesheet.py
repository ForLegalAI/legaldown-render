"""The embedded stylesheet (#18): ``stylesheet(style, standalone=False)`` is the
CSS for ``HtmlWriter(standalone=False)`` output placed in a host page. It
styles nothing outside the ``<article class="ld-document">``, and its rules
override each other as in the standalone sheet.

The tests read the sheet with a splitter that knows only the shapes the sheet
writes (one rule per line, ``@media`` blocks of such rules). A shape it does
not know fails the test, so a later rule cannot slip past the checks."""
from __future__ import annotations

import re

import pytest
from conftest import GOLDEN

from legaldown_render import OptionsError
from legaldown_render.style import HeadingStyle, Html, Style, Typography, builtin_styles, load_style
from legaldown_render.writers import stylesheet
from legaldown_render.writers.html import check_scope


def _every_branch() -> Style:
    """A style that turns on every optional part of the sheet."""
    return load_style("default", overrides={
        "typography.justify": True, "contents.enabled": True, "attachments.separator": "rule",
        "headings.1.transform": "uppercase", "headings.2.transform": "small-caps", "headings.3.italic": True,
    })


STYLES = [load_style(name) for name in builtin_styles()] + [_every_branch()]
IDS = [*builtin_styles(), "every-branch"]


# -- Reading the sheet -------------------------------------------------------------

_RULE = re.compile(r"(?P<selectors>[^{}]+?) \{ (?P<declarations>[^{}]*) \}")


def rules(css: str) -> list[tuple[str, tuple[str, ...], str]]:
    """The rules of *css* in order: (at-rule or "", selectors, declarations)."""
    found: list[tuple[str, tuple[str, ...], str]] = []
    block = ""
    for line in css.splitlines():
        if not line.strip():
            continue
        if line.startswith("@media ") and line.endswith(" {"):
            assert not block, f"nested block: {line!r}"
            block = line[:-2]
            continue
        if line == "}":
            assert block, "a closing brace outside a block"
            block = ""
            continue
        if line.startswith("@page "):
            found.append(("@page", (), line))
            continue
        match = _RULE.fullmatch(line.strip())
        assert match, f"a line the splitter does not know: {line!r}"
        selectors = tuple(part.strip() for part in match["selectors"].split(", "))
        found.append((block, selectors, match["declarations"]))
    assert not block, "an unclosed block"
    return found


def specificity(selector: str) -> tuple[int, int, int]:
    """(ids, classes and pseudo-classes, types) of one selector of the shapes
    this sheet writes; ``:where(...)`` counts nothing."""
    selector = re.sub(r":where\([^)]*\)", "", selector)
    ids = len(re.findall(r"#[\w-]+", selector))
    classes = len(re.findall(r"\.[\w-]+", selector)) + len(re.findall(r":[\w-]+", selector))
    types = len(re.findall(r"(?:^|[\s>+~])([A-Za-z][\w-]*)", selector))
    return ids, classes, types


PAGE_SELECTORS = {":root", "body", "html"}


# -- Standalone is unchanged -------------------------------------------------------

@pytest.mark.parametrize("standalone", [True, False], ids=["standalone", "embedded"])
def test_golden_stylesheet(standalone: bool, update_golden: bool) -> None:
    """Both sheets of a style that writes every optional rule, byte for byte: the
    HTML goldens hold only the default style's sheet. Regenerate with
    ``pytest --update-golden`` and review the diff."""
    css = stylesheet(_every_branch(), standalone=standalone)
    golden = GOLDEN / f"stylesheet.every-branch.{'standalone' if standalone else 'embedded'}.css"
    if update_golden or not golden.exists():
        golden.write_text(css, encoding="utf-8", newline="\n")
        if not update_golden:
            pytest.fail(f"{golden.name} did not exist and was written; review it and run again")
    assert css == golden.read_bytes().decode("utf-8")


@pytest.mark.parametrize("style", STYLES, ids=IDS)
def test_the_default_is_the_standalone_sheet(style: Style) -> None:
    assert stylesheet(style) == stylesheet(style, standalone=True)
    css = stylesheet(style)
    assert ":root {" in css and "body {" in css and "@page {" in css


# -- Nothing outside the document --------------------------------------------------

@pytest.mark.parametrize("scope", ["", ".preview", "main #doc-2 > .pane"])
@pytest.mark.parametrize("style", STYLES, ids=IDS)
def test_every_embedded_rule_is_under_the_document(style: Style, scope: str) -> None:
    root = f":where({scope}) .ld-document" if scope else ".ld-document"
    found = rules(stylesheet(style, standalone=False, scope=scope))
    assert found
    for block, selectors, _ in found:
        assert block in ("", "@media print"), block
        for selector in selectors:
            assert selector == root or selector.startswith(root + " "), selector


@pytest.mark.parametrize("style", STYLES, ids=IDS)
def test_the_embedded_sheet_has_no_page_rules(style: Style) -> None:
    css = stylesheet(style, standalone=False)
    assert "@page" not in css
    for _, selectors, _ in rules(css):
        for selector in selectors:
            assert not any(part in PAGE_SELECTORS for part in re.split(r"[\s>+~]+", selector)), selector


# -- The same cascade --------------------------------------------------------------

@pytest.mark.parametrize("scope", ["", ".preview"])
@pytest.mark.parametrize("style", STYLES, ids=IDS)
def test_the_embedded_rules_override_each_other_as_the_standalone_ones(style: Style, scope: str) -> None:
    """Every rule but the document's own and the page's is the standalone rule
    under the root, in the same order, with the same declarations, and every
    pair of selectors keeps the order of their specificities. (Placing only
    some selectors under the document would make ``.ld-document dfn`` beat
    ``.ld-style-italic`` and turn an italic defined term upright.)"""
    root = f":where({scope}) .ld-document" if scope else ".ld-document"

    def body(found):
        return [(block, selectors, declarations) for block, selectors, declarations in found
                if block != "@page" and selectors != (".ld-document",) and selectors != (root,)
                and not set(selectors) & PAGE_SELECTORS]

    standalone = body(rules(stylesheet(style)))
    embedded = body(rules(stylesheet(style, standalone=False, scope=scope)))
    # The same rules in the same order, each selector the standalone one under the
    # root: every selector gains the same specificity, so no pair changes order.
    assert [(b, tuple(f"{root} {s}" for s in sel), d) for b, sel, d in standalone] == embedded


@pytest.mark.parametrize("standalone", [True, False])
def test_an_italic_defined_term_stays_italic(standalone: bool) -> None:
    """``<dfn class="ld-defined ld-style-italic">``: the class beats the element rule."""
    found = {selector: (position, declarations)
             for position, (_, selectors, declarations) in enumerate(rules(stylesheet(Style(), standalone=standalone)))
             for selector in selectors}
    dfn = next(s for s in found if s.split()[-1] == "dfn")
    italic = next(s for s in found if s.split()[-1] == ".ld-style-italic")
    assert found[dfn][1] == "font-style: normal;" and found[italic][1] == "font-style: italic;"
    assert (specificity(italic), found[italic][0]) > (specificity(dfn), found[dfn][0])


def test_the_specificity_counter() -> None:
    assert specificity("a") == (0, 0, 1)
    assert specificity(".ld-document a") == (0, 1, 1)
    assert specificity(".ld-ref:hover") == (0, 2, 0)
    assert specificity(":where(main #x) .ld-document .ld-p > .ld-number") == (0, 3, 0)
    assert specificity(".ld-enumerated > li > .ld-label") == (0, 2, 1)


# -- What the document's own rule holds ---------------------------------------------

def _root_rule(css: str, root: str = ".ld-document") -> str:
    found = [declarations for block, selectors, declarations in rules(css) if block == "" and selectors == (root,)]
    assert len(found) == 1, found
    return found[0]


def test_the_variables_and_the_typography_are_on_the_document() -> None:
    style = Style(typography=Typography(font_family="Inter, sans-serif", font_size="10pt", line_height="1.4",
                                        color="#222", link_color="#0a0"), html=Html(max_width="40rem"))
    root = _root_rule(stylesheet(style, standalone=False))
    for declaration in ("--ld-rule: #c8c8c8;", "--ld-subtle: #f5f5f5;", "--ld-muted: #666;", "--ld-link: #0a0;",
                        "font-family: Inter, sans-serif;", "font-size: 10pt;", "line-height: 1.4;", "color: #222;",
                        "background: #fff;", "margin: 0 auto;", "max-width: 40rem;"):
        assert declaration in root
    # A page gutter is the host's: no padding on the document.
    assert "padding" not in root


def test_the_print_width_comes_after_the_document_rule() -> None:
    css = stylesheet(Style(), standalone=False)
    assert css.index(".ld-document { --ld-rule") < css.index("  .ld-document { max-width: none;")


def test_a_scoped_sheet_keeps_the_specificity_of_an_unscoped_one() -> None:
    plain = rules(stylesheet(Style(), standalone=False))
    scoped = rules(stylesheet(Style(), standalone=False, scope="main #doc-2"))
    assert len(plain) == len(scoped)
    for (_, a, _), (_, b, _) in zip(plain, scoped, strict=True):
        assert [specificity(s) for s in a] == [specificity(s) for s in b]
    assert _root_rule(stylesheet(Style(), standalone=False, scope="main #doc-2"), ":where(main #doc-2) .ld-document")


# -- extra_css --------------------------------------------------------------------

def test_extra_css_is_appended_as_written() -> None:
    """The style author's own CSS: not scoped (an untrusted style may not set it)."""
    style = Style(html=Html(extra_css="a { color: red }\n.ld-document .ld-p { color: blue }\n"))
    css = stylesheet(style, standalone=False)
    assert css.endswith("a { color: red }\n.ld-document .ld-p { color: blue }\n")
    assert "<\\/b>" in stylesheet(Style(html=Html(extra_css="x::before { content: '</b>' }")), standalone=False)


def test_values_cannot_leave_their_declaration_in_the_embedded_sheet() -> None:
    hostile = "red;}</style><script>x</script>@import 'x'; /* c */ \\ \x07\n }"
    style = Style(
        typography=Typography(font_family=hostile, font_size=hostile, line_height=hostile, color=hostile,
                              link_color=hostile),
        headings={1: HeadingStyle(size=hostile, weight=hostile, align=hostile),  # type: ignore[arg-type]
                  "1, body": HeadingStyle()},  # type: ignore[dict-item]
        html=Html(max_width=hostile),
    )
    css = stylesheet(style, standalone=False)
    assert "<" not in css and "@import" not in css and "/*" not in css and "\\" not in css
    assert "body" not in css
    for _, selectors, _ in rules(css):
        assert all(selector.startswith(".ld-document") for selector in selectors)


# -- The scope ---------------------------------------------------------------------

@pytest.mark.parametrize("scope", [".preview", "#doc-42", "div.a > .b", "main .x", "main #doc-2 > .pane", ".a.b#c",
                                   "section", "._x"])
def test_usable_scopes(scope: str) -> None:
    assert check_scope(scope) is None
    assert stylesheet(Style(), standalone=False, scope=scope).startswith(f":where({scope}) .ld-document {{")


@pytest.mark.parametrize("scope", ["a, b", ":not(", '[a="', ".a >", "> .a", ".a  .b", "x{", "x;", "/*", "</style",
                                   "@media", ".a\n.b", " .a", ".a ", "#1", ".1a", ".a:hover", "*", ".a~.b", "é", "_x", ".a\n", ".a\t"])
def test_unusable_scopes_are_refused(scope: str) -> None:
    assert check_scope(scope) is not None
    with pytest.raises(OptionsError, match="scope"):
        stylesheet(Style(), standalone=False, scope=scope)


def test_a_scope_that_is_no_string_is_refused() -> None:
    assert check_scope(3) == "the stylesheet scope must be a string, not int"
    with pytest.raises(OptionsError, match="not int"):
        stylesheet(Style(), standalone=False, scope=3)  # type: ignore[arg-type]


def test_a_scope_is_for_the_embedded_sheet_only() -> None:
    with pytest.raises(OptionsError, match="standalone=False"):
        stylesheet(Style(), scope=".preview")
    assert stylesheet(Style(), scope="") == stylesheet(Style())


def test_standalone_must_be_a_boolean() -> None:
    with pytest.raises(OptionsError, match="standalone"):
        stylesheet(Style(), standalone="no")  # type: ignore[arg-type]


def test_options_error_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        stylesheet(Style(), standalone=False, scope="a, b")
