"""Word's list numbering as the DOCX planner models it (docs/architecture.md, "The DOCX planner"): counters as Word
writes them, label templates as tokens (the same text the resolver fills in, with the owner of each counter kept),
the ``lvlText`` of a level, and the simulator that gives each paragraph of a list its number."""
from __future__ import annotations

import pytest

from legaldown_render.errors import InternalError
from legaldown_render.resolve.docx_numbering import (
    Count,
    Ghost,
    Lit,
    WordNumbering,
    extend_tokens,
    fill_tokens,
    level_text,
    level_text_problem,
    lvl_text,
    shows_section,
    split_level_text,
    text_of,
    word_counter,
)
from legaldown_render.resolve.docx_plan import AbstractNum, LevelDef, Num
from legaldown_render.resolve.numbering import extend, fill, format_counter

# -- Counters ---------------------------------------------------------------------


@pytest.mark.parametrize(("value", "num_fmt", "expected"), [
    (1, "decimal", "1"), (4000, "decimal", "4000"),
    (1, "lowerLetter", "a"), (26, "lowerLetter", "z"), (27, "lowerLetter", "aa"), (28, "lowerLetter", "bb"),
    (52, "lowerLetter", "zz"), (53, "lowerLetter", "aaa"), (54, "upperLetter", "BBB"),
    (4, "lowerRoman", "iv"), (14, "upperRoman", "XIV"), (3999, "upperRoman", "MMMCMXCIX"),
    (4000, "lowerRoman", "mmmm"), (4999, "lowerRoman", "mmmmcmxcix"),
    (0, "decimal", "0"), (0, "lowerLetter", ""),
])
def test_word_counters(value: int, num_fmt: str, expected: str) -> None:
    assert word_counter(value, num_fmt) == expected


@pytest.mark.parametrize(("counter", "num_fmt"), [
    ("decimal", "decimal"), ("lower-alpha", "lowerLetter"), ("upper-alpha", "upperLetter"),
    ("lower-roman", "lowerRoman"), ("upper-roman", "upperRoman")])
def test_word_and_renderer_counters_agree_where_word_defines_them(counter: str, num_fmt: str) -> None:
    limit = 4000 if counter.endswith("roman") else 27 if counter.endswith("alpha") else 5000
    for value in range(1, limit):
        assert word_counter(value, num_fmt) == format_counter(value, counter)


def test_word_repeats_letters_where_the_renderer_counts_on() -> None:
    assert [word_counter(v, "lowerLetter") for v in (27, 28, 29)] == ["aa", "bb", "cc"]
    assert [format_counter(v, "lower-alpha") for v in (27, 28, 29)] == ["aa", "ab", "ac"]
    assert word_counter(4000, "upperRoman") == "MMMM" and format_counter(4000, "upper-roman") == "4000"


# -- Token strings ----------------------------------------------------------------


@pytest.mark.parametrize(("template", "section"), [
    ("{n}.", None), ("({n})", None), ("{section}.{n}", "5"), ("{section}.{n}", ""), ("{section}..{n}", ""),
    ("({section}){n}", ""), ("{path}.{n}", "2"), ("a {other} {n}", "1"), ("{n}{n}", None)])
def test_filling_tokens_reads_as_filling_the_template(template: str, section: str | None) -> None:
    tokens = fill_tokens(template, n=[Count("n", "b")], path=[Lit("2.1")],
                         section=None if section is None else [Lit(section)] if section else [])
    kwargs = {} if section is None else {"section": section}
    assert text_of(tokens) == fill(template, n="b", path="2.1", **kwargs)


@pytest.mark.parametrize(("designation", "piece", "textual"), [
    ("", ".1", False), ("2.1", "(b)", False), ("Termination", "(a)", True), ("", ".1", True), ("A", ".1", True)])
def test_extending_tokens_reads_as_extending_the_designation(designation: str, piece: str, textual: bool) -> None:
    tokens = extend_tokens([Lit(designation)] if designation else [], [Lit(piece)], textual=textual)
    assert text_of(tokens) == extend(designation, piece, textual=textual)


def test_tokens_keep_the_owner_of_each_counter() -> None:
    tokens = fill_tokens("{path}({n})", n=[Count("u2", "b")], path=[Count("s1", "1"), Lit(".")])
    assert tokens == [Count("s1", "1"), Lit("."), Lit("("), Count("u2", "b"), Lit(")")]


# -- lvlText ----------------------------------------------------------------------


def test_a_level_shows_only_counters_of_its_own_list() -> None:
    refs = ["{n}", ".{n}", ".{n}"]
    assert lvl_text(level_text("{path}.", refs, 0, section=None)) == "%1."
    assert lvl_text(level_text("{path}", refs, 2, section=None)) == "%1.%2.%3"
    assert lvl_text(level_text("({n})", ["({n})"] * 3, 1, section=())) == "(%2)"
    assert lvl_text(level_text("Article {n}", ["{n}"], 0, section=None)) == "Article %1"


def test_a_label_that_needs_the_section_is_written_as_in_the_preamble() -> None:
    # Word cannot show the section's counter in a list level: the label loses the field and the dot after it.
    assert lvl_text(level_text("{section}.{n}", [".{n}"], 0, section=())) == "%1"
    assert lvl_text(level_text("{path}", [".{n}", "({n})"], 1, section=())) == "%1(%2)"


def test_literal_percent_before_a_digit_cannot_be_written() -> None:
    assert lvl_text(level_text("100% {n}", ["{n}"], 0, section=None)) == "100% %1"
    with pytest.raises(InternalError):
        lvl_text(level_text("%1 {n}", ["{n}"], 0, section=None))
    with pytest.raises(InternalError):
        lvl_text(level_text("%{n}", ["{n}"], 0, section=None))


def test_a_percent_that_meets_a_counter_where_pieces_are_put_together_is_a_problem() -> None:
    parts = level_text("{path}", ["{n}%", "{n}"], 1, section=None)
    assert parts == (0, "%", 1) and level_text_problem(parts) is not None
    assert level_text_problem(level_text("{path}", ["{n}%", " {n}"], 1, section=None)) is None
    assert level_text_problem(("5%", 0)) is not None
    with pytest.raises(InternalError):
        lvl_text(parts)


def test_a_label_shows_the_section_when_the_filled_label_has_its_counter() -> None:
    refs = ["({n})", "({n})"]
    assert shows_section("{section}.{n}", [".{n}"], 0)
    assert shows_section("{path}", refs, 1) and shows_section("{path}", refs, 0)
    assert not shows_section("({n})", refs, 1) and not shows_section("{n})", refs, 0)
    assert shows_section("({section}) {n}", refs, 0)
    # a ref that holds the section puts it in every `{path}` below, not in a label that shows only its own counter
    assert shows_section("{path}", ["{section}{n}", "({n})"], 1)
    assert not shows_section("({n})", ["{section}{n}", "({n})"], 1)
    assert shows_section("{section}.{n}", [".{n}"], 0, textual=True)


def test_split_level_text() -> None:
    assert split_level_text("%1.%2(%10)x") == [0, ".", 1, "(", 0, "0)x"]
    assert split_level_text("plain") == ["plain"]


# -- The simulator ----------------------------------------------------------------


def _levels(*specs: tuple[str, str]) -> list[LevelDef]:
    levels = [LevelDef(i, fmt, text) for i, (fmt, text) in enumerate(specs)]
    return levels + [LevelDef(i, "decimal", f"%{i + 1}") for i in range(len(levels), 9)]


HEADINGS = _levels(("decimal", "%1."), ("decimal", "%1.%2"), ("decimal", "%1.%2.%3"))
ITEMS = _levels(("lowerLetter", "(%1)"), ("lowerRoman", "(%2)"), ("upperLetter", "(%3)"))


def numbering(*nums: Num, skipped: str = "lo") -> WordNumbering:
    sim = WordNumbering(skipped=skipped)  # type: ignore[arg-type]
    sim.add_abstract(0, HEADINGS)
    sim.add_abstract(1, ITEMS)
    for num in nums:
        sim.add_num(num)
    return sim


def test_counters_count_and_deeper_levels_restart() -> None:
    sim = numbering(Num(1, 0))
    labels = [sim.step(1, i, f"u{k}").label for k, i in enumerate([0, 1, 1, 2, 0, 1])]
    assert labels == ["1.", "1.1", "1.2", "1.2.1", "2.", "2.1"]


def test_a_step_says_what_ref_gives_and_which_unit_holds_each_counter() -> None:
    sim = numbering(Num(1, 0))
    sim.step(1, 0, "s1")
    step = sim.step(1, 1, "s2")
    assert (step.count, step.own, step.label, step.word_ref) == (1, "1", "1.1", "1.1")
    assert step.tokens == (Count("s1", "1"), Lit("."), Count("s2", "1"))
    assert sim.step(1, 0, "s3").word_ref == "2" and sim.step(1, 0, "s4").label == "3."


def test_a_start_override_restarts_a_level_at_its_first_use_only() -> None:
    sim = numbering(Num(1, 1), Num(2, 1, ((0, 1), (1, 1))), Num(3, 1, ((0, 3), (1, 1))))
    assert [sim.step(1, 0, "a").label, sim.step(1, 0, "b").label] == ["(a)", "(b)"]
    assert sim.peek(2, 0) == 1
    assert [sim.step(2, 0, "c").label, sim.step(2, 0, "d").label] == ["(a)", "(b)"]
    assert sim.peek(3, 0) == 3
    assert [sim.step(3, 0, "e").label, sim.step(3, 0, "f").label] == ["(c)", "(d)"]


def test_counters_are_shared_by_every_num_of_an_abstract_num() -> None:
    # A num without an override goes on from the counter of the abstractNum; one with an override restarts the level
    # while the levels above keep their values.
    sim = numbering(Num(1, 0), Num(2, 0, ((1, 1), (2, 1))))
    sim.step(1, 0, "s1")
    sim.step(1, 1, "s2")
    sim.step(1, 1, "s3")
    assert sim.step(2, 1, "s4").label == "1.1"
    assert sim.step(1, 1, "s5").label == "1.2"
    assert sim.step(2, 0, "s6").label == "2."


def test_a_paragraph_without_numbering_counts_nothing_and_restarts_nothing() -> None:
    sim = numbering(Num(1, 0))
    sim.step(1, 0, "s1")
    sim.step(1, 1, "s2")
    sim.step(1, 1, "s3")
    step = sim.step(0, 0, "alternative")
    assert (step.count, step.label, step.word_ref, step.tokens) == (0, "", "0", ())
    assert sim.current(1, 0) == (1, "s1") and sim.current(1, 1) == (2, "s3")
    assert sim.step(1, 1, "s4").label == "1.3" and sim.step(1, 0, "s5").label == "2."


def test_peek_and_current_do_not_count() -> None:
    sim = numbering(Num(1, 1))
    assert (sim.peek(1, 0), sim.current(1, 0)) == (1, (0, None))
    sim.step(1, 0, "u1")
    assert (sim.peek(1, 0), sim.current(1, 0)) == (2, (1, "u1"))
    assert sim.peek(1, 1) == 1


def test_a_skipped_level_reads_as_its_start_value_and_counts_on() -> None:
    sim = numbering(Num(1, 0))
    sim.step(1, 0, "s1")
    deep = sim.step(1, 2, "s2")
    assert deep.label == "1.1.1"
    assert deep.tokens[2] == Count(Ghost("s2", 1), "1")
    assert sim.step(1, 1, "s3").label == "1.2"


def test_in_the_word_model_a_skipped_level_reads_zero_and_is_not_advanced() -> None:
    sim = numbering(Num(1, 0), skipped="word")
    sim.step(1, 0, "s1")
    assert sim.step(1, 2, "s2").label == "1.0.1"
    assert sim.step(1, 1, "s3").label == "1.1"


def test_each_abstract_num_has_its_own_model_of_a_level_that_nothing_numbered() -> None:
    sim = WordNumbering()
    sim.add_abstract(0, HEADINGS)
    sim.add_abstract(1, _levels(("lowerLetter", "(%1)"), ("decimal", "%1.%2")), skipped="word")
    sim.add_num(Num(1, 0))
    sim.add_num(Num(2, 1))
    assert sim.step(1, 1, "s1").label == "1.1"  # the renderer's and LibreOffice's rule
    assert sim.step(2, 1, "u1").label == ".1"  # Word's: a letter counter at 0 is nothing


def test_the_first_paragraph_of_a_list_deeper_than_the_level_it_starts_at_fills_the_levels_above() -> None:
    sim = numbering(Num(1, 1, ((1, 1),)))
    assert sim.step(1, 1, "u1").label == "(i)"


def test_letters_past_twenty_six_repeat_in_the_word_model() -> None:
    sim = numbering(Num(1, 1))
    labels = [sim.step(1, 0, f"u{i}").own for i in range(1, 30)]
    assert labels[25:] == ["z", "aa", "bb", "cc"]


def test_an_abstract_num_may_gain_its_levels_after_it_is_added() -> None:
    levels = _levels(("decimal", "%1."))
    sim = WordNumbering()
    sim.add_abstract(0, levels)
    sim.add_num(Num(1, 0))
    levels[1] = LevelDef(1, "lowerLetter", "%2)")
    sim.step(1, 0, "a")
    assert sim.step(1, 1, "b").label == "a)"


def test_abstract_num_is_frozen_data() -> None:
    abstract = AbstractNum(0, "headings", "4C440000", "multilevel", tuple(HEADINGS))
    with pytest.raises(AttributeError):
        abstract.id = 1  # type: ignore[misc]
