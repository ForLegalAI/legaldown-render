"""Text writer fidelity (WP5): attachment separators (W1), table cells and
alignment (W4), quotes and list items (W5), list indent (W6), blank labels (W7)."""
from __future__ import annotations

import pytest

from legaldown_render import render
from legaldown_render.tree import Table, Text
from legaldown_render.writers import TextWriter

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
        identification_number: DE-1
attachments:
  - id: sch-a
    title: Schedule A
    file: a.lgd
  - id: sch-b
    title: Schedule B
    file: b.pdf
effective_date: 2026-06-01
questions:
  - id: forum
    type: choice
    options: [courts, arb]
---
"""


def text(body: str, **overrides: object) -> str:
    return render(FRONT + body, format="text", overrides=overrides).output


# -- W1: attachments.separator (§13.8) -------------------------------------------


@pytest.mark.parametrize("separator", ["rule", "page-break"])
def test_a_separator_precedes_each_attachment(separator: str) -> None:
    output = text("# A\n\nBody.\n", **{"attachments.separator": separator})
    assert "Attachments\n\n---\n\nSchedule A\nFile: a.lgd\n\n---\n\nSchedule B\nFile: b.pdf\n" in output


def test_none_prints_no_separator_between_or_before_attachments() -> None:
    output = text("# A\n\nBody.\n", **{"attachments.separator": "none"})
    assert "Body.\n\nAttachments\n\nSchedule A\nFile: a.lgd\n\nSchedule B\nFile: b.pdf\n" in output
    # The one `---` left is the one before the signatures.
    assert output.count("---") == 1
    assert output.index("---") > output.index("Schedule B")


def test_the_default_style_separates_attachments() -> None:
    assert text("# A\n\nBody.\n").count("---") >= 2


def test_the_writer_needs_no_style_and_defaults_to_the_default_styles_separator() -> None:
    assert TextWriter().attachment_separator == "page-break"
    result = render(FRONT + "# A\n\nBody.\n", format="text")
    assert TextWriter().write(result.tree) == result.output


# -- W4: tables ------------------------------------------------------------------


def test_a_pipe_in_a_cell_is_escaped() -> None:
    output = text("# A\n\n| a | b |\n|---|---|\n| x \\| y | z |\n")
    assert "| a | b |\n|---|---|\n| x \\| y | z |" in output


def test_alignment_survives_in_the_separator_row() -> None:
    output = text("# A\n\n| a | b | c | d |\n|:--|:-:|--:|---|\n| 1 | 2 | 3 | 4 |\n")
    assert "| a | b | c | d |\n|:---|:---:|---:|---|\n| 1 | 2 | 3 | 4 |" in output


def test_a_table_with_fewer_alignments_than_columns_still_prints() -> None:
    cell = (Text("x"),)
    table = Table((cell, cell), ((cell, cell),), ("right",))
    assert TextWriter().table(table) == "| x | x |\n|---:|---|\n| x | x |"


# -- W5: quotes and list items ---------------------------------------------------


def test_one_quote_stays_one_quote() -> None:
    output = text("# A\n\n> first\n>\n> second\n")
    assert "> first\n>\n> second\n" in output


def test_nested_quotes_keep_their_markers_on_blank_lines() -> None:
    output = text("# A\n\n> outer\n>\n> > inner one\n> >\n> > inner two\n>\n> back\n")
    assert "> outer\n>\n> > inner one\n> >\n> > inner two\n>\n> back\n" in output


def test_a_quote_in_a_list_item_stays_one_quote() -> None:
    output = text("# A\n\n- item\n\n  > one\n  >\n  > two\n")
    assert "(a) item\n\n    > one\n    >\n    > two\n" in output


def test_blocks_of_a_list_item_are_separated() -> None:
    output = text("# A\n\n1. one\n\n   more\n\n   and more\n2. two\n")
    assert "1. one\n\n   more\n\n   and more\n2. two\n" in output
    assert " \n" not in output  # blank lines carry no trailing space


def test_a_nested_list_stays_tight_under_its_paragraph() -> None:
    output = text("# A\n\n1. one\n\n   more\n\n   - child\n   - child two\n")
    assert "1. one\n\n   more\n   (i) child\n   (ii) child two\n" in output


def test_a_paragraph_after_a_nested_list_is_separated() -> None:
    output = text("# A\n\n1. one\n   - child\n\n   after\n")
    assert "1. one\n   (i) child\n\n   after\n" in output


# -- W6: list indent -------------------------------------------------------------


def test_a_conditional_item_indents_by_its_label_not_the_condition_label() -> None:
    body = ("# A\n\n- first\n- by courts {#i when=forum:courts}\n\n  nested\n\n  - deep\n"
            "- by arbitration {#i when=forum:arb}\n- last\n")
    output = text(body)
    assert "(b) [Only if: forum:courts] by courts\n\n    nested\n    (i) deep\n" in output


# -- W7: blank labels ------------------------------------------------------------


def test_a_blanked_label_drops_its_prefix_in_party_details() -> None:
    output = text("# A\n\nBody.\n", **{"labels.identification_number": ""})
    assert "\n    DE-1\n" in output
    assert ": DE-1" not in output


def test_a_blanked_title_block_label_drops_its_prefix() -> None:
    output = text("# A\n\nBody.\n", **{"labels.effective_date": ""})
    assert "\nJune 1, 2026\n" in output
    assert ": June 1, 2026" not in output


def test_a_label_still_prefixes_its_value() -> None:
    output = text("# A\n\nBody.\n")
    assert "Registration No.: DE-1" in output
    assert "Effective date: June 1, 2026" in output
