"""Links and images (WP3): which image paths are shown (B6, §2.3), which links
are links (W3, §8.7), and links with no URL or no text (B9/W8)."""
from __future__ import annotations

import pytest

from legaldown_render import RenderRefused, render
from legaldown_render.writers.html import is_safe_href

FRONT = "---\ntitle: T\n---\n"


def text(body: str, **settings: object) -> str:
    return render(FRONT + body, format="text", **settings).output


def html(body: str, **settings: object) -> str:
    return render(FRONT + body, format="html", **settings).output


# -- B6: image paths (§2.3) ---------------------------------------------------

NON_RELATIVE = [
    "https://example.com/p.png",
    "HTTP://example.com/p.png",
    "ftp://example.com/p.png",
    "file:///etc/passwd",
    "data:image/png;base64,AAAA",
    "data:text/html,x",
    "javascript:alert(1)",
    "tel:123",
    "/etc/passwd",
    "/abs/p.png",
    "//host/p.png",
    "\\\\host\\share\\p.png",
    "\\abs.png",
    "C:\\x\\p.png",
    "C:/x/p.png",
    "c:p.png",
]


@pytest.mark.parametrize("path", NON_RELATIVE)
def test_a_non_relative_image_shows_its_alt_text(path: str) -> None:
    source = f"# A\n\nBefore ![the logo](<{path}>) after.\n"
    for fmt in ("html", "text"):
        result = render(FRONT + source, format=fmt)
        assert "<img" not in result.output
        assert "Before the logo after." in result.output
        errors = [d for d in result.diagnostics if d.rule == "path-not-relative"]
        assert len(errors) == 1, result.diagnostics
        assert errors[0].level == "error"
        # Markdown reads "\\" in the source as one backslash.
        assert path.replace("\\\\", "\\") in errors[0].message
        assert not result.ok


def test_a_data_image_is_not_embedded() -> None:
    result = render(FRONT + "# A\n\n![x](data:image/png;base64,iVBORw0KGgo=)\n", format="html")
    assert "data:" not in result.output
    assert "<img" not in result.output


@pytest.mark.parametrize("path", ["img/p.png", "../p.png", "./p.png", "../../shared/p.png", "p.png", "a/b:c.png"])
def test_a_relative_image_is_an_image(path: str) -> None:
    result = render(FRONT + f"# A\n\n![alt](<{path}> \"cap\")\n", format="html")
    assert f'<img src="{path}" alt="alt" title="cap">' in result.output
    assert not [d for d in result.diagnostics if d.rule == "path-not-relative"]
    assert result.ok


def test_a_disguised_scheme_is_still_a_scheme() -> None:
    # WHATWG ignores tabs and newlines, and a percent-encoded backslash is a backslash.
    for path in ("ht\ttps://example.com/p.png", "%5Chost%5Cp.png", "%5C%5Chost%5Cp.png"):
        result = render(FRONT + f"# A\n\n![alt](<{path}>)\n", format="html")
        assert "<img" not in result.output, path
        assert [d.rule for d in result.diagnostics if d.rule == "path-not-relative"], path


def test_alt_text_keeps_its_formatting_and_directives() -> None:
    result = render(FRONT + "# A {#a}\n\nSee ![logo of **Acme** ({{ref: a}})](https://x/p.png).\n", format="html")
    assert "<img" not in result.output
    assert "logo of <strong>Acme</strong> (" in result.output


def test_an_image_inside_a_link_keeps_the_link() -> None:
    result = render(FRONT + "# A\n\n[![logo](https://x/p.png)](https://example.com)\n", format="html")
    assert '<a href="https://example.com">logo</a>' in result.output


def test_the_same_image_path_is_reported_once() -> None:
    result = render(FRONT + "# A\n\n![a](https://x/p.png) ![b](https://x/p.png) ![c](/q.png)\n")
    errors = [d for d in result.diagnostics if d.rule == "path-not-relative"]
    assert len(errors) == 2  # the same path twice is one finding


def test_strict_refuses_a_non_relative_image() -> None:
    with pytest.raises(RenderRefused) as refused:
        render(FRONT + "# A\n\n![a](https://x/p.png)\n", strict=True)
    assert "path-not-relative" in {d.rule for d in refused.value.diagnostics}
    render(FRONT + "# A\n\n![a](img/p.png)\n", strict=True)


def test_the_validators_own_report_is_not_repeated(monkeypatch: pytest.MonkeyPatch) -> None:
    from legaldown import Diagnostic
    from legaldown import validate as real_validate

    import legaldown_render.api as api

    def validate(document, **kwargs):  # type: ignore[no-untyped-def]
        result = real_validate(document, **kwargs)
        found = Diagnostic(rule="path-not-relative", level="error", line=5,
                           message="Image path 'https://x/p.png' is not relative (§2.3).")
        result.diagnostics.append(found)
        return result

    monkeypatch.setattr(api, "validate", validate)
    result = render(FRONT + "# A\n\n![a](https://x/p.png) ![b](/other.png)\n")
    errors = [d for d in result.diagnostics if d.rule == "path-not-relative"]
    assert len(errors) == 2  # the validator's, and ours for the path it did not name
    assert any(d.line == 5 for d in errors)
    assert any("/other.png" in d.message for d in errors)


# -- W3: which links are links (§8.7) -----------------------------------------


@pytest.mark.parametrize("href", [
    "ftp://example.com/f", "sip:alice@example.com", "urn:isbn:0451450523", "https://example.com",
    "mailto:a@example.com", "tel:+420123456789", "magnet:?xt=urn:btih:x", "attachments/a.pdf", "../a.pdf", "#top",
])
def test_every_link_but_the_dangerous_ones_is_a_hyperlink(href: str) -> None:
    output = html(f"# A\n\nSee [the link](<{href}>).\n")
    assert f'<a href="{href}">the link</a>' in output


@pytest.mark.parametrize("href", [
    "javascript:alert(1)", "JavaScript:alert(1)", " javascript:alert(1)", "java\tscript:alert(1)",
    "jav\nascript:x", "vbscript:x", "file:///etc/passwd", "data:text/html,<b>x</b>", "data:image/png;base64,AAAA",
])
def test_a_refused_link_is_its_text_in_both_writers(href: str) -> None:
    # The builder is handed the URL directly: markdown-it refuses some of these
    # itself when parsing (§8.7), which is why is_safe_href is only the second line.
    from legaldown_render.style import load_style
    from legaldown_render.tree import Link, Text
    from legaldown_render.writers.html import HtmlWriter
    from legaldown_render.writers.text import inline_text

    inlines = (Link(href, (Text("click"),)),)
    assert HtmlWriter(load_style(None)).inlines(inlines) == "click"
    assert inline_text(inlines) == "click"
    assert not is_safe_href(href)


def test_the_deny_list_is_judged_as_a_browser_reads_the_url() -> None:
    for href in ("java\tscript:alert(1)", "jav\nascript:x", "\x01javascript:x", " JAVASCRIPT:x", "data:text/html,x",
                 "VBScript:x", "FILE:///x"):
        assert not is_safe_href(href), repr(href)
    for href in ("https://example.com", "#section", "attachments/a.pdf", "mailto:a@example.com", "ftp://x/y",
                 "sip:a@b", "urn:x:y", "a:b", "x/data:y", "javascript"):
        assert is_safe_href(href), href


def test_a_refused_link_in_a_document_is_its_text() -> None:
    # markdown-it would leave these as literal Markdown; the builder keeps them
    # as links so that both writers can show their text alone.
    body = "# A\n\nSee [click](javascript:alert(1)) and [the file](file:///x) and [d](data:text/html,x).\n"
    assert "See click and the file and d." in text(body)
    output = html(body)
    assert "See click and the file and d." in output
    assert "href=" not in output.split("</header>")[1]
    assert "alert" not in output


def test_a_link_with_an_unusual_scheme_shows_its_url_in_text() -> None:
    assert "the file <ftp://example.com/f>" in text("# A\n\nGet [the file](ftp://example.com/f).\n")


# -- B9/W8: links without a URL or without text -----------------------------


def test_a_link_with_no_url_is_its_text() -> None:
    assert "<a " not in html("# A\n\nSee [x]().\n").split("</header>")[1]
    assert 'href=""' not in html("# A\n\nSee [x]().\n")
    assert "See x." in text("# A\n\nSee [x]().\n")
    assert "x <>" not in text("# A\n\nSee [x]().\n")
    assert "See x." in text("# A\n\nSee [x](<>).\n")
    assert 'href=""' not in html("# A\n\nSee [x](<  >).\n")


def test_a_link_with_no_url_and_no_text_is_nothing() -> None:
    assert "See ." in text("# A\n\nSee [](<>).\n")
    assert "<a " not in html("# A\n\nSee [](<>).\n").split("</header>")[1]


def test_a_link_with_no_text_shows_its_url() -> None:
    assert '<a href="http://z.y">http://z.y</a>' in html("# A\n\nSee [](http://z.y).\n")
    assert "See http://z.y." in text("# A\n\nSee [](http://z.y).\n")


def test_an_autolink_is_its_address_alone_in_text() -> None:
    assert "Mail a@b.c now" in text("# A\n\nMail <a@b.c> now\n")
    assert "Call tel:+1 now" in text("# A\n\nCall [tel:+1](tel:+1) now\n")
    assert "Call +1 now" in text("# A\n\nCall [+1](tel:+1) now\n")
    assert "See https://x.y/z." in text("# A\n\nSee <https://x.y/z>.\n")
    assert 'href="mailto:a@b.c"' in html("# A\n\nMail <a@b.c> now\n")


def test_a_link_text_that_differs_from_its_url_shows_both_in_text() -> None:
    assert "the site <https://x.y/z>" in text("# A\n\nSee [the site](https://x.y/z)\n")
    assert "Mail a <mailto:a@b.c>" in text("# A\n\nMail [a](mailto:a@b.c)\n")


def test_a_link_around_a_picture_keeps_the_picture_not_its_url() -> None:
    output = html("# A\n\n[![](img/p.png)](https://example.com)\n")
    assert '<a href="https://example.com"><img src="img/p.png" alt=""></a>' in output
