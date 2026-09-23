"""Rendering behaviour, asserted mostly on plain-text output (the resolution
oracle) and on HTML where markup is the point."""
from __future__ import annotations

import re

import pytest
from conftest import DOCUMENTS

from legaldown_render import DocumentError, RenderOptions, RenderRefused, render

FRONT = """---
title: T
sides:
  - name: providers
    label: Provider
    parties:
      - name: acme
        label: Acme
        type: legal_entity
        legal_name: Acme Corporation
  - name: clients
    parties:
      - name: beta
        type: legal_entity
        legal_name: Beta Inc.
---
"""


def text(body: str, **settings: object) -> str:
    return render(FRONT + body, format="text", **settings).output


def features(**settings: object) -> str:
    return render((DOCUMENTS / "features.lgd").read_text(encoding="utf-8"), **settings).output


# -- numbering and designations (§13.1–§13.3) --------------------------------

NUMBERED = """
# Scope {#scope}

## Termination {#termination}

The Provider may terminate if:

- payment is late {#late}
  - by thirty days {#late-thirty}
- the Client breaches {#breach}

See {{ref: termination}}, {{ref: late}}, and {{ref: late-thirty}}.
"""


@pytest.mark.parametrize(("scheme", "heading", "refs"), [
    ("decimal", "1.1 Termination", "See 1.1, 1.1(a), and 1.1(a)(i)."),
    ("legal-outline", "A. Termination", "See I.A, I.A(a), and I.A(a)(i)."),
    ("mixed", "(a) Termination", "See 1(a), 1(a)(a), and 1(a)(a)(i)."),
    ("none", "Termination", "See Termination, Termination (a), and Termination (a) (i)."),
])
def test_numbering_schemes(scheme: str, heading: str, refs: str) -> None:
    output = text(NUMBERED, overrides={"numbering.scheme": scheme})
    assert heading in output.splitlines()
    assert refs in output


def test_ordered_lists_are_renumbered() -> None:
    output = text("# A\n\n7. first\n7. second\n")
    assert "1. first\n2. second" in output


def test_ref_to_unenumerated_item_falls_back_with_warning() -> None:
    result = render(FRONT + NUMBERED, format="text", overrides={"enumeration.enabled": False})
    assert "See 1.1, 1.1, and 1.1." in result.output
    assert "- payment is late" in result.output
    assert "ref-not-enumerated" in {d.rule for d in result.diagnostics}


def test_paragraph_numbering_and_references() -> None:
    body = "# Fees {#fees}\n\nFirst.\n\nSecond. {#fees-second}\n\nSee {{ref: fees-second}}.\n"
    output = text(body, overrides={"paragraphs.numbered": True})
    assert "1.1 First." in output
    assert "1.2 Second." in output
    assert "1.3 See 1.2." in output


def test_custom_level_labels() -> None:
    output = text("# Fees\n\n## Late\n", overrides={
        "numbering.levels": [{"counter": "upper-roman", "label": "Article {n}", "ref": "{n}"}]})
    assert "Article I Fees" in output
    assert "I.1 Late" in output


# -- inline directives (§13.3–§13.5) --------------------------------------------


def test_labels_never_hide_failure_markers() -> None:
    output = text("# A\n\n{{party: nobody, label=Someone}} and {{term: nothing, label=Thing}}.\n")
    assert "[UNKNOWN PARTY: nobody] and [UNDEFINED: nothing]." in output


def test_party_and_side_display() -> None:
    output = text("# A\n\n{{party: acme}}, {{party: beta}}, {{side: clients}}, {{party: acme, label=the Provider}}.\n")
    assert "Acme, Beta Inc., Clients, the Provider." in output


def test_defined_terms_render_without_quotation_marks() -> None:
    body = '# D\n\n"Services" {{def: services}} means work. The {{term: services}} are paid.\n'
    result = render(FRONT + body)
    assert '<dfn class="ld-defined ld-style-bold" id="def:services">Services</dfn> means work' in result.output
    assert '<a class="ld-term ld-style-plain" href="#def:services">Services</a>' in result.output
    assert '"Services"' not in result.output


def test_directives_in_code_stay_literal() -> None:
    output = text("# A\n\nUse `{{ref: a}}` here.\n\n```\n{{term: x}}\n```\n")
    assert "Use {{ref: a}} here." in output
    assert "    {{term: x}}" in output


def test_every_failure_marker_in_the_features_document() -> None:
    output = features(format="text")
    for marker in ("[BROKEN REF: nowhere]", "[UNDEFINED: nothing]", "[INVALID DATE: 2026-13-45]",
                   "[UNKNOWN CURRENCY: XYZ]", "[UNKNOWN PARTY: nobody]", "[INVALID PARTY: Bad_Name]",
                   "[UNKNOWN SIDE: nobody]", "[INVALID DURATION: -1]", "[INVALID DURATION UNIT: M]",
                   "[INVALID FIELD: value]", "[UNKNOWN DIRECTIVE: trem]", "[UNKNOWN ATTACHMENT: schedule-z]",
                   "[INVALID PLACEHOLDER: fee]", "[NOT PROCESSED: include includes/extra.lgd]"):
        assert marker in output


def test_frontmatter_placeholders_render_as_blanks() -> None:
    output = render((DOCUMENTS / "template.lgd").read_text(encoding="utf-8"), format="text").output
    assert output.startswith("Consulting Agreement with [_____]")


# -- template view (§15.8) ------------------------------------------------------


def test_template_view() -> None:
    output = render((DOCUMENTS / "template.lgd").read_text(encoding="utf-8"), format="text").output
    assert "[Only if: non-solicit]" in output
    assert output.count("3. Dispute Resolution") == 2  # alternatives share a number
    assert "[a competent court / a competent court or an emergency arbitrator]" in output
    assert "> [Drafting note]" in output
    assert "(b) [Only if: !non-solicit] nothing in Section 3 limits this item" in output


def test_template_labels_follow_the_document_language() -> None:
    source = (DOCUMENTS / "template.lgd").read_text(encoding="utf-8").replace("language: en", "language: cs")
    output = render(source, format="text").output
    assert "[Pouze pokud: non-solicit]" in output
    assert "> [Poznámka pro zpracovatele]" in output


# -- HTML safety and structure ---------------------------------------------------


def test_raw_html_is_never_emitted() -> None:
    result = render(FRONT + '# A\n\n<script>alert(1)</script>\n\nText <b onclick="x">bold</b>.\n')
    assert "<script>alert" not in result.output
    assert "onclick" not in result.output
    assert "raw-html" in {d.rule for d in result.diagnostics}


def test_text_is_escaped() -> None:
    output = render(FRONT + "# A & B\n\nFees < 5 & more, \\<b\\> shown as text.\n").output
    assert "A &amp; B" in output
    assert "Fees &lt; 5 &amp; more, &lt;b&gt; shown as text." in output


def test_unsafe_links_are_not_links() -> None:
    output = features()
    assert 'href="https://example.com"' in output
    assert "javascript:" not in re.findall(r'href="([^"]*)"', output)


def test_every_internal_link_has_a_target() -> None:
    for name in ("features", "template"):
        output = render((DOCUMENTS / f"{name}.lgd").read_text(encoding="utf-8")).output
        ids = re.findall(r' id="([^"]+)"', output)
        assert len(ids) == len(set(ids)), "duplicate ids"
        for target in re.findall(r'href="#([^"]+)"', output):
            assert target in ids, target


def test_links_never_nest() -> None:
    output = render(FRONT + "# A {#a}\n\n[see {{ref: a}}](https://example.com)\n").output
    assert '<a href="https://example.com">see <span class="ld-ref">1</span></a>' in output


def test_directives_in_alt_text_are_resolved() -> None:
    output = render(FRONT + "# A {#a}\n\n![Figure for {{ref: a}}](figure.png)\n").output
    assert '<img src="figure.png" alt="Figure for 1">' in output


def test_none_scheme_designates_by_resolved_heading_text() -> None:
    output = text("# Payment to {{party: acme}} {#pay}\n\nSee {{ref: pay}}.\n", overrides={"numbering.scheme": "none"})
    assert "See Payment to Acme." in output


def test_fragment_output() -> None:
    output = render(FRONT + "# A\n", standalone=False).output
    assert output.startswith('<article class="ld-document">')
    assert "<style>" not in output


def test_output_is_deterministic() -> None:
    assert features() == features()


# -- options, errors ---------------------------------------------------------------


def test_strict_refuses_documents_with_errors() -> None:
    with pytest.raises(RenderRefused) as caught:
        render(FRONT + "# A\n\n{{ref: nowhere}}\n", strict=True)
    assert "ref-broken" in {d.rule for d in caught.value.diagnostics}
    render(FRONT + "# A\n\nFine.\n", strict=True)


def test_locale_option_and_language_hint() -> None:
    body = "# A\n\n{{date: 2026-06-01}}\n"
    assert "1. června 2026" in text(body, locale="cs-CZ")
    assert "June 1, 2026" in text(body)
    czech = FRONT.replace("title: T", "title: T\nlanguage: cs")
    assert "1. června 2026" in render(czech + body, format="text").output


def test_unreadable_document() -> None:
    with pytest.raises(DocumentError):
        render("---\n- a list\n---\n\n# A\n")


def test_options_object_and_keywords_are_exclusive() -> None:
    with pytest.raises(TypeError):
        render(FRONT, RenderOptions(), format="text")


# -- regressions from code review -------------------------------------------------


def test_disguised_javascript_urls_are_not_links() -> None:
    from legaldown_render.writers.html import is_safe_href

    for href in ("java\tscript:alert(1)", "jav\nascript:x", "\x01javascript:x", " JAVASCRIPT:x", "data:text/html,x"):
        assert not is_safe_href(href), repr(href)
    for href in ("https://example.com", "#section", "attachments/a.pdf", "mailto:a@example.com"):
        assert is_safe_href(href), href
    source = FRONT.replace("title: T", 'title: T\nattachments:\n  - id: annex\n    title: Annex\n    file: "java\\tscript:alert(1)"')
    output = render(source + "# A\n\nSee {{attach: annex}}.\n", overrides={"attachments.render": "omit"}).output
    assert "script:alert" not in "".join(re.findall(r'href="([^"]*)"', output))
    assert '<span class="ld-value ld-attach">Annex</span>' in output


def test_emphasis_around_a_defined_term_stays_paired() -> None:
    output = render(FRONT + '# D\n\n**"Notice" {{def:}}** means a notice.\n\n*"Fee" {{def:}} means the fee.*\n',
                    standalone=False).output
    assert '<strong><dfn class="ld-defined ld-style-bold" id="def:notice">Notice</dfn></strong> means a notice.' in output
    assert '<em><dfn class="ld-defined ld-style-bold" id="def:fee">Fee</dfn> means the fee.</em>' in output
    assert "*" not in re.sub(r"<[^>]+>", "", output.split("<h2", 1)[1])


def test_alternative_sections_restart_their_subsection_numbers() -> None:
    source = FRONT.replace("title: T", TEMPLATE_QUESTIONS) + (
        "# Disputes {#d when=forum:courts}\n\n## Sub one\n\n## Sub two\n\n"
        "# Disputes {#d when=forum:arb}\n\n## Sub alt {#sub-alt}\n\nSee {{ref: sub-alt}}.\n")
    output = render(source, format="text").output
    assert "1.1 Sub alt" in output
    assert "See 1.1." in output


def test_alternative_paragraphs_and_items_share_a_number() -> None:
    source = FRONT.replace("title: T", TEMPLATE_QUESTIONS) + (
        "# A\n\nIntro.\n\nCourts. {#p when=forum:courts}\n\nArbitration. {#p when=forum:arb}\n\nAfter.\n\n"
        "- first\n- by courts {#i when=forum:courts}\n- by arbitration {#i when=forum:arb}\n- last\n")
    output = render(source, format="text", overrides={"paragraphs.numbered": True}).output
    assert "1.2 Courts." in output
    assert "1.2 Arbitration." in output
    assert "1.3 After." in output
    assert "(b) [Only if: forum:courts] by courts" in output
    assert "(b) [Only if: forum:arb] by arbitration" in output
    assert "(c) last" in output


def test_source_holding_sentinel_characters_renders_them() -> None:
    body = "# A\n\nx 5 y and `` in code, {{party: acme}}.\n"
    output = render(FRONT + body, format="text").output
    assert "x 5 y and  in code, Acme." in output


def test_item_opening_with_a_nested_list_keeps_its_label_first() -> None:
    output = text("# A\n\n1. first\n2. - - deep\n")
    assert "2.\n    (i)\n        (A) deep" in output


def test_an_unresolved_node_is_an_internal_error() -> None:
    from dataclasses import replace

    from legaldown_render import InternalError
    from legaldown_render.tree import DirectiveSource, Paragraph, assert_resolved

    result = render(FRONT + "# A\n\n{{party: acme}}\n", format="text")
    directive = next(iter(__import__("legaldown").iter_directives("{{party: acme}}")))
    section = replace(result.tree.sections[0], blocks=(Paragraph((DirectiveSource(directive),)),))
    with pytest.raises(InternalError):
        assert_resolved(replace(result.tree, sections=(section,)))


TEMPLATE_QUESTIONS = """title: T
questions:
  forum:
    type: choice
    choices:
      courts: Courts
      arb: Arbitration"""
