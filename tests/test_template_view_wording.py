"""Template view wording (#15): blanks labelled from their id or their
question's prompt (``placeholders.blank`` fields), and condition labels for
each shape of condition (``labels.condition_true`` and the others). Nothing
changes unless a style sets them; a blank's ``note=`` is never shown
(§10.1, §13.5 rule 4)."""
from __future__ import annotations

import pytest
from conftest import DOCUMENTS

from legaldown_render import StyleError, load_style, render

QUESTIONS = """---
title: T
language: {language}
questions:
  fee:
    type: money
    prompt: "  Monthly fee  "
  start:
    type: date
  vat:
    type: boolean
    prompt: Is VAT charged?
  forum:
    type: choice
    prompt: How are disputes resolved?
    choices:
      courts: State courts
      arbitration: "{{question}} arbitration"
---
"""

BODY = """
# Fees {{#fees}}

The fee is {{{{placeholder: fee, currency=EUR, note=Agreed in the term sheet}}}}, from
{{{{placeholder: start}}}}, for {{{{placeholder: purchase-price}}}}.

VAT is added. {{when=vat}}

No VAT is added. {{when=!vat}}

# Courts {{#courts when=forum:courts}}

Courts.

# Not courts {{#not-courts when=!forum:courts}}

Elsewhere.

# Arbitration {{#arbitration when=forum:arbitration}}

Arbitration.
"""


def template(language: str = "en") -> str:
    return QUESTIONS.format(language=language) + BODY.format()


def text(source: str, **overrides: object) -> str:
    return render(source, format="text", style=load_style("default", overrides=overrides)).output


# -- Nothing changes by default ------------------------------------------------------

def test_the_default_template_view_is_unchanged() -> None:
    output = text(template())
    assert "The fee is [_____], from [_____], for [_____]." in output
    for label in ("[Only if: vat]", "[Only if: !vat]", "[Only if: forum:courts]", "[Only if: !forum:courts]",
                  "[Only if: forum:arbitration]"):
        assert label in output
    assert "term sheet" not in output


def test_a_style_that_sets_only_the_condition_label_keeps_its_output() -> None:
    output = text(template(), **{"labels.condition": "When {condition}:"})
    assert "[When vat:]" in output and "[When !forum:courts:]" in output


# -- Blanks ----------------------------------------------------------------------

def test_a_blank_can_show_its_id_name_or_prompt() -> None:
    assert "The fee is [fee], from [start], for [purchase-price]." in text(template(), **{"placeholders.blank": "[{id}]"})
    assert "The fee is [Fee], from [Start], for [Purchase price]." in text(
        template(), **{"placeholders.blank": "[{name}]"})
    # The prompt, stripped; without one, the name.
    assert "The fee is [Monthly fee], from [Start], for [Purchase price]." in text(
        template(), **{"placeholders.blank": "[{prompt}]"})


def test_a_blank_never_shows_its_note() -> None:
    for blank in ("[{id}]", "[{name}]", "[{prompt}]", "[{id}: {name}: {prompt}]"):
        assert "term sheet" not in text(template(), **{"placeholders.blank": blank})


def test_only_the_blank_fields_are_fields() -> None:
    """Any other braces are text, so a blank written as ``{TBD}`` renders as it did."""
    output = text(template(), **{"placeholders.blank": "{TBD} {note} {id}"})
    assert "The fee is {TBD} {note} fee," in output


def test_a_blank_is_filled_in_one_pass() -> None:
    source = template().replace("prompt: \"  Monthly fee  \"", "prompt: \"{name} {id}\"")
    assert "The fee is [{name} {id}]," in text(source, **{"placeholders.blank": "[{prompt}]"})


def test_a_blank_with_fields_always_shows() -> None:
    """§13.5 rule 6 is judged on the filled text: ``{id}`` alone is never empty."""
    assert "The fee is fee," in text(template(), **{"placeholders.blank": "{id}"})
    assert "The fee is [TBD: fee]," in text(template(), **{"placeholders.blank": " "})


def test_a_frontmatter_blank_is_labelled_too() -> None:
    source = (DOCUMENTS / "template.lgd").read_text(encoding="utf-8")
    output = text(source, **{"placeholders.blank": "[{prompt}]"})
    assert "Consulting Agreement with [The client's name]" in output


def test_a_labelled_blank_in_a_heading_reads_in_a_reference_under_the_none_scheme() -> None:
    source = "---\ntitle: T\n---\n\n# Price of {{placeholder: price}} {#price}\n\nSee {{ref: price}}.\n"
    output = text(source, **{"numbering.scheme": "none", "placeholders.blank": "[{name}]"})
    assert "Price of [Price]" in output
    assert "See Price of [Price]." in output


def test_a_labelled_blank_may_wrap_in_html() -> None:
    source = template()
    plain = render(source, format="html").output
    assert 'class="ld-blank" data-placeholder="fee"' in plain and "ld-blank-label" not in plain
    labelled = render(source, format="html",
                      style=load_style("default", overrides={"placeholders.blank": "[{name}]"})).output
    assert 'class="ld-blank ld-blank-label" data-placeholder="fee"' in labelled
    assert ".ld-blank-label { white-space: normal; }" in labelled
    assert labelled.index(".ld-blank {") < labelled.index(".ld-blank-label {")


def test_the_blank_fields_reach_docx() -> None:
    import io
    import zipfile

    output = render(template(), format="docx",
                    style=load_style("default", overrides={"placeholders.blank": "[{name}]"})).binary
    document = zipfile.ZipFile(io.BytesIO(output)).read("word/document.xml").decode("utf-8")
    assert "[Purchase price]" in document


# -- Condition labels --------------------------------------------------------------

READABLE = {
    "labels.condition_true": "Only if: {question}",
    "labels.condition_false": "Only if not: {question}",
    "labels.condition_is": "Only if: {question} = {answer}",
    "labels.condition_is_not": "Only if: {question} ≠ {answer}",
}


def test_each_shape_of_condition_takes_its_label() -> None:
    output = text(template(), **READABLE)
    assert "[Only if: vat]" in output
    assert "[Only if not: vat]" in output
    assert "[Only if: forum = State courts]" in output
    assert "[Only if: forum ≠ State courts]" in output


def test_an_answer_label_is_filled_in_one_pass() -> None:
    assert "[Only if: forum = {question} arbitration]" in text(template(), **READABLE)


def test_a_shape_without_its_label_takes_the_condition_label() -> None:
    output = text(template(), **{"labels.condition_is": "If {question} is {answer}", "labels.condition": "[{condition}]"})
    assert "[If forum is State courts]" in output
    assert "[[vat]]" in output and "[[!forum:courts]]" in output


def test_an_empty_shape_label_shows_the_bare_condition() -> None:
    output = text(template(), **{"labels.condition_true": "", "labels.condition": "When {condition}"})
    assert "[vat]" in output and "[When !vat]" in output


def test_the_condition_as_written_is_a_field_too() -> None:
    assert "[Only if forum:courts (forum)]" in text(
        template(), **{"labels.condition_is": "Only if {condition} ({question})"})


def test_a_value_without_a_choice_label_shows_the_value() -> None:
    source = template().replace("      courts: State courts\n", "      courts: \"  \"\n")
    assert "[Only if: forum = courts]" in text(source, **READABLE)


def test_an_invalid_condition_takes_the_condition_label() -> None:
    source = template().replace("{when=vat}", "{when=forum:nowhere}")
    result = render(source, format="text", style=load_style("default", overrides=READABLE))
    assert "[Only if: forum:nowhere]" in result.output
    assert any(diagnostic.rule == "condition-invalid" for diagnostic in result.diagnostics)


def test_the_labels_reach_every_conditional_unit() -> None:
    """Sections, list items, paragraphs, attachments and the contents."""
    source = (DOCUMENTS / "template.lgd").read_text(encoding="utf-8")
    output = text(source, **READABLE, **{"contents.enabled": True})
    assert "[Only if: non-solicit]" in output
    assert "[Only if not: non-solicit]" in output
    assert "[Only if: forum = State courts]" in output
    assert output.count("[Only if: forum = Arbitration]") >= 2  # the section and its contents entry


def test_a_language_with_its_own_labels_keeps_them_for_unset_shapes() -> None:
    output = text(template("cs"), **{"labels.condition_is": "Pouze pokud: {question} = {answer}"})
    assert "[Pouze pokud: forum = State courts]" in output
    assert "[Pouze pokud: vat]" in output and "[Pouze pokud: !forum:courts]" in output


def test_an_extended_style_inherits_the_shape_labels(tmp_path) -> None:
    (tmp_path / "base.yaml").write_text('version: 1\nname: base\nlabels:\n  condition_true: "If {question}"\n',
                                        encoding="utf-8")
    (tmp_path / "child.yaml").write_text('version: 1\nname: child\nextends: base.yaml\nlabels:\n'
                                         '  condition: "When {condition}"\n', encoding="utf-8")
    style = load_style(tmp_path / "child.yaml")
    output = render(template(), format="text", style=style).output
    assert "[If vat]" in output and "[When !vat]" in output


def test_the_labels_reach_html_and_docx() -> None:
    import io
    import zipfile

    style = load_style("default", overrides=READABLE)
    assert "Only if: forum = State courts" in render(template(), format="html", style=style).output
    docx = render(template(), format="docx", style=style).binary
    assert "Only if: forum = State courts" in zipfile.ZipFile(io.BytesIO(docx)).read("word/document.xml").decode()


# -- Validation --------------------------------------------------------------------

@pytest.mark.parametrize(("key", "template_text", "problem"), [
    ("labels.condition_true", "Only if", "must contain {condition} or {question}"),
    ("labels.condition_false", "Not {answer}", "unknown field {answer}"),
    ("labels.condition_is", "If {question}", "must contain {answer} or {condition}"),
    ("labels.condition_is_not", "Unless {answer}", "must contain {condition} or {question}"),
    ("labels.condition_is", "If {prompt} = {answer}", "unknown field {prompt}"),
])
def test_a_shape_label_must_show_the_condition(key: str, template_text: str, problem: str) -> None:
    with pytest.raises(StyleError) as caught:
        load_style("default", overrides={key: template_text})
    assert any(message.startswith(f"{key}: {problem}") for message in caught.value.problems), caught.value.problems


@pytest.mark.parametrize("template_text", ["{condition}", "If {question} ({condition})", ""])
def test_usable_shape_labels(template_text: str) -> None:
    for key in READABLE:
        load_style("default", overrides={key: template_text})


def test_untrusted_styles_take_the_shape_labels_and_blank_fields() -> None:
    style = load_style({"labels": {"condition_is": "If {question} = {answer}"}, "placeholders": {"blank": "[{name}]"}},
                       trusted=False)
    output = render(template(), format="text", style=style).output
    assert "[If forum = State courts]" in output and "[Purchase price]" in output


def test_the_fill_functions_fill_in_one_pass() -> None:
    """A value is never filled again, whatever the order of the fields."""
    from legaldown_render.resolve.resolver import fill_fields
    from legaldown_render.style.model import fill_blank

    assert fill_fields("{a} {b} {c}", {"a": "{b}", "b": "x"}) == "{b} x {c}"
    assert fill_blank("{prompt} {id} {name}", {"prompt": "{id}", "id": "fee", "name": "{prompt}"}) == "{id} fee {prompt}"
