"""Links and images: which image paths are shown (§2.3), which links are links
(§8.7), a link with no URL or no text, and how a refused link or image is
reported."""
from __future__ import annotations

import pytest
from conftest import FRONT, html, text

from legaldown_render import RenderRefused, render
from legaldown_render.urls import displayed_url, is_link_target, is_safe_href

# -- Image paths (§2.3) ----------------------------------------------------------

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
        # Line 6 is the paragraph's: a report at another line is another finding.
        found = Diagnostic(rule="path-not-relative", level="error", line=6,
                           message="Image path 'https://x/p.png' is not relative (§2.3).")
        result.diagnostics.append(found)
        return result

    monkeypatch.setattr(api, "validate", validate)
    result = render(FRONT + "# A\n\n![a](https://x/p.png) ![b](/other.png)\n")
    errors = [d for d in result.diagnostics if d.rule == "path-not-relative"]
    assert len(errors) == 2  # the validator's, and ours for the path it did not name
    assert all(d.line == 6 for d in errors)
    assert any("/other.png" in d.message for d in errors)


def test_a_path_that_starts_another_is_not_covered_by_the_validators_report() -> None:
    # Whatever the validator reports for one path, a different path whose text
    # it contains is still the renderer's to report.
    result = render(FRONT + "# A\n\n![a](/x.png) ![b](/x.png2)\n")
    messages = [d.message for d in result.diagnostics if d.rule == "path-not-relative"]
    assert any("'/x.png2'" in m for m in messages)
    assert any("'/x.png'" in m for m in messages)


@pytest.mark.parametrize("path", ["javascript:alert(1)", "data:text/html,x", "https://e/p.png"])
def test_the_not_relative_message_is_generic(path: str) -> None:
    result = render(FRONT + f"# A\n\n![x](<{path}>)\n")
    message = next(d.message for d in result.diagnostics if d.rule == "path-not-relative")
    assert "never fetched" not in message
    assert "file references are relative paths with no URI scheme. Its alt text is shown instead." in message


@pytest.mark.parametrize("empty", ["", "  ", "%20"])
def test_an_image_with_no_path_is_its_alt_text(empty: str) -> None:
    for fmt in ("html", "text"):
        result = render(FRONT + f"# A\n\nBefore ![the logo](<{empty}>) after.\n", format=fmt, strict=True)
        assert "<img" not in result.output
        assert 'src=""' not in result.output
        assert "Before the logo after." in result.output
        assert not [d for d in result.diagnostics if d.level == "error"]


# -- Which links are links (§8.7) ------------------------------------------------

HANDLERS = [
    "ms-msdt:/id PCWDiagnostic", "ms-word:ofe|u|https://x/a.docx", "ms-officecmd:{}", "search-ms:query=x",
    "ms-appinstaller:?source=https://x/a.appx", "vscode://file/x", "intent://x#Intent;end", "blob:https://x/1",
    "about:blank", "jar:https://x/a.jar!/", "view-source:https://x", "mhtml:file://x", "ms-its:x.chm::/a.htm",
    "C:\\x", "C:/x", "localhost:8080", "magnet:?xt=urn:btih:x", "javascript:alert(1)", "file:///etc/passwd",
    "data:text/html,x", " MS-MSDT:x",
]

ALLOWED = [
    "http://e/x", "https://e/x", "mailto:a@e", "tel:+1", "ftp://e/x", "ftps://e/x", "sftp://e/x", "sip:a@e",
    "sips:a@e", "sms:+1", "urn:isbn:1", "geo:1,2", "news:a.b", "a/b.pdf", "../a.pdf", "#top", "?q=1",
    "ftp://example.com/f", "sip:alice@example.com", "urn:isbn:0451450523", "https://example.com",
    "mailto:a@example.com", "tel:+420123456789", "attachments/a.pdf",
]


@pytest.mark.parametrize("href", ALLOWED)
def test_an_allowed_scheme_or_a_relative_url_is_a_link(href: str) -> None:
    assert is_link_target(href)
    assert f'<a href="{href}">click</a>' in html(f"# A\n\nSee [click](<{href}>).\n")


@pytest.mark.parametrize("href", HANDLERS)
def test_a_protocol_handler_or_pseudo_scheme_is_not_a_link(href: str) -> None:
    assert not is_link_target(href)
    body = f"# A\n\nSee [click](<{href}>) now.\n"
    result = render(FRONT + body, format="html")
    assert "See click now." in result.output
    assert "<a href" not in result.output.split("</header>")[1]
    assert "See click now." in text(body)
    assert "<" not in text(body).split("See click now.")[0].split("\n")[-1]


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


def test_the_scheme_is_judged_as_a_browser_reads_the_url() -> None:
    # markdown-it percent-encodes a tab in a URL it parses ("ms%09-msdt:x" is a
    # relative path to a browser); the check still holds for a URL it is given
    # directly, as a browser would read it.
    for href in ("java\tscript:alert(1)", "jav\nascript:x", "\x01javascript:x", " JAVASCRIPT:x", "data:text/html,x",
                 "VBScript:x", "FILE:///x", "ms\t-msdt:x", "\u0000 vscode://x"):
        assert not is_safe_href(href), repr(href)
        assert not is_link_target(href), repr(href)
    for href in ("https://example.com", "#section", "attachments/a.pdf", "mailto:a@example.com", "ftp://x/y",
                 "sip:a@b", "urn:x:y", "x/data:y", "javascript", "HTTPS://X", "\thttps://x"):
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


def test_a_refused_link_is_reported_once_as_a_warning() -> None:
    body = "# A\n\n[a](ms-msdt:x) and [b](ms-msdt:x) and [c](vscode://x) and [d](http://ok) and [e]().\n"
    result = render(FRONT + body, format="html")
    refused = [d for d in result.diagnostics if d.rule == "render-link-refused"]
    assert [d.level for d in refused] == ["warning", "warning"]
    assert "Link to 'ms-msdt:x' is not rendered as a link" in refused[0].message
    assert "its text is shown" in refused[0].message
    assert "vscode://x" in refused[1].message
    assert result.ok  # a Warning does not refuse, even under --strict's Errors
    assert render(FRONT + body, format="text").diagnostics == result.diagnostics


def test_an_empty_or_allowed_link_is_not_reported() -> None:
    result = render(FRONT + "# A\n\n[a]() [b](<>) [c](https://e) [d](#x) [e](rel/path)\n")
    assert not [d for d in result.diagnostics if d.rule == "render-link-refused"]


# -- Links without a URL or without text -----------------------------------------

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


def test_displayed_url_undoes_the_encoding_unless_it_shows_a_control() -> None:
    assert displayed_url(" https://e/a%20b ") == "https://e/a b"
    assert displayed_url("https://e/a%0ab") == "https://e/a%0ab"
    assert displayed_url("https://e/%E2%80%AE") == "https://e/%E2%80%AE"


def test_a_link_with_no_text_shows_the_written_url() -> None:
    body = "# A\n\nSee [](<https://e/a b/é>).\n"
    assert 'href="https://e/a%20b/%C3%A9">https://e/a b/é</a>' in html(body)
    assert "See https://e/a b/é." in text(body)


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
