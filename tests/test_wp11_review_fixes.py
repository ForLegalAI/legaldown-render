"""Review fixes after wave 3 (WP11): the plural of a huge fraction is computed
from its first digits, a list item opening with a table, code or quote keeps
that block intact in plain text, the ``-o`` extension rule has no holes, and a
term text worked out inside a cycle is not kept."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from babel import Locale

from legaldown_render import render
from legaldown_render.resolve.values import Formatter
from legaldown_render.style.model import Values
from legaldown_render.writers import file_extension, format_for_path

SRC = Path(__file__).resolve().parents[1] / "src"
FRONT = "---\ntitle: T\n---\n\n"
FEATURES = str(Path(__file__).resolve().parent / "documents" / "features.lgd")


def text(body: str, **settings: object) -> str:
    return render(FRONT + body, format="text", **settings).output


# -- 1: the plural category of a huge fraction -----------------------------------


def _formatter(locale: str, **values: str) -> Formatter:
    return Formatter(Locale.parse(locale), Values(**values))


@pytest.mark.parametrize("unit", ["D", "Y", "W"])
@pytest.mark.parametrize("locale", ["cs", "sk", "lt", "pl", "ru", "en"])
def test_a_huge_fraction_has_the_unit_form_of_its_first_digits(locale: str, unit: str) -> None:
    formatter = _formatter(locale)
    huge = formatter.format_duration("1." + "3" * 4400, unit).text
    short = formatter.format_duration("1." + "3" * 40, unit).text
    assert huge.replace("3" * 4400, "") == short.replace("3" * 40, "")


def test_a_huge_fraction_is_many_in_czech() -> None:
    formatter = _formatter("cs")
    assert formatter.format_duration("1." + "3" * 4400, "D").text.endswith(" dne")
    assert formatter.format_duration("1." + "3" * 40, "D").text.endswith(" dne")


def test_a_huge_fraction_of_zeros_ending_in_a_digit_keeps_its_fraction() -> None:
    # Cut to 30 digits it is "1.000...0": still a fraction (v != 0), so "many" in Czech.
    value = "1." + "0" * 4399 + "1"
    assert _formatter("cs").format_duration(value, "D").text.rsplit(" ", 1)[-1] == "dne"


def test_a_name_mode_money_amount_with_a_huge_fraction_uses_the_form_of_its_first_digits() -> None:
    formatter = _formatter("cs", money="name")
    huge = formatter.money("1." + "3" * 4400, "EUR")
    short = formatter.money("1." + "3" * 40, "EUR")
    assert huge.replace("3" * 4400, "") == short.replace("3" * 40, "")
    assert huge.endswith("eura")  # "many", not the "other" form "eur"


def test_an_integer_part_beyond_the_limit_falls_back_to_other() -> None:
    # No category can be computed (not even from the first digits): "other" ("dnů" in Czech).
    other = _formatter("cs").format_duration("1.5", "D").text.rsplit(" ", 1)[-1]
    assert other == "dne"
    assert _formatter("cs").format_duration("1" * 4400, "D").text.endswith(" dnů")


# -- 2: an item that opens with a table, code or a quote -------------------------


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


# -- 3: the extension rule of -o --------------------------------------------------


@pytest.mark.parametrize(("name", "extension"), [
    ("x.pdf", "pdf"), (".pdf", "pdf"), (".md", "md"), ("x.pdf.", "pdf"), ("x.docx ", "docx"),
    ("x.pdf. .", "pdf"), ("X.PDF", "pdf"), ("a.b.txt", "txt"), ("contract", ""), ("", ""), (".", ""),
    ("..", ""), ("x.", ""),
])
def test_the_extension_of_a_file_name(name: str, extension: str) -> None:
    assert file_extension(name) == extension


@pytest.mark.parametrize(("name", "expected"), [
    (".txt", "text"), ("x.txt.", "text"), ("x.html ", "html"), (".htm", "html"), ("x.unknown", None),
])
def test_the_format_a_file_name_implies(name: str, expected: str | None) -> None:
    assert format_for_path(name) == expected


def _run(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    return subprocess.run([sys.executable, "-m", "legaldown_render.cli", *arguments],
                          capture_output=True, text=True, cwd=cwd, env=env, check=False)


@pytest.mark.parametrize("name", [".pdf", ".md", "x.pdf.", "x.docx ", "x.docx. .", "dir.pdf", "x.MD"])
def test_an_unwritten_extension_is_refused_however_it_is_spelled(tmp_path: Path, name: str) -> None:
    result = _run(FEATURES, "-o", name, "-q", cwd=tmp_path)
    assert result.returncode == 2, result.stderr
    assert "cannot tell the output format" in result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["x.pdf.", ".md", "x.docx "])
def test_format_overrides_an_unwritten_extension(tmp_path: Path, name: str) -> None:
    result = _run(FEATURES, "-o", name, "-f", "text", "-q", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / name).read_text(encoding="utf-8").lstrip().startswith("<")


@pytest.mark.parametrize("name", ["contract", "contract.v2", "report.2025-10-27", ".out", "x.", "x.."])
def test_a_name_without_a_known_extension_still_writes_html(tmp_path: Path, name: str) -> None:
    result = _run(FEATURES, "-o", name, "-q", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "<html" in (tmp_path / name).read_text(encoding="utf-8").lower()


@pytest.mark.parametrize(("name", "marker"), [(".txt", "<html"), ("x.txt.", "<html")])
def test_a_text_name_with_a_dot_quirk_writes_text(tmp_path: Path, name: str, marker: str) -> None:
    result = _run(FEATURES, "-o", name, "-q", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert marker not in (tmp_path / name).read_text(encoding="utf-8").lower()


# -- 4: a term text worked out inside a cycle is not kept ------------------------

CYCLE = ('# A\n\nUsed first: {{term: ca}} and {{term: cb}}.\n\n'
         '"Alpha {{term: cb}}" {{def: ca}}\n\n"Beta {{term: ca}}" {{def: cb}}\n')


def test_a_term_reference_in_a_cycle_does_not_depend_on_the_order_of_use() -> None:
    # WP12: a term shown by {{term:}} is its text with that term open (a reference back to it
    # shows its id), whichever term is used first. (The definition site renders with no term
    # open, so it can read longer: "Beta Alpha Beta ca" there, "Beta Alpha cb" in a reference.)
    ca_first = text(CYCLE)
    cb_first = text(CYCLE.replace("{{term: ca}} and {{term: cb}}", "{{term: cb}} and {{term: ca}}"))
    assert "Used first: Alpha Beta ca and Beta Alpha cb." in ca_first
    assert "Used first: Beta Alpha cb and Alpha Beta ca." in cb_first


def _chain(n: int, *, cycle: bool, heading: bool = False) -> str:
    """Terms t0..tn, each reading the next one twice; the last one reads t0 when *cycle*."""
    defs = [f'"T{i} {{{{term: t{i + 1}}}}} {{{{term: t{i + 1}}}}}" {{{{def: t{i}}}}}' for i in range(n)]
    defs.append(f'"T{n}{" {{term: t0}}" if cycle else ""}" {{{{def: t{n}}}}}')
    title = "About {{term: t0}}" if heading else "A"
    return f"# {title}\n\nUsed: {{{{term: t0}}}}\n\n" + "\n\n".join(defs) + "\n"


@pytest.mark.parametrize("heading", [False, True])
@pytest.mark.parametrize("scheme", ["decimal", "none"])
@pytest.mark.parametrize("cycle", [False, True])
def test_a_branching_chain_of_terms_is_worked_out_once_per_term(cycle: bool, scheme: str, heading: bool) -> None:
    # Each term reads the next twice: worked out afresh at each use, 20 terms is 2**20 resolutions.
    # The text of t0 is itself 2**n words long (4**n with a cycle, which reads the chain round
    # twice), so the chains cannot be longer. Under the none scheme a heading is read as text
    # before the targets are known (§13.3).
    started = time.perf_counter()
    out = text(_chain(9 if cycle else 20, cycle=cycle, heading=heading), overrides={"numbering.scheme": scheme})
    assert time.perf_counter() - started < 2
    assert "{{" not in out


def test_a_chain_without_a_cycle_reads_as_each_term_does() -> None:
    lines = text(_chain(3, cycle=False)).splitlines()
    used = next(line for line in lines if line.startswith("Used: "))
    # t1 as used inside t0 is the same text as at t1's own definition.
    site = next(line for line in lines if line.startswith("T1 "))
    assert used == f"Used: T0 {site} {site}"


# -- 4b: a term text is capped ----------------------------------------------------


def _warnings(body: str, **settings: object) -> list[str]:
    return [d.message for d in render(FRONT + body, format="text", **settings).diagnostics
            if d.rule == "render-term-too-long"]


@pytest.mark.parametrize(("n", "cycle"), [(40, False), (14, True)])
@pytest.mark.parametrize("scheme", ["decimal", "none"])
def test_a_branching_chain_of_terms_is_capped_and_reported(n: int, cycle: bool, scheme: str) -> None:
    started = time.perf_counter()
    result = render(FRONT + _chain(n, cycle=cycle, heading=True), format="text",
                    overrides={"numbering.scheme": scheme})
    assert time.perf_counter() - started < 2
    assert len(result.output) < 100_000
    messages = [d.message for d in result.diagnostics if d.rule == "render-term-too-long"]
    assert messages
    assert len(messages) == len(set(messages))  # one per id
    assert all("longer than 1000 characters" in message and "shows the definition id" in message
               for message in messages)
    assert "{{" not in result.output


def test_a_term_over_the_cap_shows_its_id_and_one_warning_names_it() -> None:
    long_term = "word " * 250
    body = f'# A\n\n"{long_term}" {{{{def: big}}}}\n\nSee {{{{term: big}}}} and {{{{term: big}}}}.\n'
    out = text(body)
    assert "See big and big." in out
    assert _warnings(body) == [
        "The term of 'big' is longer than 1000 characters once its references are resolved; "
        "'{{term: big}}' shows the definition id."]


def test_a_label_still_wins_over_the_cap() -> None:
    body = '# A\n\n"' + "word " * 250 + '" {{def: big}}\n\nSee {{term: big, label=Big}}.\n'
    assert "See Big." in text(body)


def test_a_term_at_the_cap_is_shown_whole() -> None:
    term = "x" * 1000
    body = f'# A\n\n"{term}" {{{{def: edge}}}}\n\nSee {{{{term: edge}}}}.\n'
    assert f"See {term}." in text(body)
    assert _warnings(body) == []


def test_a_normal_nested_term_is_unaffected() -> None:
    body = ('# A\n\n"Company" {{def: company}}\n\n"Affiliate of the {{term: company}}" {{def: affiliate}}\n\n'
            'See {{term: affiliate}}.\n')
    assert "See Affiliate of the Company." in text(body)
    assert _warnings(body) == []
