"""The HTML writer's stylesheet for print (§13.8): a page break goes before the
attachments container, not inside it, and only where the separator asks for
one."""
from __future__ import annotations

from conftest import run_cli_text

ATTACHED = (
    "---\ntitle: T\nattachments:\n"
    "  - id: one\n    title: First attachment\n    file: one.pdf\n"
    "  - id: two\n    title: Second attachment\n    file: two.pdf\n"
    "---\n\n# A {#a}\n\nBody.\n"
)


def test_a_standalone_page_break_render_has_the_attachment_break_rules() -> None:
    done = run_cli_text("-", "--set", "attachments.separator=page-break", stdin=ATTACHED)
    assert done.returncode == 0, done.stderr
    assert "<!DOCTYPE html>" in done.stdout
    assert 'class="ld-attachments ld-separator-page-break"' in done.stdout
    # The break goes before the container (heading included), then before each later attachment.
    assert ".ld-attachments.ld-separator-page-break { break-before: page; }" in done.stdout
    assert ".ld-separator-page-break .ld-attachment + .ld-attachment { break-before: page; }" in done.stdout
    assert ".ld-attachments-heading { break-after: avoid; }" in done.stdout
    # The old rule broke before every attachment, the first one included, inside the container.
    assert ".ld-separator-page-break .ld-attachment { break-before: page; }" not in done.stdout


def test_without_a_page_break_separator_the_container_is_not_broken() -> None:
    done = run_cli_text("-", "--set", "attachments.separator=none", stdin=ATTACHED)
    assert done.returncode == 0, done.stderr
    # The rules are scoped by the separator class, which no element carries here.
    assert 'class="ld-attachments ld-separator-none"' in done.stdout
    assert 'ld-separator-page-break"' not in done.stdout


# -- The stylesheet of a Style built by hand -------------------------------------

def test_the_stylesheet_strips_what_could_leave_its_declaration() -> None:
    """The loader refuses such values; ``stylesheet`` is the last line for a Style
    built by hand, which it never validated."""
    from legaldown_render.style import HeadingStyle, Html, Page, Style, Typography
    from legaldown_render.writers.html import stylesheet

    hostile = "red;}</style><script>x</script>@import 'x'; /* c */ \\ \x07\n\u2028}"
    style = Style(
        typography=Typography(font_family=hostile, font_size=hostile, line_height=hostile, color=hostile,
                              link_color=hostile),
        headings={1: HeadingStyle(size=hostile, weight=hostile)},
        page=Page(size=hostile, margin=hostile),
        html=Html(max_width=hostile),
    )
    css = stylesheet(style)
    generated = css.split(":root {", 1)[1]
    assert "<" not in generated
    assert "/*" not in generated and "*/" not in generated
    declarations = [line for line in css.split("\n") if line.startswith(("body {", ".ld-document {", "@page {"))]
    assert declarations
    for line in declarations:
        assert line.count("{") == 1
        assert line.count("}") == 1
        assert "\\" not in line
        assert "@" not in line.removeprefix("@page")


def test_comment_delimiters_removed_from_a_value_cannot_join_into_another() -> None:
    from legaldown_render.writers.html import _css

    assert "/*" not in _css("a//**/*b*/*/c") and "*/" not in _css("a//**/*b*/*/c")
    assert _css("a/*b*/c") == "abc"


def test_the_stylesheet_keeps_a_division_sign() -> None:
    from legaldown_render.style import Html, Style
    from legaldown_render.writers.html import stylesheet

    assert "max-width: calc(100% / 3);" in stylesheet(Style(html=Html(max_width="calc(100% / 3)")))
