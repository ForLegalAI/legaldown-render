"""``html_id_prefix`` (issue #13): every id of the HTML output, and every
link within it, carries the prefix, so that a page embedding the output keeps
its own ids apart."""
from __future__ import annotations

import re

import pytest
from conftest import DOCUMENTS

from legaldown_render import OptionsError, render
from legaldown_render.cli import main

IDS = re.compile(r' id="([^"]*)"')
FRAGMENTS = re.compile(r' href="#([^"]*)"')


def html(source: str, **settings: object) -> str:
    return render(source, format="html", **settings).output


@pytest.mark.parametrize("name", ["features.lgd", "template.lgd"])
@pytest.mark.parametrize("prefix", ["", "ld-", "doc_1"])
def test_every_link_within_the_page_has_its_target(name: str, prefix: str) -> None:
    output = html((DOCUMENTS / name).read_text(encoding="utf-8"), html_id_prefix=prefix)
    ids = IDS.findall(output)
    fragments = FRAGMENTS.findall(output)
    assert ids
    assert len(set(ids)) == len(ids)
    assert all(identifier.startswith(prefix) for identifier in ids)
    assert set(fragments) <= set(ids)


def test_the_prefix_changes_nothing_but_ids_and_links() -> None:
    source = (DOCUMENTS / "features.lgd").read_text(encoding="utf-8")
    plain, prefixed = html(source), html(source, html_id_prefix="ld-")
    assert prefixed != plain
    assert prefixed.replace(' id="ld-', ' id="').replace(' href="#ld-', ' href="#') == plain


def test_authored_and_attachment_links_are_prefixed_and_others_are_not() -> None:
    source = ("---\ntitle: T\nattachments:\n  - id: annex\n    title: Annex\n    file: annex.pdf\n---\n\n"
              "# Scope {#scope}\n\nSee [scope](#scope), {{attach: annex}}, [site](https://example.com/#top) "
              "and {{ref: scope}}.\n")
    output = html(source, html_id_prefix="p-")
    assert 'id="p-scope"' in output
    assert 'href="#p-scope">scope</a>' in output
    assert 'href="https://example.com/#top"' in output
    assert set(FRAGMENTS.findall(output)) <= set(IDS.findall(output))
    assert any(fragment != "p-scope" for fragment in FRAGMENTS.findall(output))  # the attachment's


def test_a_bare_fragment_and_ids_of_the_host_page_stay_as_written() -> None:
    output = html("# Scope {#scope}\n\n[top](#), [skip](#main) and [scope](#scope).\n", html_id_prefix="ld-")
    assert 'href="#">top</a>' in output
    assert 'href="#main">skip</a>' in output
    assert 'href="#ld-scope">scope</a>' in output


def test_the_writer_checks_the_prefix_itself() -> None:
    from legaldown_render.style import load_style
    from legaldown_render.writers import HtmlWriter

    with pytest.raises(OptionsError, match="id prefix"):
        HtmlWriter(load_style("default"), id_prefix="my doc ")


def test_the_tree_keeps_its_anchors() -> None:
    result = render("# Scope {#scope}\n\nText.\n", format="html", html_id_prefix="ld-")
    assert result.tree.sections[0].anchor == "scope"
    assert 'id="ld-scope"' in result.output


@pytest.mark.parametrize("prefix", ["ld-", "1x", None])
def test_other_formats_ignore_the_prefix(prefix: object) -> None:
    source = "# Scope {#scope}\n\nText.\n"
    assert render(source, format="text", html_id_prefix=prefix).output == render(source, format="text").output
    assert render(source, format="docx", html_id_prefix=prefix).binary


@pytest.mark.parametrize("prefix", ["1st", "-x", "has space", "a#b", "a%20", "é", "a:b", "a\"b", None, 1])
def test_an_unusable_prefix_is_refused(prefix: str) -> None:
    with pytest.raises(OptionsError, match="id prefix"):
        render("Text.\n", html_id_prefix=prefix)


def test_command_line(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    document = tmp_path / "doc.lgd"
    document.write_text("# Scope {#scope}\n\nSee {{ref: scope}}.\n", encoding="utf-8")
    assert main([str(document), "--fragment", "--id-prefix", "ld-", "-q"]) == 0
    output = capsys.readouterr().out
    assert 'id="ld-scope"' in output and 'href="#ld-scope"' in output
    assert main([str(document), "--id-prefix", "1"]) == 2
    for output in ("doc.docx", "doc.txt"):
        with pytest.raises(SystemExit):
            main([str(document), "--id-prefix", "ld-", "-o", str(tmp_path / output)])
