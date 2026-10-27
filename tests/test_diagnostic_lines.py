"""Where the renderer's own diagnostics point (§16.9): the file, and the line
of the block, list item or heading they are in, or of the frontmatter key they
are about; how a repeat is left out; how the command line prints them.

The validator names the first line of a block for anything in it, and so do
these: a paragraph's findings are all at its first line, a table's at its
header row, a quote's at its first ``>``, a list item's at its marker. What
the validator makes public of a directive's own line is not used (see
``legaldown_render.diagnostics``)."""
from __future__ import annotations

from pathlib import Path

import pytest
from conftest import run_cli_text

import legaldown_render.api as api
from legaldown_render import RenderOptions, render, render_file
from legaldown_render.cli import main
from legaldown_render.diagnostics import key_line
from legaldown_render.errors import RenderRefused

HEAD = "---\ntitle: T\n---\n"


def lines_of(result, rule: str) -> list[int | None]:  # type: ignore[no-untyped-def]
    return [d.line for d in result.diagnostics if d.rule == rule]


def at(source: str, needle: str) -> int:
    """The line (from 1) of the first line of *source* holding *needle*."""
    return next(number for number, line in enumerate(source.split("\n"), 1) if needle in line)


# One finding that the renderer reports, whatever the block it stands in: an
# include is not processed (§17.5), a link's scheme is refused (§8.7), an image
# path is not relative (§2.3).
TRIGGERS = {
    "render-not-processed": "{{include: part.lgd}}",
    "render-link-refused": "[the link](ms-msdt:x)",
    "path-not-relative": "![the logo](/etc/passwd)",
}

#: Containers for a trigger: the text of a section with the trigger at MARK, and the word that
#: marks the line its finding is expected at.
CONTAINERS = {
    "paragraph": ("# A\n\nFirst line FIRST\nsecond line\nthird MARK line\n", "FIRST"),
    "heading": ("# Heading MARK FIRST\n\nText.\n", "FIRST"),
    "list item": ("# A\n\n- one\n- two FIRST MARK\n- three\n", "FIRST"),
    "item, second line": ("# A\n\n- one\n- two FIRST\n  continued MARK\n- three\n", "FIRST"),
    "nested item": ("# A\n\n- one\n  - nested FIRST MARK\n  - more\n- three\n", "FIRST"),
    "nested item, third level": ("# A\n\n- a\n  - b\n    - c FIRST MARK\n- d\n", "FIRST"),
    "item, second block": ("# A\n\n- one FIRST\n\n  another MARK paragraph\n", "FIRST"),
    "ordered item": ("# A\n\n1. one\n2. two FIRST MARK\n", "FIRST"),
    "table": ("# A\n\n| a FIRST | b |\n|---|---|\n| x | y |\n| z | MARK |\n", "FIRST"),
    "quote": ("# A\n\n> quoted FIRST\n> second MARK\n", "FIRST"),
    "quote, second paragraph": ("# A\n\n> quoted FIRST\n>\n> second MARK\n", "FIRST"),
    "list in a quote": ("# A\n\n> intro FIRST\n>\n> - item MARK\n> - item\n", "FIRST"),
    "quote in a list item": ("# A\n\n- one\n- two FIRST\n\n  > quoted MARK\n", "FIRST"),
    "drafting note": ("# A\n\n> [!DRAFTING] FIRST\n> note MARK\n", "FIRST"),
    "definition paragraph": ('# A\n\n"Thing" {{def: thing}} is FIRST MARK\n', "FIRST"),
}


@pytest.mark.parametrize("rule", sorted(TRIGGERS))
@pytest.mark.parametrize("container", sorted(CONTAINERS))
def test_a_finding_is_at_the_first_line_of_its_block(container: str, rule: str) -> None:
    body, first = CONTAINERS[container]
    source = HEAD + "\nBefore.\n\n" + body.replace("MARK", TRIGGERS[rule])
    expected = at(source, first)
    for fmt in ("html", "text"):
        result = render(source, format=fmt)
        assert lines_of(result, rule) == [expected], (container, fmt, result.diagnostics)


def test_a_finding_in_the_preamble_is_at_its_block() -> None:
    source = HEAD + "\nFirst preamble paragraph.\n\nSecond one\nhas a {{include: x.lgd}}.\n\n# A\n\nBody.\n"
    assert lines_of(render(source), "render-not-processed") == [at(source, "Second one")]


def test_a_finding_in_a_list_in_the_preamble_is_at_its_item() -> None:
    source = HEAD + "\n- one\n- two {{include: x.lgd}}\n\n# A\n"
    assert lines_of(render(source), "render-not-processed") == [at(source, "- two")]


def test_a_finding_in_a_section_title_is_at_its_heading() -> None:
    source = HEAD + "\nText.\n\n## Second {{include: x.lgd}}\n\nBody.\n"
    assert lines_of(render(source), "render-not-processed") == [at(source, "## Second")]


def test_a_finding_in_a_heading_under_the_none_scheme_is_reported_once_at_its_heading() -> None:
    # The `none` scheme reads each heading as text before the references are known.
    source = HEAD + "\n# One\n\n## Second {{include: x.lgd}}\n\nSee {{ref: second}}.\n"
    result = render(source, overrides={"numbering.scheme": "none"})
    assert lines_of(result, "render-not-processed") == [at(source, "## Second")]


def test_a_heading_inside_a_list_item_has_its_items_line() -> None:
    source = HEAD + "\n# A\n\n- one\n- ## two {{include: x.lgd}}\n"
    assert lines_of(render(source), "render-not-processed") == [at(source, "- ## two")]


def test_each_block_has_its_own_line() -> None:
    source = (HEAD + "\n# A\n\nOne [x](ms-msdt:one).\n\n- item [y](ms-msdt:two)\n\n> quote [z](ms-msdt:three)\n\n"
              "| h [w](ms-msdt:four) |\n|---|\n| c |\n\n# B\n\nLast [v](ms-msdt:five).\n")
    expected = [at(source, f"ms-msdt:{word}") for word in ("one", "two", "three", "four", "five")]
    assert lines_of(render(source), "render-link-refused") == expected


def test_a_table_is_at_its_header_row_whichever_cell_holds_the_finding() -> None:
    source = HEAD + "\n# A\n\n| a | b |\n|---|---|\n| x | y |\n| z | {{include: x.lgd}} |\n"
    assert lines_of(render(source), "render-not-processed") == [at(source, "| a | b |")]


# -- ref-not-enumerated, render-ref-ambiguous ------------------------------------------------


def test_ref_not_enumerated_is_at_its_block() -> None:
    source = (HEAD + "\n# A {#a}\n\n- first {#x}\n- second\n\nSee\nthe item {{ref: x}}.\n")
    result = render(source, format="text", overrides={"enumeration.enabled": False})
    assert lines_of(result, "ref-not-enumerated") == [at(source, "See")]


def test_ref_not_enumerated_in_a_list_item_is_at_the_item() -> None:
    source = (HEAD + "\n# A {#a}\n\n- first {#x}\n- second\n  see {{ref: x}}\n- third\n")
    result = render(source, format="text", overrides={"enumeration.enabled": False})
    assert lines_of(result, "ref-not-enumerated") == [at(source, "- second")]


def test_render_ref_ambiguous_is_at_its_block() -> None:
    source = HEAD + "\n# A\n\n- a {#x}\n\nPara.\n\n- b {#y}\n\n> Quoted\n> See {{ref: x}}.\n"
    assert lines_of(render(source, format="text"), "render-ref-ambiguous") == [at(source, "> Quoted")]


def test_ref_findings_are_the_block_not_the_directive() -> None:
    # A {{ref:}} paragraph is lifted by the validator and written back, so the
    # renderer names its block's first line, not the line the directive is on.
    source = HEAD + "\n# A\n\n- a {#x}\n\nPara.\n\n- b {#y}\n\nFirst line\nof a paragraph, See {{ref: x}}.\n"
    assert lines_of(render(source, format="text"), "render-ref-ambiguous") == [at(source, "First line")]


# -- render-term-limit, render-term-too-long ----------------------------------------------


def _chain(n: int) -> str:
    defs = [f'"T{i} {{{{term: t{i + 1}}}}} {{{{term: t{i + 1}}}}}" {{{{def: t{i}}}}}' for i in range(n)]
    defs.append(f'"T{n}" {{{{def: t{n}}}}}')
    return HEAD + "\n# About {{term: t0}}\n\nUsed: {{term: t0}}\n\n" + "\n\n".join(defs) + "\n"


@pytest.mark.parametrize("scheme", ["decimal", "none"])
def test_a_term_that_is_too_long_is_reported_at_its_definition(scheme: str) -> None:
    # The text is worked out at the definition (for a heading under `none`, too),
    # whichever reference asks for it first: t0 is used in the title and in the body.
    source = _chain(14)
    result = render(source, format="text", overrides={"numbering.scheme": scheme})
    found = [d for d in result.diagnostics if d.rule == "render-term-too-long"]
    assert found
    for d in found:
        identifier = d.message.split("'")[1]
        assert d.line == at(source, "{{def: " + identifier + "}}"), d


def _tangle(k: int) -> str:
    defs = [f'"T{i} {" ".join(f"{{{{term: t{j}}}}}" for j in range(k))}" {{{{def: t{i}}}}}' for i in range(k)]
    return HEAD + "\n# A\n\nUsed: {{term: t0}}\n\n" + "\n\n".join(defs) + "\n"


def test_the_term_limit_is_reported_at_a_line_of_a_definition() -> None:
    source = _tangle(12)
    result = render(source, format="text")
    [line] = lines_of(result, "render-term-limit")
    assert line is not None
    assert source.split("\n")[line - 1].startswith('"T')


# -- render-locale-fallback --------------------------------------------------------------


def test_a_duration_with_no_pattern_in_the_locale_is_at_its_block(monkeypatch: pytest.MonkeyPatch) -> None:
    from legaldown_render.resolve import values

    real = values._unit_patterns
    monkeypatch.setattr(values, "_unit_patterns",
                        lambda locale, unit, length: {} if locale.language == "cs" else real(locale, unit, length))
    source = ("---\ntitle: T\nlanguage: cs\n---\n\n# Lhůty\n\nPrvní\n\n- druhá {{duration: 5, unit=D}}\n\n"
              "Třetí {{duration: 6, unit=D}} a {{duration: 2, unit=MO}}.\n")
    result = render(source, format="text")
    # One per unit, at the first place it is used.
    assert [(d.line, d.message.split("'")[1]) for d in result.diagnostics if d.rule == "render-locale-fallback"] \
        == [(at(source, "druhá"), "duration-day"), (at(source, "Třetí"), "duration-month")]


def test_an_unknown_document_language_is_at_the_language_key() -> None:
    source = "---\ntitle: T\nlanguage: xx\nversion: '1'\n---\n\n# A\n"
    assert lines_of(render(source), "render-locale-fallback") == [at(source, "language:")]


def test_the_language_key_line_is_the_keys_not_the_frontmatters_first() -> None:
    source = "---\ntitle: T\nparties: []\nlanguage: xx\n---\n\n# A\n"
    assert lines_of(render(source), "render-locale-fallback") == [4]


# -- Frontmatter keys ---------------------------------------------------------------------


def test_a_finding_in_a_frontmatter_value_is_at_its_key() -> None:
    source = ('---\ntitle: "T {{include: t.lgd}}"\nsubtitle: "Sub {{include: s.lgd}}"\n'
              'version: "v {{include: v.lgd}}"\neffective_date: "e {{include: e.lgd}}"\n---\n\n# A\n')
    result = render(source, overrides={"title_block.version": True, "title_block.effective_date": True})
    found = {d.message.split("{{include: ")[1][:5]: d.line for d in result.diagnostics
             if d.rule == "render-not-processed"}
    assert found == {"t.lgd": 2, "s.lgd": 3, "v.lgd": 4, "e.lgd": 5}


def test_a_finding_in_an_attachment_title_is_at_its_title_key() -> None:
    source = ("---\ntitle: T\nattachments:\n  - id: first\n    title: One\n  - id: second\n"
              "    title: \"Two {{include: x.lgd}}\"\n---\n\n# A\n\nSee {{attach: first}} and {{attach: second}}.\n")
    assert lines_of(render(source), "render-not-processed") == [at(source, "title: \"Two")]


SIDES = """---
title: T
document_type: contract
sides:
  - name: seller
    label: "Seller {{include: l.lgd}}"
    parties:
      - name: acme
        legal_name: Acme
        type: legal_entity
        address: "Street {{include: a.lgd}}"
  - name: buyer
    label: Buyer
    parties:
      - name: bob
        legal_name: Bob
        type: natural_person
        address: "Road {{include: b.lgd}}"
---

# A
"""


def test_a_finding_in_a_party_or_a_side_is_at_its_key() -> None:
    result = render(SIDES)
    found = {d.message.split("{{include: ")[1][:5]: d.line for d in result.diagnostics
             if d.rule == "render-not-processed"}
    # A party's detail is at the party; a side's label, at its label key.
    assert found == {"l.lgd": at(SIDES, 'label: "Seller'), "a.lgd": at(SIDES, "name: acme"),
                     "b.lgd": at(SIDES, "name: bob")}


def test_signature_blocks_are_not_a_second_report() -> None:
    result = render(SIDES, overrides={"signatures.enabled": True})
    assert len(lines_of(result, "render-not-processed")) == 3


def test_key_line_is_general_and_none_without_a_source() -> None:
    from legaldown import Document, parse

    source = ("---\ntitle: T\namends:\n  file: ../a.lgd\n  title: X\nsupersedes: ../b.lgd\n"
              "translations:\n  cs: ../cs.lgd\nattachments:\n  - id: a\n    title: A\n    file: ../c.lgd\n---\n\n# A\n")
    document = parse(source)
    assert key_line(document, "amends", "file") == at(source, "file: ../a.lgd")
    assert key_line(document, "supersedes") == at(source, "supersedes:")
    assert key_line(document, "translations", "cs") == at(source, "cs:")
    assert key_line(document, "attachments", 0, "file") == at(source, "file: ../c.lgd")
    # A key that is not written is the nearest one above it that is.
    assert key_line(document, "amends", "date") == at(source, "amends:")
    assert key_line(parse("# A\n"), "language") is None
    assert key_line(Document(), "title") is None
    document.sections.clear()  # changed since it was parsed: no longer fits its source
    assert key_line(document, "title") is None


def test_a_document_without_a_source_has_no_lines() -> None:
    # The tree built from a dict has no source map; its findings name no line.
    from legaldown import Document

    document = Document.from_dict({"metadata": {"title": "T"}, "sections": [
        {"title": "A", "level": 1, "blocks": [{"kind": "paragraph", "text": "x {{include: p.lgd}}"}]}]})
    from legaldown import validate

    from legaldown_render.build import build_tree
    from legaldown_render.resolve import Formatter, parse_locale, resolve_tree
    from legaldown_render.style import load_style

    result = validate(document)
    tree = build_tree(document, result)
    assert tree.sections[0].line is None
    style = load_style(None)
    _, found = resolve_tree(tree, document, result, style, Formatter(parse_locale("en"), style.values))
    assert [d.line for d in found if d.rule == "render-not-processed"] == [None]


# -- The nodes -----------------------------------------------------------------------------


def test_the_tree_nodes_carry_their_lines() -> None:
    source = HEAD + ("\nPreamble.\n\n# A\n\nPara.\n\n- one\n  - nested\n- two\n\n> quote\n\n> [!DRAFTING] note\n\n"
                     "```\ncode\n```\n\n| a |\n|---|\n| b |\n")
    tree = render(source).tree
    assert tree.preamble[0].line == at(source, "Preamble")
    section = tree.sections[0]
    assert section.line == at(source, "# A")
    paragraph, bullets, quote, note, code, table = section.blocks
    assert [b.line for b in (paragraph, quote, note, code, table)] == [
        at(source, "Para."), at(source, "> quote"), at(source, "[!DRAFTING]"), at(source, "```"), at(source, "| a |")]
    assert [item.line for item in bullets.items] == [at(source, "- one"), at(source, "- two")]
    assert bullets.items[0].blocks[1].items[0].line == at(source, "- nested")


def test_a_line_is_not_part_of_equality() -> None:
    from legaldown_render.tree import Paragraph, Table

    assert Paragraph((), line=3) == Paragraph((), line=9)
    assert Table((), (), (), line=1) == Table((), (), ())


def test_lines_survive_resolving() -> None:
    source = HEAD + "\n# A\n\n> quote {{ref: a}}\n\n| h |\n|---|\n| {{ref: a}} |\n\n> [!DRAFTING] note\n"
    section = render(source).tree.sections[0]
    assert [block.line for block in section.blocks] == [at(source, "> quote"), at(source, "| h |"),
                                                        at(source, "[!DRAFTING]")]
    assert section.line == at(source, "# A")


# -- Repeats -------------------------------------------------------------------------------


def test_the_same_finding_on_two_lines_is_reported_once_at_the_first() -> None:
    source = HEAD + "\n# A\n\nOne ![a](/x.png).\n\nTwo ![b](/x.png).\n\nThree [l](ms-msdt:x) [m](ms-msdt:x)\n\nFour [l](ms-msdt:x)\n"
    result = render(source)
    assert lines_of(result, "path-not-relative") == [at(source, "One")]
    assert lines_of(result, "render-link-refused") == [at(source, "Three")]


def test_different_findings_of_one_rule_each_have_their_line() -> None:
    source = HEAD + "\n# A\n\nOne ![a](/x.png).\n\nTwo ![b](/y.png).\n"
    assert lines_of(render(source), "path-not-relative") == [at(source, "One"), at(source, "Two")]


def _with_validator_report(monkeypatch: pytest.MonkeyPatch, line: int | None, path: str = "https://x/p.png") -> None:
    from legaldown import Diagnostic
    from legaldown import validate as real_validate

    def validate(document, **kwargs):  # type: ignore[no-untyped-def]
        result = real_validate(document, **kwargs)
        result.diagnostics.append(Diagnostic(rule="path-not-relative", level="error", line=line,
                                             message=f"Image path '{path}' is not relative (§2.3)."))
        return result

    monkeypatch.setattr(api, "validate", validate)


def test_the_validators_report_of_a_path_at_its_line_covers_the_renderers(monkeypatch: pytest.MonkeyPatch) -> None:
    source = HEAD + "\n# A\n\n![a](https://x/p.png) ![b](/other.png)\n"
    _with_validator_report(monkeypatch, at(source, "![a]"))
    errors = [d for d in render(source).diagnostics if d.rule == "path-not-relative"]
    # The validator's for the path it named, ours for the other one at the same line.
    assert [(d.line, "/other.png" in d.message) for d in errors] == [(at(source, "![a]"), False),
                                                                      (at(source, "![a]"), True)]


def test_a_report_with_no_line_covers_the_path_wherever_it_is(monkeypatch: pytest.MonkeyPatch) -> None:
    source = HEAD + "\n# A\n\n![a](https://x/p.png)\n"
    _with_validator_report(monkeypatch, None)
    assert len([d for d in render(source).diagnostics if d.rule == "path-not-relative"]) == 1


def test_the_same_path_at_another_line_is_not_covered(monkeypatch: pytest.MonkeyPatch) -> None:
    source = HEAD + "\n# A\n\n![a](https://x/p.png)\n\n\nOther ![b](https://x/p.png)\n"
    _with_validator_report(monkeypatch, at(source, "![a]"))
    errors = [d for d in render(source).diagnostics if d.rule == "path-not-relative"]
    # The validator's line, and ours at the first image that is not the line it named.
    assert [d.line for d in errors] == [at(source, "![a]"), at(source, "Other")]


# -- The file ------------------------------------------------------------------------------

REFS = HEAD + "\n# A\n\nSee {{include: x.lgd}} and ![i](/etc/passwd).\n\n<div>raw</div>\n"


def test_findings_name_the_file_when_it_is_given() -> None:
    result = render(REFS, filename="contract.lgd")
    assert {d.rule for d in result.diagnostics} >= {"render-not-processed", "path-not-relative", "raw-html"}
    assert {d.file for d in result.diagnostics} == {"contract.lgd"}
    assert {d.file for d in render(REFS).diagnostics} == {""}


def test_render_file_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "deal.lgd"
    path.write_text(REFS, encoding="utf-8")
    found = {(d.rule, d.file) for d in render_file(path).diagnostics}
    assert ("render-not-processed", "deal.lgd") in found
    assert ("raw-html", "deal.lgd") in found  # the validator's, too
    # Options given: the file name is added to them; one already named stays.
    assert {d.file for d in render_file(path, RenderOptions(format="text")).diagnostics} == {"deal.lgd"}
    named = render_file(path, RenderOptions(filename="other.lgd"))
    assert {d.file for d in named.diagnostics} == {"other.lgd"}
    assert {d.file for d in render_file(path, format="text").diagnostics} == {"deal.lgd"}
    with pytest.raises(TypeError):
        render_file(path, RenderOptions(), format="text")


def test_the_locale_fallback_names_the_file() -> None:
    source = "---\ntitle: T\nlanguage: xx\n---\n\n# A\n"
    [found] = [d for d in render(source, filename="x.lgd").diagnostics if d.rule == "render-locale-fallback"]
    assert (found.file, found.line) == ("x.lgd", 3)


def test_assembly_names_the_file_too() -> None:
    template = ("---\ntitle: T\nquestions:\n  q:\n    type: text\n    prompt: Q\n---\n\n# A\n\n"
                "Hello {{placeholder: q}} {{include: x.lgd}}\n")
    result = render(template, answers={"q": "world"}, filename="t.lgd")
    assert {d.file for d in result.diagnostics} == {"t.lgd"}
    # The lines are those of the assembled document (the answers are filled in).
    [found] = [d for d in result.diagnostics if d.rule == "render-not-processed"]
    assert found.line is not None


def test_an_assembly_warning_names_the_file() -> None:
    template = ("---\ntitle: T\nquestions:\n  q:\n    type: text\n    prompt: Q\n---\n\n# A\n\n"
                "Hello {{placeholder: q}}\n")
    result = render(template, RenderOptions(answers={"q": "world", "zzz": "1"}, filename="t.lgd"))
    [unknown] = [d for d in result.diagnostics if d.rule == "answer-unknown"]
    assert unknown.file == "t.lgd"


def test_a_refused_assembly_names_the_file_in_every_diagnostic() -> None:
    template = ("---\ntitle: T\nquestions:\n  q:\n    type: text\n    prompt: Q\n---\n\n# A\n\n"
                "Hello {{placeholder: q}}\n")
    with pytest.raises(RenderRefused) as refused:
        render(template, RenderOptions(answers={"q": 5}, filename="t.lgd"))
    assert [d.rule for d in refused.value.diagnostics] == ["answer-invalid"]
    assert {d.file for d in refused.value.diagnostics} == {"t.lgd"}


# -- The command line ------------------------------------------------------------------------


def test_the_command_line_prints_the_file_and_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "deal.lgd"
    path.write_text(REFS, encoding="utf-8")
    assert main([str(path), "-f", "text", "-o", str(tmp_path / "out.txt")]) == 0
    lines = capsys.readouterr().err.splitlines()
    wanted = at(REFS, "See")
    assert f"{path}:{wanted}: warning: [render-not-processed] " in " ".join(lines)
    assert any(line.startswith(f"{path}:{wanted}: error: [path-not-relative] ") for line in lines)
    # The validator's own line is printed the same way.
    assert any(line.startswith(f"{path}:{at(REFS, '<div>')}: warning: [raw-html] ") for line in lines)
    # Each diagnostic once, with one location: no `file:line` twice over.
    assert not any(f"{path}:{wanted}:" in line.split(": ", 1)[1] for line in lines)
    assert len(lines) == len(set(lines))


def test_the_command_line_prints_a_frontmatter_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "x.lgd"
    path.write_text("---\ntitle: T\nlanguage: xx\n---\n\n# A\n", encoding="utf-8")
    assert main([str(path), "-o", str(tmp_path / "out.html")]) == 0
    assert f"{path}:3: info: [render-locale-fallback] " in capsys.readouterr().err


def test_standard_input_prints_its_name_and_line() -> None:
    done = run_cli_text("-", "-f", "text", stdin=REFS)
    assert f"<stdin>:{at(REFS, 'See')}: warning: [render-not-processed] " in done.stderr


def test_strict_prints_the_lines_it_refuses_with(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "x.lgd"
    path.write_text(REFS, encoding="utf-8")
    assert main([str(path), "--strict"]) == 1
    err = capsys.readouterr().err
    assert f"{path}:{at(REFS, 'See')}: error: [path-not-relative] " in err


# -- The cost of lines ---------------------------------------------------------------------

RICH = HEAD + """
Preamble one.

- pre item
  - pre nested

# One {#one}

Para.

1. first
   - nested a
     - deeper
   - nested b

     second block of nested b
2. second

> quote
>
> - in a quote

| a | b |
|---|---|
| x | y |

```
code
```

---

## Two

- x
- y
  1. z
"""


def test_the_builders_lines_are_those_of_line_of() -> None:
    from legaldown import parse, validate
    from legaldown.models import LIST_KINDS

    from legaldown_render.build import build_tree

    document = parse(RICH)
    tree = build_tree(document, validate(document))

    def items(block):  # type: ignore[no-untyped-def]
        for item in block.items:
            yield item
            for child in item.blocks:
                if hasattr(child, "items"):
                    yield from items(child)

    for section_index, (blocks, built) in enumerate(
            [(document.preamble, tree.preamble)] + [(s.blocks, t.blocks) for s, t in
                                                    zip(document.sections, tree.sections, strict=True)]):
        owner = None if section_index == 0 else section_index - 1
        if owner is not None:
            assert tree.sections[owner].line == document.line_of(owner)
        assert len(blocks) == len(built)
        for index, (block, node) in enumerate(zip(blocks, built, strict=True)):
            if block.kind in LIST_KINDS:
                found = [item.line for item in items(node)]
                assert found == [document.line_of(owner, index, n) for n in range(len(found))]
            elif block.kind != "rule":  # a rule has no line: nothing reports on it
                assert node.line == document.line_of(owner, index), (owner, index, block.kind)


def test_a_render_checks_the_document_against_its_source_a_constant_number_of_times(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # Document.line_of and SourceMap.key each re-read the whole document's shape
    # (positions.document_shape) to see it still fits its source, so asking for
    # every block's line, or every party's key, one by one is quadratic.
    import legaldown.positions as positions

    calls = []
    real = positions.document_shape
    monkeypatch.setattr(positions, "document_shape", lambda document: (calls.append(1), real(document))[1])
    front = ("---\ntitle: T\nsides:\n  - name: a\n    parties:\n      - name: p\n        legal_name: P\n"
             "        type: legal_entity\n  - name: b\n    parties:\n      - name: q\n        legal_name: Q\n"
             "        type: legal_entity\nattachments:\n  - id: x\n    title: X\n---\n")
    body = "# A\n\n" + "\n\n".join(f"Paragraph {i}." for i in range(2000))
    body += "\n\n- one\n- two\n  - three\n\n" + " ".join("{{party: p}} {{side: b}} {{attach: x}}" for _ in range(500))
    result = render(front + body + "\n", format="text")
    assert result.output
    assert len(calls) <= 3, len(calls)
