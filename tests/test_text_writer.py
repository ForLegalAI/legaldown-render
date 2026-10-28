"""The plain-text writer's layout (§13.8): attachment separators, table cells
and alignment, quotes and list items (also those that open with a table, code
or a quote), list indent, and the labels a style may blank."""
from __future__ import annotations

import legaldown
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


# -- Attachment separators (§13.8) -----------------------------------------------

@pytest.mark.parametrize("separator", ["rule", "page-break"])
def test_a_separator_precedes_each_attachment(separator: str) -> None:
    output = text("# A\n\nBody.\n", **{"attachments.separator": separator})
    # The heading stays with the first attachment; a rule falls before it and between attachments.
    assert "Body.\n\n---\n\nAttachments\n\nSchedule A\nFile: a.lgd\n\n---\n\nSchedule B\nFile: b.pdf\n" in output


def test_none_prints_no_separator_around_attachments() -> None:
    output = text("# A\n\nBody.\n", **{"attachments.separator": "none"})
    assert "Body.\n\nAttachments\n\nSchedule A\nFile: a.lgd\n\nSchedule B\nFile: b.pdf\n" in output
    # The one `---` left is the one before the signatures.
    assert output.count("---") == 1
    assert output.index("---") > output.index("Schedule B")


def test_the_default_style_separates_attachments() -> None:
    assert text("# A\n\nBody.\n").count("---") == 3  # heading, between attachments, signatures


def test_the_writer_needs_no_style_and_defaults_to_the_default_styles_separator() -> None:
    assert TextWriter().attachment_separator == "page-break"
    result = render(FRONT + "# A\n\nBody.\n", format="text")
    assert TextWriter().write(result.tree) == result.output


# -- Tables ----------------------------------------------------------------------

def test_a_pipe_in_a_cell_is_escaped() -> None:
    output = text("# A\n\n| a | b |\n|---|---|\n| x \\| y | z |\n")
    assert "| a | b |\n|---|---|\n| x \\| y | z |" in output


@pytest.mark.parametrize("cell", ["a | b", "a \\| b", "a \\\\| b", "a\\ b", "ends\\", "x\\|y"])
def test_a_cell_with_pipes_and_backslashes_is_read_back_as_it_was(cell: str) -> None:
    # The text writer's table is read by the validator's reader (the tool a
    # LegalDown reader is expected to use), so a cell's plain text, pipes and
    # backslashes included, must come back from it unchanged.
    markup = cell.replace("\\", "\\\\").replace("|", "\\|")  # the cell's text as GFM table markup
    output = text(f"# A\n\n| H | K |\n|---|---|\n| {markup} | z |\n")
    tables = [block for _, _, block in legaldown.parse(output).iter_blocks() if block.kind == "table"]
    assert [table.rows for table in tables] == [[[cell, "z"]]]


def test_alignment_survives_in_the_separator_row() -> None:
    output = text("# A\n\n| a | b | c | d |\n|:--|:-:|--:|---|\n| 1 | 2 | 3 | 4 |\n")
    assert "| a | b | c | d |\n|:---|:---:|---:|---|\n| 1 | 2 | 3 | 4 |" in output


def test_a_table_with_fewer_alignments_than_columns_still_prints() -> None:
    cell = (Text("x"),)
    table = Table((cell, cell), ((cell, cell),), ("right",))
    assert TextWriter().table(table) == "| x | x |\n|---:|---|\n| x | x |"


# -- Quotes and list items -------------------------------------------------------

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


def test_an_item_that_opens_with_a_table_keeps_a_valid_table() -> None:
    out = text("# A\n\n1. | x | y |\n   |---|---|\n   | 1 | 2 |\n")
    assert "\n1.\n   | x | y |\n   |---|---|\n   | 1 | 2 |\n" in out
    assert "1. |" not in out


def test_an_item_that_opens_with_code_indents_it_under_the_item_text() -> None:
    out = text("# A\n\n1. ```\n   code\n   more\n   ```\n2. next\n")
    assert "\n1.\n       code\n       more\n2. next\n" in out


def test_an_item_that_opens_with_a_quote_puts_the_quote_on_its_own_lines() -> None:
    out = text("# A\n\n1. > one\n   >\n   > two\n2. next\n")
    assert "\n1.\n   > one\n   >\n   > two\n2. next\n" in out
    assert "1. >" not in out


def test_blocks_after_a_non_paragraph_first_block_are_set_apart() -> None:
    out = text("# A\n\n1. | x |\n   |---|\n   | 1 |\n\n   then text\n")
    assert "\n1.\n   | x |\n   |---|\n   | 1 |\n\n   then text\n" in out


def test_an_item_that_opens_with_a_paragraph_is_unchanged() -> None:
    out = text("# A\n\n1. first\n   > quote\n2. second\n   | x |\n   |---|\n   | 1 |\n")
    assert "\n1. first\n\n   > quote\n2. second\n\n   | x |\n   |---|\n   | 1 |\n" in out


# -- List indent -----------------------------------------------------------------

def test_a_conditional_item_indents_by_its_label_not_the_condition_label() -> None:
    body = ("# A\n\n- first\n- by courts {#i when=forum:courts}\n\n  nested\n\n  - deep\n"
            "- by arbitration {#i when=forum:arb}\n- last\n")
    output = text(body)
    assert "(b) [Only if: forum:courts] by courts\n\n    nested\n    (i) deep\n" in output


# -- Blank labels ----------------------------------------------------------------

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
    assert "Identification No.: DE-1" in output
    assert "Effective date: June 1, 2026" in output


def test_a_blanked_signature_label_prints_the_value_alone() -> None:
    body = "# A\n\nBody.\n"
    output = text(body, **{"labels.signature_name": "", "labels.signature_title": ""})
    assert "Name:" not in output and "Title:" not in output
    assert "Date:\nPlace:" in output


def test_a_signature_row_with_no_label_and_no_value_is_omitted() -> None:
    output = text("# A\n\nBody.\n", **{"labels.signature_date": "", "labels.signature_place": ""})
    assert output.endswith("______________________________\nName:\nTitle:\n")
    assert "\n:" not in output


def test_a_blanked_signature_label_with_a_representative_shows_the_name() -> None:
    source = FRONT.replace("identification_number: DE-1", "representatives:\n          - name: John Smith\n"
                           "            title: CEO") + "# A\n\nBody.\n"
    output = render(source, format="text", overrides={"labels.signature_name": "", "labels.signature_title": ""}).output
    assert "______________________________\nJohn Smith\nCEO\nDate:\nPlace:\n" in output
