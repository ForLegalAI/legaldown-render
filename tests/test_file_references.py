"""File references (§2.3): every path a document uses to name another file (an
``{{include:}}``, ``attachments[].file``, ``amends.file``, ``supersedes.file``, a
``translations`` value, an image) must be relative (``path-not-relative``) and,
when a document root is configured, stay within it (``path-outside-root``); the
checks are lexical; what the renderer does with a path that fails; the options
and the ``--root`` setting that configure the root; the lines of the findings."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from conftest import run_cli_text

import legaldown_render.api as api
from legaldown_render import OptionsError, RenderOptions, RenderRefused, render, render_file
from legaldown_render.cli import main
from legaldown_render.urls import file_reference_problem

NOT_RELATIVE = "path-not-relative"
OUTSIDE = "path-outside-root"
RULES = (NOT_RELATIVE, OUTSIDE)

# -- The check itself ------------------------------------------------------------------


@pytest.mark.parametrize(("path", "base", "url", "expected"), [
    ("a/b.lgd", None, False, None),
    ("", None, False, None),
    ("../x.lgd", None, False, None),
    ("../x.lgd", ".", False, "outside-root"),
    ("../x.lgd", "sub", False, None),
    ("../../x.lgd", "sub", False, "outside-root"),
    ("a/../x.lgd", ".", False, None),
    ("a/../../x.lgd", ".", False, "outside-root"),
    ("./a/./b", ".", False, None),
    ("a//b", ".", False, None),
    ("..", ".", False, "outside-root"),
    ("..", "sub", False, None),
    ("a/b/..", ".", False, None),
    ("/etc/passwd", ".", False, "not-relative"),
    ("/etc/passwd", None, False, "not-relative"),
    ("//host/share", None, False, "not-relative"),
    ("\\x", None, False, "not-relative"),
    ("\\\\host\\x", None, False, "not-relative"),
    ("https://x.example/a", None, False, "not-relative"),
    ("HTTPS://x.example/a", None, False, "not-relative"),
    ("file:///etc/passwd", None, False, "not-relative"),
    ("data:text/plain,x", None, False, "not-relative"),
    ("C:\\x", None, False, "not-relative"),
    ("C:/x", None, False, "not-relative"),
    ("c:x", None, False, "not-relative"),
    ("ht\ttps://x", None, False, "not-relative"),
    (" /etc/passwd", None, False, "not-relative"),
    ("a/b:c", None, False, None),
    # A backslash is read as a separator.
    ("a\\b", ".", False, None),
    ("..\\x", ".", False, "outside-root"),
    ("..\\x", "sub", False, None),
    ("a\\..\\..\\x", ".", False, "outside-root"),
    # Encoding: decoded for a URL, as written for a path.
    ("%2e%2e/x", ".", False, None),
    ("%2e%2e/x", ".", True, "outside-root"),
    ("..%2fx", ".", True, "outside-root"),
    ("..%5cx", ".", True, "outside-root"),
    ("%2fetc", None, True, "not-relative"),
    ("%2fetc", None, False, None),
    ("%2e%2e/x", "sub", True, None),
    # One decoding only: %252e is a name, %2e after it.
    ("%252e%252e/x", ".", True, None),
    # Spaces after the name are the Windows reading of the same segment.
    (".. /x", ".", False, "outside-root"),
    # Windows drops trailing dots and spaces too.
    ("... /x", ".", False, "outside-root"),
    ("..../x", ".", False, "outside-root"),
    ("... /x", "sub", False, None),
    ("a/.. ./../x", ".", False, "outside-root"),
    # Names that only look like it.
    ("a..b.png", ".", False, None),
    ("...hidden/x", ".", False, None),
    ("..a/x", ".", False, None),
    # A control character is not a file name: NUL, after the decoding a URL gets.
    ("a\x00b", None, False, "not-relative"),
    ("..%00/x.png", ".", True, "not-relative"),
    ("..%00/x.png", ".", False, None),
    ("a\x1fb/x", ".", False, "not-relative"),
    ("a\x7fb/x", ".", False, "not-relative"),
    ("a\x85b/x", ".", False, "not-relative"),
])
def test_the_check(path: str, base: str | None, url: bool, expected: str | None) -> None:
    assert file_reference_problem(path, base=base, url=url) == expected


# -- Each kind of reference ------------------------------------------------------------


def _frontmatter(extra: str) -> str:
    return f"---\ntitle: T\n{extra}---\n\n# A\n\nBody.\n"


def _kind_source(kind: str, path: str) -> tuple[str, str]:
    """A document with *path* in a reference of *kind*, and the text of the
    line the finding is expected at."""
    quoted = json.dumps(path)
    if kind == "include":
        return _frontmatter("").replace("Body.", f"Before {{{{include: {path}}}}} after."), "Before"
    if kind == "image":
        # Markdown reads two backslashes as one.
        return _frontmatter("").replace("Body.", f"Before ![the logo](<{path.replace(chr(92), chr(92) * 2)}>) after."), "Before"
    if kind == "attachment":
        return _frontmatter(f"attachments:\n  - id: annex\n    title: Annex\n    file: {quoted}\n"), "    file:"
    if kind == "amends":
        return _frontmatter(f"amends:\n  title: Original\n  file: {quoted}\n"), "  file:"
    if kind == "supersedes":
        return _frontmatter(f"supersedes:\n  title: Old\n  file: {quoted}\n"), "  file:"
    if kind == "translation":
        return _frontmatter(f"translations:\n  cs: {quoted}\n"), "  cs:"
    raise AssertionError(kind)


KINDS = ["include", "image", "attachment", "amends", "supersedes", "translation"]
#: The kinds whose path is read as a URL.
URL_KINDS = {"image"}


def line_of(source: str, needle: str) -> int:
    return next(n for n, line in enumerate(source.split("\n"), 1) if needle in line)


def expected_line(kind: str, path: str) -> int:
    source, marker = _kind_source(kind, path)
    return line_of(source, marker)


def findings(result) -> list[tuple[str, int | None]]:  # type: ignore[no-untyped-def]
    return [(d.rule, d.line) for d in result.diagnostics if d.rule in RULES]


def reported(kind: str, path: str, tmp_path: Path, where: str | None, **settings: object) -> list[tuple[str, int | None]]:
    """The file-reference findings for *path* in *kind*, with the document root
    *tmp_path* (``where`` is the document's directory in it, ``""`` for the
    root itself) or none."""
    source, _ = _kind_source(kind, path)
    if where is not None:
        settings.update(document_root=tmp_path, document_path=tmp_path / where / "doc.lgd")
    return findings(render(source, **settings))


# (path, no root, document at the root, document in the directory "sub")
COMMON = [
    ("annex/a.lgd", None, None, None),
    ("a.lgd", None, None, None),
    ("../a.lgd", None, OUTSIDE, None),
    ("a/../a.lgd", None, None, None),
    ("a/../../a.lgd", None, OUTSIDE, None),
    ("../../a.lgd", None, OUTSIDE, OUTSIDE),
    ("../sub/a.lgd", None, OUTSIDE, None),
    ("/etc/passwd", NOT_RELATIVE, NOT_RELATIVE, NOT_RELATIVE),
    ("//host/share/a.lgd", NOT_RELATIVE, NOT_RELATIVE, NOT_RELATIVE),
    ("https://x.example/a.lgd", NOT_RELATIVE, NOT_RELATIVE, NOT_RELATIVE),
    ("file:///etc/passwd", NOT_RELATIVE, NOT_RELATIVE, NOT_RELATIVE),
    ("C:\\x\\a.lgd", NOT_RELATIVE, NOT_RELATIVE, NOT_RELATIVE),
    ("C:/x/a.lgd", NOT_RELATIVE, NOT_RELATIVE, NOT_RELATIVE),
    ("\\\\host\\share\\a.lgd", NOT_RELATIVE, NOT_RELATIVE, NOT_RELATIVE),
    ("\\a.lgd", NOT_RELATIVE, NOT_RELATIVE, NOT_RELATIVE),
    # A backslash is a separator.
    ("a\\b.lgd", None, None, None),
    ("..\\a.lgd", None, OUTSIDE, None),
    ("..\\..\\a.lgd", None, OUTSIDE, OUTSIDE),
]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(("path", "no_root", "at_root", "in_sub"), COMMON, ids=[c[0] for c in COMMON])
def test_each_kind_of_reference(kind: str, path: str, no_root: str | None, at_root: str | None,
                                in_sub: str | None, tmp_path: Path) -> None:
    line = expected_line(kind, path)
    for where, expected in ((None, no_root), ("", at_root), ("sub", in_sub)):
        found = reported(kind, path, tmp_path, where)
        assert found == ([(expected, line)] if expected else []), (kind, path, where, found)


@pytest.mark.parametrize("kind", KINDS)
def test_encoded_traversal_is_read_as_a_url_only_where_it_is_one(kind: str, tmp_path: Path) -> None:
    # A directory may be called "%2e%2e"; an image's path is a URL, in which it is "..".
    path = "%2e%2e/%2e%2e/a.lgd"
    expected = [(OUTSIDE, expected_line(kind, path))] if kind in URL_KINDS else []
    assert reported(kind, path, tmp_path, "") == expected
    assert reported(kind, path, tmp_path, None) == []
    # Encoded separators too.
    encoded = "..%2f..%5ca.lgd"
    assert reported(kind, encoded, tmp_path, "") == ([(OUTSIDE, expected_line(kind, encoded))]
                                                    if kind in URL_KINDS else [])


@pytest.mark.parametrize("kind", ["include", "image"])
def test_a_disguised_scheme_is_a_scheme(kind: str, tmp_path: Path) -> None:
    for path in ("ht\ttps://x.example/a", "%68ttps://x.example/a" if kind in URL_KINDS else "https:%2f%2fx.example"):
        assert [rule for rule, _ in reported(kind, path, tmp_path, None)] == [NOT_RELATIVE], path


@pytest.mark.parametrize("kind", ["include", "image"])
def test_a_path_with_a_control_character_is_not_relative(kind: str) -> None:
    path = {"include": "a\x00b.lgd", "image": "..%00/x.png"}[kind]
    source = _frontmatter("").replace("Body.", {"include": f"{{{{include: {path}}}}}", "image": f"![x]({path})"}[kind])
    assert [rule for rule, _ in findings(render(source))] == [NOT_RELATIVE]


@pytest.mark.parametrize("path", [".. ./x.png", "... /x.png", "..../x.png"])
def test_a_parent_directory_as_windows_reads_it_leaves_the_root(path: str, tmp_path: Path) -> None:
    source = _frontmatter("").replace("Body.", f"![x](<{path}>)")
    assert [rule for rule, _ in findings(render(source, document_root=tmp_path, document_path=tmp_path / "d.lgd"))] == [OUTSIDE]


# -- What happens to the path ------------------------------------------------------------


def test_an_image_outside_the_root_is_its_alt_text(tmp_path: Path) -> None:
    source = _frontmatter("").replace("Body.", "Before ![the logo](../../etc/logo.png) after.")
    for fmt in ("html", "text"):
        result = render(source, format=fmt, document_root=tmp_path, document_path=tmp_path / "doc.lgd")
        assert "<img" not in result.output and "etc/logo" not in result.output
        assert "Before the logo after." in result.output
        [found] = [d for d in result.diagnostics if d.rule == OUTSIDE]
        assert found.level == "error" and "'../../etc/logo.png'" in found.message and "root" in found.message
        assert not result.ok
    # Within the root it is an image; with no root, too.
    inside = render(source.replace("../../", "../"), document_root=tmp_path, document_path=tmp_path / "d" / "doc.lgd")
    assert '<img src="../etc/logo.png"' in inside.output and inside.ok
    assert '<img src="../../etc/logo.png"' in render(source).output


def test_an_include_marker_stays_visible_and_the_path_is_reported(tmp_path: Path) -> None:
    source = _frontmatter("").replace("Body.", "{{include: ../../outside/part.lgd}}")
    for fmt in ("html", "text"):
        result = render(source, format=fmt, document_root=tmp_path)
        assert "[NOT PROCESSED: include ../../outside/part.lgd]" in result.output
        assert {d.rule for d in result.diagnostics} >= {"render-not-processed", OUTSIDE}


def test_attachments_keep_their_path_as_text_and_never_link_to_a_bad_one(tmp_path: Path) -> None:
    front = ("attachments:\n  - id: annex\n    title: Annex A\n    file: ../../secret/annex.lgd\n"
             "  - id: fine\n    title: Annex B\n    file: annex/b.lgd\n")
    source = _frontmatter(front).replace("Body.", "See {{attach: annex}} and {{attach: fine}}.")
    for mode, overrides in (("placeholder", {}), ("omit", {"attachments.render": "omit"})):
        result = render(source, document_root=tmp_path, overrides=overrides)
        assert (OUTSIDE, line_of(source, "    file: ../../")) in findings(result), mode
        assert 'href="../../secret' not in result.output
        assert "See Annex A and" in render(source, document_root=tmp_path, overrides=overrides, format="text").output
        if mode == "placeholder":
            # The placeholder shows the path, as text.
            assert "../../secret/annex.lgd" in result.output
    omitted = render(source, document_root=tmp_path, overrides={"attachments.render": "omit"})
    assert "secret" not in omitted.output
    # A path that is fine is a link; with no root, so is the one that leaves the directory.
    assert '<a class="ld-value ld-attach" href="annex/b.lgd">Annex B</a>' in omitted.output
    assert 'href="../../secret/annex.lgd"' in render(source, overrides={"attachments.render": "omit"}).output


def test_the_attachment_link_is_read_as_a_url(tmp_path: Path) -> None:
    # As written the path names a directory called "%2e%2e"; as the link's URL it leaves the root.
    source = _frontmatter("attachments:\n  - id: annex\n    title: Annex\n    file: '%2e%2e/%2e%2e/a.lgd'\n"
                          ).replace("Body.", "See {{attach: annex}}.")
    result = render(source, document_root=tmp_path, overrides={"attachments.render": "omit"})
    assert "href=" not in result.output
    assert [rule for rule, _ in findings(result)] == [OUTSIDE]
    assert findings(render(source, document_root=tmp_path)) == []
    assert findings(render(source, overrides={"attachments.render": "omit"})) == []


def test_the_same_attachment_path_is_one_finding(tmp_path: Path) -> None:
    source = _frontmatter("attachments:\n  - id: annex\n    title: Annex\n    file: /etc/passwd\n"
                          ).replace("Body.", "See {{attach: annex}}.")
    result = render(source, overrides={"attachments.render": "omit"})
    assert findings(result) == [(NOT_RELATIVE, line_of(source, "    file:"))]
    assert "href=" not in result.output


def test_a_markdown_link_is_not_a_file_reference(tmp_path: Path) -> None:
    # §8.7 links follow the link allow-list; §2.3 is about references to files.
    source = _frontmatter("").replace("Body.", "See [the file](/etc/passwd) and [up](../../a.html).")
    result = render(source, document_root=tmp_path)
    assert findings(result) == []
    assert '<a href="/etc/passwd">the file</a>' in result.output and '<a href="../../a.html">up</a>' in result.output


def test_the_same_path_in_two_places_is_one_finding_at_the_first(tmp_path: Path) -> None:
    source = _frontmatter("").replace("Body.", "One {{include: /x.lgd}}.\n\nTwo {{include: /x.lgd}}.\n\nThree {{include: /y.lgd}}.")
    assert findings(render(source)) == [(NOT_RELATIVE, line_of(source, "One")), (NOT_RELATIVE, line_of(source, "Three"))]


def test_a_path_that_breaks_both_rules_is_not_relative() -> None:
    assert [rule for rule, _ in findings(render(_frontmatter("").replace("Body.", "{{include: /../x}}"),
                                                 document_root="."))] == [NOT_RELATIVE]


def test_the_validators_report_of_an_include_is_not_repeated(monkeypatch: pytest.MonkeyPatch) -> None:
    from legaldown import Diagnostic
    from legaldown import validate as real_validate

    source = _frontmatter("").replace("Body.", "Before {{include: https://x.example/p.lgd}}")
    line = line_of(source, "Before")

    def validate(document, **kwargs):  # type: ignore[no-untyped-def]
        result = real_validate(document, **kwargs)
        result.diagnostics.append(Diagnostic(rule=NOT_RELATIVE, level="error", line=line,
                                             message="Include path 'https://x.example/p.lgd' is not relative."))
        return result

    monkeypatch.setattr(api, "validate", validate)
    assert findings(render(source)) == [(NOT_RELATIVE, line)]
    assert len([d for d in render(source).diagnostics if d.rule == NOT_RELATIVE]) == 1


def test_a_finding_names_its_file(tmp_path: Path) -> None:
    source, _ = _kind_source("amends", "/etc/passwd")
    [found] = [d for d in render(source, filename="deal.lgd").diagnostics if d.rule == NOT_RELATIVE]
    assert found.file == "deal.lgd"
    assert found.level == "error" and "'/etc/passwd'" in found.message


# -- Strict ---------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_strict_refuses_a_path_outside_the_root(kind: str, tmp_path: Path) -> None:
    source, _ = _kind_source(kind, "../../a.lgd")
    with pytest.raises(RenderRefused) as refused:
        render(source, strict=True, document_root=tmp_path)
    assert OUTSIDE in {d.rule for d in refused.value.diagnostics}


@pytest.mark.parametrize("kind", [k for k in KINDS if k != "include"])
def test_strict_without_a_root_accepts_a_path_that_leaves_the_directory(kind: str) -> None:
    # §2.3 defines no default root. (An include is a warning of its own, not an error.)
    source, _ = _kind_source(kind, "../../a.lgd")
    assert render(source, strict=True).ok


@pytest.mark.parametrize("kind", KINDS)
def test_strict_refuses_an_absolute_path(kind: str) -> None:
    source, _ = _kind_source(kind, "/etc/passwd")
    with pytest.raises(RenderRefused) as refused:
        render(source, strict=True)
    assert NOT_RELATIVE in {d.rule for d in refused.value.diagnostics}


# -- The document root --------------------------------------------------------------------


def test_the_document_sits_at_the_root_when_it_has_no_path(tmp_path: Path) -> None:
    source, _ = _kind_source("amends", "../a.lgd")
    assert [rule for rule, _ in findings(render(source, document_root=tmp_path))] == [OUTSIDE]
    assert [rule for rule, _ in findings(render(source, document_root=str(tmp_path)))] == [OUTSIDE]
    assert findings(render(source)) == []


def test_a_document_in_a_subdirectory_may_go_up_to_the_root(tmp_path: Path) -> None:
    source, _ = _kind_source("amends", "../shared/a.lgd")
    deep = tmp_path / "a" / "b" / "doc.lgd"
    assert findings(render(source, document_root=tmp_path, document_path=deep)) == []
    source, _ = _kind_source("amends", "../../../a.lgd")
    assert [rule for rule, _ in findings(render(source, document_root=tmp_path, document_path=deep))] == [OUTSIDE]


def test_the_root_may_be_given_with_dots_and_the_path_relative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "docs" / "sub").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    source, _ = _kind_source("amends", "../a.lgd")
    assert findings(render(source, document_root="docs", document_path="docs/sub/doc.lgd")) == []
    assert findings(render(source, document_root="docs/sub/..", document_path="./docs/sub/doc.lgd")) == []
    assert [r for r, _ in findings(render(source, document_root="docs", document_path="docs/doc.lgd"))] == [OUTSIDE]


def test_a_symbolic_link_is_not_followed(tmp_path: Path) -> None:
    # The check is lexical: it neither resolves links nor reads anything.
    outside = tmp_path / "outside"
    root = tmp_path / "root"
    outside.mkdir()
    root.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available")
    source, _ = _kind_source("amends", "link/a.lgd")
    assert findings(render(source, document_root=root)) == []


def test_a_root_that_does_not_exist_is_fine(tmp_path: Path) -> None:
    source, _ = _kind_source("amends", "../a.lgd")
    assert [r for r, _ in findings(render(source, document_root=tmp_path / "nope"))] == [OUTSIDE]


def test_a_document_outside_the_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    with pytest.raises(OptionsError, match="outside the document root"):
        render("# A\n", document_root=root, document_path=tmp_path / "doc.lgd")
    with pytest.raises(OptionsError):
        render("# A\n", document_root=root, document_path=root / ".." / "doc.lgd")
    # A directory whose name starts with dots is not the parent.
    render("# A\n", document_root=root, document_path=root / "..x" / "doc.lgd")
    # Both are checked even when the document has nothing to check.
    with pytest.raises(OptionsError):
        render("# A\n", document_root=root, document_path=tmp_path / "other" / "doc.lgd", format="text")


def test_an_empty_root_or_path_is_refused() -> None:
    with pytest.raises(OptionsError, match="document_root"):
        render("# A\n", document_root="")
    with pytest.raises(OptionsError, match="document_path"):
        render("# A\n", document_root=".", document_path="")


@pytest.mark.skipif(os.name != "nt", reason="drive letters are a Windows matter")
def test_a_document_on_another_drive_is_refused() -> None:  # pragma: no cover - Windows only
    with pytest.raises(OptionsError, match="drive"):
        render("# A\n", document_root="C:\\docs", document_path="D:\\docs\\doc.lgd")


def test_a_path_without_a_root_is_not_checked_for_the_document(tmp_path: Path) -> None:
    # document_path alone configures no root.
    source, _ = _kind_source("amends", "../../../a.lgd")
    assert findings(render(source, document_path=tmp_path / "doc.lgd")) == []


def test_the_document_path_names_the_file_unless_one_is_given(tmp_path: Path) -> None:
    source, _ = _kind_source("amends", "/x")
    result = render(source, document_path=tmp_path / "deal.lgd")
    assert {d.file for d in result.diagnostics} == {"deal.lgd"}
    named = render(source, document_path=tmp_path / "deal.lgd", filename="other.lgd")
    assert {d.file for d in named.diagnostics} == {"other.lgd"}


def test_options_default_to_no_root() -> None:
    options = RenderOptions()
    assert options.document_root is None and options.document_path is None


# -- render_file -----------------------------------------------------------------------


def test_render_file_sets_the_document_path(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    path = sub / "deal.lgd"
    # Up one directory is the root; up two escapes it.
    source, _ = _kind_source("amends", "../a.lgd")
    path.write_text(source, encoding="utf-8")
    assert findings(render_file(path, document_root=tmp_path)) == []
    assert findings(render_file(path, RenderOptions(document_root=tmp_path))) == []
    assert [r for r, _ in findings(render_file(path, document_root=sub))] == [OUTSIDE]
    # Without a root: no check.
    path.write_text(_kind_source("amends", "../../../a.lgd")[0], encoding="utf-8")
    assert findings(render_file(path)) == []
    escaping = findings(render_file(path, document_root=tmp_path))
    assert [r for r, _ in escaping] == [OUTSIDE]
    assert {d.file for d in render_file(path, document_root=tmp_path).diagnostics} == {"deal.lgd"}


def test_render_file_refuses_a_document_outside_the_root(tmp_path: Path) -> None:
    (tmp_path / "root").mkdir()
    path = tmp_path / "deal.lgd"
    path.write_text("# A\n", encoding="utf-8")
    with pytest.raises(OptionsError):
        render_file(path, document_root=tmp_path / "root")


def test_render_file_keeps_a_document_path_that_was_given(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    path = tmp_path / "deal.lgd"
    path.write_text(_kind_source("amends", "../a.lgd")[0], encoding="utf-8")
    # The document is said to be in sub/, so ../ stays in the root.
    assert findings(render_file(path, document_root=tmp_path, document_path=tmp_path / "sub" / "x.lgd")) == []


# -- The command line --------------------------------------------------------------------


def _write(tmp_path: Path, body_kind: str, path: str, name: str = "deal.lgd", directory: str = "") -> Path:
    target = tmp_path / directory / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_kind_source(body_kind, path)[0], encoding="utf-8")
    return target


def test_root_reports_a_path_that_escapes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    document = _write(tmp_path, "include", "../../outside/part.lgd")
    source, _ = _kind_source("include", "../../outside/part.lgd")
    assert main([str(document), "-f", "text", "--root", str(tmp_path)]) == 0
    err = capsys.readouterr().err
    assert f"{document}:{line_of(source, 'Before')}: error: [path-outside-root] " in err
    assert main([str(document), "-f", "text"]) == 0
    assert "path-outside-root" not in capsys.readouterr().err


def test_root_with_strict_refuses(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    document = _write(tmp_path, "amends", "../../a.lgd")
    assert main([str(document), "--strict", "--root", str(tmp_path)]) == 1
    assert "[path-outside-root]" in capsys.readouterr().err
    assert main([str(document), "--strict"]) == 0


def test_root_is_the_boundary_for_a_document_in_a_subdirectory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    document = _write(tmp_path, "amends", "../shared/a.lgd", directory="contracts")
    assert main([str(document), "-q", "--root", str(tmp_path)]) == 0
    assert main([str(document), "--root", str(tmp_path / "contracts")]) == 0
    assert "[path-outside-root]" in capsys.readouterr().err


def test_root_refuses_a_document_outside_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "root").mkdir()
    document = _write(tmp_path, "amends", "a.lgd")
    assert main([str(document), "--root", str(tmp_path / "root")]) == 2
    assert "outside the document root" in capsys.readouterr().err


def test_root_with_standard_input_puts_the_document_at_the_root(tmp_path: Path) -> None:
    source, _ = _kind_source("amends", "../a.lgd")
    done = run_cli_text("-", "-f", "text", "--root", str(tmp_path), stdin=source)
    assert done.returncode == 0
    assert f"<stdin>:{line_of(source, '  file:')}: error: [path-outside-root] " in done.stderr
    assert "path-outside-root" not in run_cli_text("-", "-f", "text", stdin=source).stderr


def test_not_relative_is_reported_without_a_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    document = _write(tmp_path, "translation", "https://x.example/cs.lgd")
    assert main([str(document), "-f", "text"]) == 0
    assert f"{document}:{line_of(document.read_text(encoding='utf-8'), '  cs:')}: error: [path-not-relative] " \
        in capsys.readouterr().err


def test_root_is_in_the_help() -> None:
    done = run_cli_text("--help")
    assert "--root" in done.stdout and "DIR" in done.stdout
