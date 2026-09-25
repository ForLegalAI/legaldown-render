"""Rendering behaviour, asserted mostly on plain-text output (the resolution
oracle) and on HTML where markup is the point."""
from __future__ import annotations

import re

import pytest
from conftest import DOCUMENTS

from legaldown_render import DocumentError, RenderOptions, RenderRefused, render
from legaldown_render.style import StyleError

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
- the Client breaches {#breach}

See {{ref: termination}}, {{ref: late}}, and {{ref: breach}}.
"""


@pytest.mark.parametrize(("scheme", "heading", "refs"), [
    ("decimal", "1.1 Termination", "See 1.1, 1.1(a), and 1.1(b)."),
    ("legal-outline", "A. Termination", "See I.A, I.A(a), and I.A(b)."),
    ("mixed", "(a) Termination", "See 1(a), 1(a)(a), and 1(a)(b)."),
    ("none", "Termination", "See Termination, Termination (a), and Termination (b)."),
])
def test_numbering_schemes(scheme: str, heading: str, refs: str) -> None:
    output = text(NUMBERED, overrides={"numbering.scheme": scheme})
    assert heading in output.splitlines()
    assert refs in output


def test_ordered_lists_are_renumbered() -> None:
    output = text("# A\n\n7. first\n7. second\n")
    assert "1. first\n2. second" in output


def test_nested_lists_render_flat_until_the_validator_keeps_them() -> None:
    # One parser: lists are the validator's, which keeps one level of items
    # (ForLegalAI/legaldown-validator#14).
    output = text("# A\n\n- one {#one}\n  - nested {#nested}\n- two\n\nSee {{ref: nested}}.\n")
    assert "(a) one\n(b) nested\n(c) two" in output
    assert "See 1(b)." in output


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


@pytest.mark.parametrize(("language", "condition", "note"), [
    ("de", "[Nur wenn: non-solicit]", "[Bearbeitungshinweis]"),
    ("fr", "[Uniquement si\u00a0: non-solicit]", "[Note de rédaction]"),
    ("pl", "[Tylko jeżeli: non-solicit]", "[Uwaga redakcyjna]"),
    ("sk", "[Iba ak: non-solicit]", "[Poznámka pre spracovateľa]"),
])
def test_built_in_labels_for_more_languages(language: str, condition: str, note: str) -> None:
    source = (DOCUMENTS / "template.lgd").read_text(encoding="utf-8").replace("language: en", f"language: {language}")
    output = render(source, format="text").output
    assert condition in output
    assert note in output


def test_every_built_in_language_sets_every_label() -> None:
    from dataclasses import fields

    from legaldown_render.style.labels import BUILTIN_LABELS
    for language, labels in BUILTIN_LABELS.items():
        for item in fields(labels):
            assert getattr(labels, item.name), f"{language}: {item.name} is not set"
        assert "{condition}" in labels.condition and "{file}" in labels.attachment_file, language


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


def test_contents_lists_sections_to_their_depth_then_attachments() -> None:
    body = "# One {#one}\n\nText.\n\n## Sub {#sub}\n\n### Deep\n\n# Two with **bold**\n"
    assert "Contents" not in text(body)
    output = text(body, overrides={"contents.enabled": True})
    assert "Contents\n1. One\n    1.1 Sub\n2. Two with bold\n\n" in output
    output = text(body, overrides={"contents.enabled": True, "contents.depth": 3})
    assert "        1.1.1 Deep" in output


def test_contents_in_html_links_each_entry_without_repeating_anchors() -> None:
    body = "# One {#one}\n\n\"Services\" {{def: services}} means work.\n\n# Uses {{term: services}}\n"
    html = render(FRONT + body, standalone=False, overrides={"contents.enabled": True}).output
    nav = html[html.index("<nav"):html.index("</nav>")]
    assert '<a href="#one"><span class="ld-number">1.</span> <span class="ld-contents-text">One</span></a>' in nav
    assert "<dfn" not in nav and nav.count("<a ") == 2
    assert 'id="' not in nav


def test_contents_label_follows_the_language_and_marks_conditions() -> None:
    output = render((DOCUMENTS / "template.lgd").read_text(encoding="utf-8"), format="text",
                    overrides={"contents.enabled": True}).output
    contents = output[output.index("Contents\n"):].split("\n\n", 1)[0]
    assert "2. Non-Solicitation [Only if: non-solicit]" in contents
    assert text("# A\n", overrides={"contents.enabled": True, "labels.contents": "Obsah"}).count("Obsah") == 1


def test_contents_follow_the_numbering_depth_not_the_heading_level() -> None:
    body = "# A\n\n### B\n\n## C\n"
    assert "Contents\n1. A\n    1.2 C\n\n" in text(body, overrides={"contents.enabled": True})
    assert "Contents\n1. A\n        1.1.1 B\n    1.2 C\n\n" in text(
        body, overrides={"contents.enabled": True, "contents.depth": 3})


@pytest.mark.parametrize("body", [
    "# A\n\n### B\n\n## C\n\n### D\n",
    "## A\n\n### B\n\n## C\n",
    "# A\n\n###### B\n\n# C\n",
])
def test_section_numbers_are_the_validators(body: str) -> None:
    from legaldown import parse_document, validate_document
    result = render(FRONT + body, format="text")
    expected = [entry.number for entry in validate_document(parse_document(FRONT + body)).sections]
    assert [section.designation for section in result.tree.sections] == expected


def test_contents_group_attachments_under_their_heading() -> None:
    output = render((DOCUMENTS / "features.lgd").read_text(encoding="utf-8"), format="text",
                    overrides={"contents.enabled": True}).output
    contents = output[output.index("Contents\n"):].split("\n\n", 1)[0]
    assert contents.endswith("\nAttachments\n    Schedule A: Service Description\n    Exhibit 1: Price List")


def test_contents_heading_reads_as_in_the_body_without_links() -> None:
    body = "# See {{ref: nowhere}} and [site](https://example.com/x)\n"
    html = render(FRONT + body, standalone=False, overrides={"contents.enabled": True}).output
    nav = html[html.index("<nav"):html.index("</nav>")]
    assert 'class="ld-failure"' in nav
    assert "example.com" not in nav


def test_an_empty_contents_label_leaves_out_the_heading() -> None:
    settings = {"contents.enabled": True, "labels.contents": ""}
    html = render(FRONT + "# A\n", standalone=False, overrides=settings).output
    assert '<nav class="ld-contents">\n<ol' in html
    output = text("# A\n", overrides=settings)
    assert "\n\n\n" not in output and output.count("1. A") == 2


def test_contents_css_only_when_enabled() -> None:
    assert ".ld-contents" not in render(FRONT + "# A\n").output
    assert ".ld-contents-list" in render(FRONT + "# A\n", overrides={"contents.enabled": True}).output


@pytest.mark.parametrize("depth", [True, 2.0, 0, 6])
def test_contents_depth_must_be_a_level(depth: object) -> None:
    with pytest.raises(StyleError, match="contents.depth"):
        render(FRONT + "# A\n", overrides={"contents.depth": depth})


def test_representatives_are_labelled_and_french_labels_space_their_colons() -> None:
    source = (DOCUMENTS / "features.lgd").read_text(encoding="utf-8")
    assert "Represented by: John Smith" in render(source, format="text").output
    relabelled = render(source, format="text", overrides={"labels.represented_by": "Acting for"}).output
    assert "Acting for: John Smith" in relabelled
    french = render(source.replace("language: en", "language: fr"), format="text").output
    assert "Représentée par\xa0: John Smith" in french
    assert "Nom\xa0: " in french and "\nLieu\xa0:\n" in french
    assert "Date d'effet\xa0: " in french


def test_a_representative_without_a_name_or_title_has_no_stray_comma() -> None:
    front = FRONT.replace("        legal_name: Acme Corporation\n",
                          "        legal_name: Acme Corporation\n        representatives:\n          - title: Director\n", 1)
    output = render(front + "# A\n", format="text").output
    assert "Represented by: Director\n" in output


def test_an_empty_colon_label_is_kept() -> None:
    source = (DOCUMENTS / "features.lgd").read_text(encoding="utf-8")
    assert "Represented byJohn Smith" in render(source, format="text", overrides={"labels.colon": ""}).output


def test_contents_without_an_attachments_label_lists_attachments_at_the_top() -> None:
    output = render((DOCUMENTS / "features.lgd").read_text(encoding="utf-8"), format="text",
                    overrides={"contents.enabled": True, "labels.attachments": ""}).output
    contents = output[output.index("Contents\n"):].split("\n\n", 1)[0]
    assert contents.endswith("4. Broken References\nSchedule A: Service Description\nExhibit 1: Price List")


def test_contents_link_the_attachments_heading_and_never_repeat_a_definition() -> None:
    html = render((DOCUMENTS / "features.lgd").read_text(encoding="utf-8"), standalone=False,
                  overrides={"contents.enabled": True}).output
    assert '<a href="#ld:attachments"><span class="ld-contents-text">Attachments</span></a>' in html
    assert '<section class="ld-attachments ld-separator-page-break" id="ld:attachments">' in html
    html = render(FRONT + '# The "Term" {{def: term}}\n', standalone=False, overrides={"contents.enabled": True}).output
    nav = html[html.index("<nav"):html.index("</nav>")]
    assert "<dfn" not in nav and '<span class="ld-defined ld-style-bold">Term</span>' in nav
    assert html.count("<dfn") == 1
    html = render(FRONT + '# The "*Big* Term" {{def: term}}\n', standalone=False,
                  overrides={"contents.enabled": True}).output
    nav = html[html.index("<nav"):html.index("</nav>")]
    assert '<span class="ld-defined ld-style-bold"><em>Big</em> Term</span>' in nav


def test_an_identifier_with_a_colon_never_becomes_an_anchor() -> None:
    source = (DOCUMENTS / "features.lgd").read_text(encoding="utf-8")
    html = render(source + "\nText. {#ld:attachments}\n\n- item {#def:services}\n", standalone=False).output
    assert html.count('id="ld:attachments"') == 1
    assert html.count('id="def:services"') == 1


def test_an_empty_attachments_label_leaves_out_the_heading() -> None:
    html = render((DOCUMENTS / "features.lgd").read_text(encoding="utf-8"), standalone=False,
                  overrides={"labels.attachments": ""}).output
    assert "ld-attachments-heading" not in html


def test_a_representative_with_nothing_to_show_has_no_row() -> None:
    front = FRONT.replace("        legal_name: Acme Corporation\n",
                          "        legal_name: Acme Corporation\n        representatives:\n          - {}\n", 1)
    assert "Represented by" not in render(front + "# A\n", format="text").output


def test_a_colon_ending_in_a_no_break_space_keeps_it_after_date_and_place() -> None:
    output = render((DOCUMENTS / "features.lgd").read_text(encoding="utf-8"), format="text",
                    overrides={"labels.colon": " \u2013\xa0"}).output
    assert "\nDate \u2013\xa0\n" in output and "\nPlace \u2013\xa0\n" in output


TEMPLATE = (DOCUMENTS / "template.lgd").read_text(encoding="utf-8")
ANSWERS = {"client-name": "Beta Ltd", "fee": "5000.00", "non-solicit": False, "forum": "arbitration"}


def test_a_template_with_answers_renders_its_assembled_document() -> None:
    result = render(TEMPLATE, answers=ANSWERS, format="text")
    assert not result.tree.is_template
    assert "Consulting Agreement with Beta Ltd" in result.output
    assert "Non-Solicitation" not in result.output
    assert "Disputes are resolved by arbitration." in result.output
    assert "courts" not in result.output
    assert "a competent court or an emergency arbitrator" in result.output
    assert "Drafting note" not in result.output and "partner in charge" not in result.output
    assert "Only if" not in result.output


def test_assembly_warnings_are_reported_with_the_rest() -> None:
    result = render(TEMPLATE, answers={**ANSWERS, "stray": "x"}, format="text")
    assert "answer-unknown" in {d.rule for d in result.diagnostics}
    assert result.ok


@pytest.mark.parametrize(("answers", "rule"), [
    ({key: value for key, value in ANSWERS.items() if key != "forum"}, "answer-missing"),
    ({**ANSWERS, "forum": "mediation"}, "answer-invalid"),
])
def test_assembly_errors_refuse_the_render(answers: dict, rule: str) -> None:
    with pytest.raises(RenderRefused, match="Assembly refused") as caught:
        render(TEMPLATE, answers=answers)
    assert rule in {d.rule for d in caught.value.diagnostics}


def test_a_template_with_errors_is_not_assembled() -> None:
    broken = TEMPLATE.replace("{{ref: disputes}}", "{{ref: nowhere}}")
    with pytest.raises(RenderRefused, match="only a template without errors") as caught:
        render(broken, answers=ANSWERS)
    assert "ref-broken" in {d.rule for d in caught.value.diagnostics}


def test_unanswered_blanks_stay_and_the_final_check_catches_them() -> None:
    answers = {key: value for key, value in ANSWERS.items() if key != "fee"}
    result = render(TEMPLATE, answers=answers, format="text", final=True)
    assert "[_____]" in result.output
    assert "placeholder-unfilled" in {d.rule for d in result.diagnostics}
    assert "template-construct-present" not in {d.rule for d in result.diagnostics}


def test_final_check_reports_blanks_and_template_constructs() -> None:
    body = "# A\n\nPay {{placeholder: fee, type=text}}.\n\n> [!DRAFTING]\n> Check the fee.\n"
    assert not {"placeholder-unfilled", "template-construct-present"} & {d.rule for d in render(FRONT + body).diagnostics}
    result = render(FRONT + body, final=True)
    assert {"placeholder-unfilled", "template-construct-present"} <= {d.rule for d in result.diagnostics}
    assert not result.ok
    with pytest.raises(RenderRefused):
        render(FRONT + body, final=True, strict=True)
    render(FRONT + "# A\n\nFine.\n", final=True, strict=True)


def test_locale_option_and_language_hint() -> None:
    body = "# A\n\n{{date: 2026-06-01}}\n"
    assert "1. června 2026" in text(body, locale="cs-CZ")
    assert "June 1, 2026" in text(body)
    czech = FRONT.replace("title: T", "title: T\nlanguage: cs")
    assert "1. června 2026" in render(czech + body, format="text").output


def test_unreadable_document() -> None:
    with pytest.raises(DocumentError):
        render("---\ntitle: [unclosed\n---\n\n# A\n")


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
    body = "# A\n\nx \ue0005\ue001 y and `\ue000` in code, {{party: acme}}.\n"
    output = render(FRONT + body, format="text").output
    assert "x \ue0005\ue001 y and \ue000 in code, Acme." in output


def test_item_opening_with_a_nested_list_keeps_its_label_first() -> None:
    # The text writer, on a tree with nesting (as the validator will give
    # once it keeps nested lists): the item's label comes first.
    from legaldown_render.tree import List, ListItem, Paragraph, RenderTree, Section, Text
    from legaldown_render.writers import TextWriter

    deep = List(False, (ListItem((Paragraph((Text("deep"),)),), label="(A)"),), enumerated=True)
    middle = List(False, (ListItem((deep,), label="(i)"),), enumerated=True)
    outer = List(True, (ListItem((Paragraph((Text("first"),)),), label="1."), ListItem((middle,), label="2.")),
                 enumerated=True)
    tree = RenderTree((), (), "en", (), (Section(1, (Text("A"),), "a", "", (outer,), label="1."),))
    assert "1. first\n2.\n    (i)\n        (A) deep" in TextWriter().write(tree)


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


# -- regressions from the second code review ---------------------------------------


def test_sentinels_written_as_entities_are_plain_text() -> None:
    body = "# A\n\nParty {{placeholder: x}} and &#xE000;9&#xE001; here, &#xE000;0&#xE001; too.\n"
    output = render(FRONT + body, format="text").output
    assert "Party [_____] and \ue0009\ue001 here, \ue0000\ue001 too." in output


def test_sentinels_percent_encoded_in_urls_stay_as_written() -> None:
    body = "# A\n\n{{placeholder: x}} [link](http://a/%EE%80%800%EE%80%81) [y](&#xE000;1&#xE001;)\n"
    output = render(FRONT + body).output
    assert 'href="http://a/%EE%80%800%EE%80%81"' in output
    assert "placeholder" not in "".join(re.findall(r'href="([^"]*)"', output))


def test_emphasis_wrapping_only_the_term_is_left_to_the_style() -> None:
    body = '# D\n\n*"Fee"* {{def:}} means the fee, and "Tax"\xa0{{def:}} means tax.\n'
    output = render(FRONT + body, standalone=False, overrides={"definitions.style": "plain"}).output
    assert '<dfn class="ld-defined ld-style-plain" id="def:fee">Fee</dfn> means the fee' in output
    assert '<dfn class="ld-defined ld-style-plain" id="def:tax">Tax</dfn> means tax.' in output
    assert "<em>" not in output


def test_directives_in_link_titles_are_resolved() -> None:
    output = render(FRONT + '# A\n\n[site](https://example.com "{{party: acme}}")\n').output
    assert '<a href="https://example.com" title="Acme">site</a>' in output


def test_only_adjacent_alternatives_share_a_number() -> None:
    # As the validator numbers sections: an alternative follows its sibling.
    source = FRONT.replace("title: T", TEMPLATE_QUESTIONS) + (
        "# A\n\nCourts. {#p when=forum:courts}\n\nMid.\n\nArb. {#p when=forum:arb}\n\nAfter.\n")
    output = render(source, format="text", overrides={"paragraphs.numbered": True}).output
    for line in ("1.1 Courts.", "1.2 Mid.", "1.3 Arb.", "1.4 After."):
        assert line in output


def test_units_that_can_appear_together_do_not_share_a_number() -> None:
    source = FRONT.replace("title: T", TEMPLATE_QUESTIONS) + (
        "# One {#d when=forum:courts}\n\n# Two {#d when=forum:courts}\n")
    output = render(source, format="text").output
    assert "1. One" in output
    assert "2. Two" in output


# -- regressions from the third code review -------------------------------------------

THREE_WAY = """title: T
questions:
  q:
    type: choice
    choices: {a: A, b: B, c: C}
  flag:
    type: boolean"""


def test_underscore_emphasis_closes_after_a_defined_term() -> None:
    output = render(FRONT + '# D\n\nAnd _the "Fee"_ {{def: fee}} applies.\n', standalone=False).output
    assert '<em>the <dfn class="ld-defined ld-style-bold" id="def:fee">Fee</dfn></em> applies.' in output


def test_definitions_in_titles_and_alt_text_do_not_take_the_anchor() -> None:
    body = ("# D\n\n[x](https://a.example 'about \"Fee\" {{def: fee}}') ![\"Tax\" {{def: tax}} chart](c.png) "
            "{{term: fee}} {{term: tax}}\n\n\"Fee\" {{def: fee}} means the fee.\n")
    output = render(FRONT + body, standalone=False).output
    assert 'title="about Fee"' in output
    assert 'alt="Tax chart"' in output
    ids = set(re.findall(r' id="([^"]+)"', output))
    assert "def:fee" in ids
    for target in re.findall(r'href="#([^"]+)"', output):
        assert target in ids


def test_alternatives_use_their_enclosing_presence() -> None:
    source = FRONT.replace("title: T", THREE_WAY) + (
        "# S {#s when=!q:c}\n\nOne. {#p when=!q:a}\n\nTwo. {#p when=!q:b}\n\n"
        "## Child {#x when=!q:a}\n\n## Child {#x when=!q:b}\n")
    output = render(source, format="text", overrides={"paragraphs.numbered": True}).output
    assert "1.1 One." in output
    assert "1.1 Two." in output
    assert output.count("1.1 Child") == 2


def test_invalid_conditions_are_never_alternatives() -> None:
    source = FRONT.replace("title: T", THREE_WAY) + "# A\n\nOne. {#p when=flag}\n\nTwo. {#p when=flag:yes}\n"
    output = render(source, format="text", overrides={"paragraphs.numbered": True}).output
    assert "1.1 One." in output
    assert "1.2 Two." in output


def test_a_shared_number_excludes_every_unit_holding_it() -> None:
    # B (q:b) and C (!q:a) can both appear, so C does not join A and B.
    source = FRONT.replace("title: T", THREE_WAY) + (
        "# A\n\n- first {#i when=q:a}\n- second {#i when=q:b}\n- third {#i when=!q:a}\n")
    output = render(source, format="text").output
    assert "(a) [Only if: q:a] first" in output
    assert "(a) [Only if: q:b] second" in output
    assert "(b) [Only if: !q:a] third" in output


def test_section_numbers_agree_with_the_validator() -> None:
    from legaldown import parse_document, validate_document

    source = FRONT.replace("title: T", THREE_WAY) + (
        "# A {#x when=q:a}\n\n# B\n\n# C {#x when=q:b}\n\n# D {#d when=q:a}\n\n# E {#d when=q:b}\n")
    result = render(source, format="text")
    expected = [entry.number for entry in validate_document(parse_document(source)).sections]
    assert [section.designation for section in result.tree.sections] == expected == ["1", "2", "3", "4", "4"]


# -- regressions from the fourth code review -------------------------------------------


def test_preamble_condition_is_literal_outside_a_template() -> None:
    result = render(FRONT + "Intro text {when=q}\n\n# A\n\nBody.\n", format="text")
    assert "Intro text {when=q}" in result.output
    assert "[Only if" not in result.output
    assert result.tree.is_template is False


def test_preamble_condition_applies_in_a_template() -> None:
    source = FRONT.replace("title: T", THREE_WAY) + "Intro text {when=flag}\n\n# A\n\nBody.\n"
    output = render(source, format="text").output
    assert "[Only if: flag] Intro text" in output


def test_preamble_list_item_markers_are_literal() -> None:
    source = FRONT.replace("title: T", THREE_WAY) + "- item one {when=flag}\n- item two {#two}\n\n# A\n"
    output = render(source, format="text").output
    assert "(a) item one {when=flag}" in output
    assert "(b) item two {#two}" in output


def test_hidden_lead_character_in_the_source_is_plain_text() -> None:
    body = "# A\n\n\u2e31{{include: x.lgd}} {#p}\n\nSee {{ref: p}}.\n"
    output = render(FRONT + body, format="text").output
    assert "See 1(a)." not in output and "See 1." in output
    assert "\u2e31[NOT PROCESSED: include x.lgd]" in output


def test_level_six_headings_are_numbered_as_the_validator_numbers_them() -> None:
    from legaldown import parse_document, validate_document

    source = FRONT + "# A\n\n## B\n\n### C\n\n#### D\n\n##### E\n\n###### F\n"
    result = render(source, format="text")
    expected = [entry.number for entry in validate_document(parse_document(source)).sections]
    assert [section.designation for section in result.tree.sections] == expected


def test_none_scheme_designation_skips_nested_references() -> None:
    body = "# Later *see {{ref: b}}* {#c}\n\nSee {{ref: c}}.\n\n# B {#b}\n"
    output = text(body, overrides={"numbering.scheme": "none"})
    assert "See Later see." in output
    assert "BROKEN" not in output


def test_defined_term_nested_in_a_heading_keeps_its_anchor() -> None:
    # (The validator does not register a definition in a heading, so a
    # {{term:}} to it is term-undefined; what matters here is the anchor.)
    body = '# *The "Buyer" {{def:}} and friends* {#a}\n\nSee {{ref: a}}.\n'
    output = render(FRONT + body, standalone=False, overrides={"numbering.scheme": "none"}).output
    assert '<dfn class="ld-defined ld-style-bold" id="def:buyer">Buyer</dfn>' in output
    assert '<a class="ld-ref" href="#a">The Buyer and friends</a>' in output


def test_definition_in_a_link_inside_alt_text_takes_no_anchor() -> None:
    body = '# A\n\n![pic [the "Fee" {{def:}}](u)](i.png) and {{term: fee}}.\n'
    output = render(FRONT + body, standalone=False).output
    assert 'id="def:fee"' not in output
    assert 'href="#def:fee"' not in output
    assert 'alt="pic the Fee"' in output


# -- regressions from the fifth code review --------------------------------------------


def test_a_malformed_include_is_not_an_include_paragraph() -> None:
    output = text('# A\n\n{{include: "x}} {#pp}\n\nSee {{ref: pp}}.\n')
    assert "See 1." in output
    result = render(FRONT + '{{include: "x}} {when=flag}\n\nIntro {when=flag}\n\n# A\n', format="text")
    assert result.tree.is_template is False
    assert "Intro {when=flag}" in result.output


def test_a_choose_in_frontmatter_makes_a_template() -> None:
    source = FRONT.replace("title: T", 'title: "A {{choose: flag, true=x, false=y}}"')
    result = render(source + "Intro {when=flag}\n\n# A\n", format="text")
    assert result.tree.is_template is True
    assert "Intro {when=flag}" not in result.output


def test_the_first_definition_in_document_order_keeps_the_anchor() -> None:
    body = 'The "Fee" {{def:}} is due.\n\n# A\n\nThe "Fee" {{def:}} again. See {{term: fee}}.\n'
    output = render(FRONT + body, standalone=False).output
    preamble, sections = output.split('<div class="ld-preamble">')[1].split("</div>", 1)
    assert 'id="def:fee"' in preamble
    assert 'id="def:fee"' not in sections


def test_a_later_paragraph_in_a_list_item_is_the_validators_paragraph() -> None:
    # The validator ends the list at the blank line and reads "second" as a
    # top-level paragraph, whose marker it places; so does the renderer.
    output = text("# A\n\n- item one\n\n  second {#sec}\n\nSee {{ref: sec}}.\n")
    assert "(a) item one\n\nsecond\n\nSee 1." in output


def test_a_level_six_heading_nests_where_its_number_puts_it() -> None:
    output = render(FRONT + "# A\n\n## B\n\n### C\n\n#### D\n\n##### E\n\n###### F\n", standalone=False).output
    assert output.count('class="ld-section ld-level-5"') == 2
    # F is E's sibling, numbered after it — not nested inside it.
    assert ('</section><section class="ld-section ld-level-5" id="f"><h6 class="ld-heading">'
            '<span class="ld-number">1.1.1.1.2</span>') in output.replace("\n", "")


# -- one parser: the renderer follows the validator's reading ---------------------------


def _validator_rules(source: str) -> set[str]:
    from legaldown import parse_document, validate_document

    return {d.rule for d in validate_document(parse_document(source)).diagnostics}


def test_a_signature_block_heading_is_an_ordinary_section() -> None:
    result = render(FRONT + "# One\n\nText.\n\n# Signature Block {#signature-block}\n\nSigned\n", format="text")
    assert [s.designation for s in result.tree.sections] == ["1", "2"]
    assert "Signed" in result.output


def test_every_list_marker_makes_a_list() -> None:
    output = text("# A\n\n* star\n* list\n\n1) one\n2) two\n")
    assert "(a) star\n(b) list" in output
    assert "1. one\n2. two" in output


def test_table_columns_keep_their_alignment() -> None:
    html = render(FRONT + "# A\n\n| l | c | r | n |\n|:--|:-:|--:|---|\n| 1 | 2 | 3 | 4 |\n", standalone=False).output
    assert '<th style="text-align: left">l</th>' in html
    assert '<td style="text-align: center">2</td>' in html
    assert '<td style="text-align: right">3</td>' in html
    assert "<td>4</td>" in html


def test_indented_code_renders_as_code_without_its_indent() -> None:
    html = render(FRONT + "# A\n\nPara.\n\n    code {{ref: x}}\n      deeper\n", standalone=False).output
    assert "<pre class=\"ld-code\"><code>code {{ref: x}}\n  deeper\n</code></pre>" in html
    assert "BROKEN REF" not in html


def test_markers_and_references_agree_with_the_validators_diagnostics() -> None:
    cases = [
        ("1. item\n   ```\n   code\n   ```\n   after {#x}\n\nSee {{ref: x}}.\n", True),
        ("1. # Heading {#x}\n\nSee {{ref: x}}.\n", False),
        ("- item one\n\n  second {#x}\n\nSee {{ref: x}}.\n", False),
    ]
    for body, broken in cases:
        source = FRONT + "# One\n\n" + body
        output = render(source, format="text").output
        assert ("ref-broken" in _validator_rules(source)) is broken
        assert ("[BROKEN REF: x]" in output) is broken


def test_template_decision_is_the_validators() -> None:
    source = FRONT.replace("title: T", THREE_WAY) + (
        "- intro\n\n  {{include: other.lgd}} {when=flag}\n\nPreamble para {when=flag}\n\n# A\n")
    result = render(source, format="text")
    assert result.tree.is_template is True
    assert "[Only if: flag] Preamble para" in result.output


# -- regressions from the seventh code review ------------------------------------------


def test_quote_content_is_read_as_a_body() -> None:
    # Text starting with "---" inside a quote is not frontmatter.
    output = text("# A\n\n> ---\n> title: x\n> ---\n> Visible?\n")
    for line in ("> title: x", "> Visible?"):
        assert line in output


def test_a_signature_block_heading_in_a_quote_renders() -> None:
    # What follows the heading depends on the validator's parser
    # (ForLegalAI/legaldown-validator#24); only the text before it is pinned.
    output = text("# A\n\n> intro\n>\n> ## Signature Block {#signature-block}\n> after text\n")
    assert "> intro" in output


def test_cutting_a_marker_keeps_the_space_after_a_reference() -> None:
    assert "See 1 and more." in text("# A {#a}\n\nSee {{ref: a}} and more. {#p2}\n")


def test_a_marker_copy_inside_a_comment_is_not_the_marker() -> None:
    output = text("# A\n\nText here. {#p1} <!-- {#p1} -->\n\nSee {{ref: p1}}.\n")
    assert "Text here.\n" in output
    assert "{#p1}" not in output


def test_an_item_opening_with_a_drafting_note_or_code() -> None:
    output = text("# A\n\n- > [!DRAFTING]\n  > note here\n- ```\n  code\n  ```\n")
    assert "(a) > [Drafting note]\n    > note here" in output
    assert "(b)     code" in output
    assert "```" not in output


def test_an_html_block_is_dropped_whole_and_reported() -> None:
    result = render(FRONT + "# A\n\n<script>\nalert(1)\n</script>\n\nAfter.\n", standalone=False)
    assert "script" not in result.output
    assert "alert(1)" not in result.output
    assert "After." in result.output
    assert "raw-html" in {d.rule for d in result.diagnostics}


def test_a_comment_block_renders_nothing_and_holds_no_section() -> None:
    result = render(FRONT + "# Zero\n\n<!--\nhidden\n\n# Old clause\n-->\n\nNext.\n", format="text")
    assert [s.designation for s in result.tree.sections] == ["1"]
    assert "hidden" not in result.output and "Old clause" not in result.output
    assert "Next." in result.output
    assert "raw-html" not in {d.rule for d in result.diagnostics}


def test_an_html_block_after_a_comment_is_still_reported() -> None:
    result = render(FRONT + "# A\n\n<!-- note --> <b>tail</b>\n", format="text")
    assert "tail" not in result.output
    assert "raw-html" in {d.rule for d in result.diagnostics}


def test_an_unclosed_comment_inside_a_paragraph_is_text() -> None:
    # CommonMark: only a line that starts with <!-- opens an HTML block.
    output = text("# A\n\nBefore <!-- open\n\nShown.\n")
    assert "Before <!-- open" in output
    assert "Shown." in output


def test_a_hard_break_keeps_both_lines() -> None:
    # How the break shows depends on the validator's parser
    # (ForLegalAI/legaldown-validator#25); only the text is pinned.
    output = text("# A\n\nLine one\\\nline two\n")
    assert "Line one" in output
    assert "line two" in output


def test_a_comment_opener_in_code_or_a_directive_does_not_open() -> None:
    output = text("# A\n\nx `<!--` y\n\nz\n\nw {{blank: a <!-- b}} v\n\nlast\n")
    assert "\nz\n" in output
    assert "\nlast\n" in output


def test_an_escaped_or_empty_comment_opener_does_not_open() -> None:
    output = text("# A\n\nLiteral \\<!-- not a comment --> here.\n\nNext.\n\nText <!--> after.\n\nA <!---> b\n")
    assert "Literal <!-- not a comment --> here." in output
    assert "\nNext.\n" in output
    assert "Text after." in output
    assert "A b" in output


def test_a_dropped_comment_or_tag_leaves_one_space() -> None:
    result = render(FRONT + "# A\n\na <!-- x --> b <br> c <span>d</span> Line<br>two Word<b>bold</b>word\n",
                    format="text")
    assert "a b c d Line two Wordboldword" in result.output
    warnings = [d for d in result.diagnostics if d.rule == "raw-html"]
    assert len(warnings) == 1 and "in 1 place" in warnings[0].message


def test_table_rows_are_as_wide_as_the_header() -> None:
    output = text("# A\n\n| a | b |\n|---|---|\n| 1 | 2 | 3 |\n| x |\n")
    assert "| 1 | 2 |\n| x |  |" in output


def test_template_decision_matches_the_validator_on_the_test_documents() -> None:
    from legaldown import parse_document
    from legaldown.validator.core import is_template

    from legaldown_render.validator_bridge import placed_markers

    for name in ("features", "template"):
        document = parse_document((DOCUMENTS / f"{name}.lgd").read_text(encoding="utf-8"))
        assert placed_markers(document).template == is_template(document)
