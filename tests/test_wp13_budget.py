"""Review fixes (WP13): the term budget charges only repeat work, and every
command-line value echoed to stderr is escaped."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from legaldown_render import render

SRC = Path(__file__).resolve().parents[1] / "src"
FRONT = "---\ntitle: T\n---\n\n"


def rules(result, rule: str) -> list[str]:  # noqa: ANN001
    return [d.message for d in result.diagnostics if d.rule == rule]


# -- 1: acyclic terms are free ------------------------------------------------------


def _independent(n: int, *, heading: bool) -> str:
    parts = []
    for i in range(n):
        if heading:
            parts.append(f"## H{i} {{{{term: t{i}}}}}\n\n")
        parts.append(f'"Term {i}" {{{{def: t{i}}}}} used {{{{term: t{i}}}}}\n\n')
    return "# A\n\n" + "".join(parts)


@pytest.mark.parametrize("scheme", ["decimal", "none"])
@pytest.mark.parametrize("heading", [False, True])
def test_3000_independent_terms_show_their_text_and_no_warning(scheme: str, heading: bool) -> None:
    result = render(FRONT + _independent(3000, heading=heading), format="text", overrides={"numbering.scheme": scheme})
    assert rules(result, "render-term-limit") == []
    lines = result.output.splitlines()
    assert "Term 2999 used Term 2999" in lines
    assert "Term 0 used Term 0" in lines
    assert not any(line.endswith(" used t2999") for line in lines)
    if heading and scheme == "none":
        assert any(line.endswith("H2999 Term 2999") for line in lines)


# -- 2: a work budget on repeat computations ------------------------------------------


def _ring(k: int, n: int) -> str:
    """k terms in a cycle, each holding n references to the next."""
    defs = [f'"T{i} {" ".join(f"{{{{term: t{(i + 1) % k}}}}}" for _ in range(n))}" {{{{def: t{i}}}}}' for i in range(k)]
    return "# A\n\nUsed: {{term: t0}}\n\n" + "\n\n".join(defs) + "\n"


def test_a_ring_of_terms_with_many_references_costs_a_small_multiple_of_the_document() -> None:
    document = FRONT + _ring(12, 2000)
    started = time.perf_counter()
    result = render(document, format="text", overrides={"numbering.scheme": "none"})
    cyclic = time.perf_counter() - started
    # The same document with the references pointing at a term that names nothing.
    flat = document.replace("{{term: t0}}", "{{term: leaf}}")
    for i in range(12):
        flat = flat.replace(f"{{{{term: t{i}}}}}", "{{term: leaf}}")
    flat += '\n"Leaf" {{def: leaf}}\n'
    started = time.perf_counter()
    render(flat, format="text", overrides={"numbering.scheme": "none"})
    acyclic = time.perf_counter() - started
    assert rules(result, "render-term-limit")
    assert cyclic < 3 * acyclic + 1


@pytest.mark.parametrize("k", [7, 12, 40])
def test_all_to_all_terms_stay_fast(k: int) -> None:
    defs = [f'"T{i} {" ".join(f"{{{{term: t{j}}}}}" for j in range(k))}" {{{{def: t{i}}}}}' for i in range(k)]
    started = time.perf_counter()
    result = render(FRONT + "# A\n\nUsed: {{term: t0}}\n\n" + "\n\n".join(defs) + "\n", format="text")
    assert time.perf_counter() - started < 2
    assert "{{" not in result.output


def test_a_chain_of_500_terms_stays_fast_and_reports_the_limit() -> None:
    defs = [f'"T{i} {{{{term: t{i + 1}}}}}" {{{{def: t{i}}}}}' for i in range(500)] + ['"End" {{def: t500}}']
    started = time.perf_counter()
    result = render(FRONT + "# A\n\nUsed: {{term: t0}}\n\n" + "\n\n".join(defs) + "\n", format="text")
    assert time.perf_counter() - started < 2
    assert len(rules(result, "render-term-limit")) == 1


# -- 3: command-line values are escaped on stderr -------------------------------------


def _cli(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    return subprocess.run([sys.executable, "-m", "legaldown_render.cli", *arguments],
                          capture_output=True, cwd=cwd, env=env, check=False)


def _clean(errors: bytes) -> str:
    text = errors.decode("utf-8")
    assert not any((ord(c) < 0x20 and c != "\n") or 0x7F <= ord(c) < 0xA0 for c in text), text
    return text


ESC = "x\x1b]0;pwn\x07y"


def test_an_input_file_name_is_escaped(tmp_path: Path) -> None:
    result = _cli(ESC + ".lgd", cwd=tmp_path)
    assert result.returncode == 1
    assert "cannot read" in _clean(result.stderr) and "\\x1b" in _clean(result.stderr)


def test_an_answers_path_is_escaped(tmp_path: Path) -> None:
    document = tmp_path / "d.lgd"
    document.write_text(FRONT + "# A\n", encoding="utf-8")
    result = _cli(str(document), "--answers", ESC + ".yaml", cwd=tmp_path)
    assert result.returncode == 1
    assert "\\x1b" in _clean(result.stderr)


def test_an_output_path_is_escaped(tmp_path: Path) -> None:
    document = tmp_path / "d.lgd"
    document.write_text(FRONT + "# A\n", encoding="utf-8")
    result = _cli(str(document), "-o", "no-such-dir\x1b[2J/out.html", cwd=tmp_path)
    assert result.returncode == 1
    assert "\\x1b" in _clean(result.stderr)
    refused = _cli(str(document), "-o", "x\x1b[2J.pdf", cwd=tmp_path)
    assert refused.returncode == 2
    assert "\\x1b" in _clean(refused.stderr)


def test_a_style_name_is_escaped(tmp_path: Path) -> None:
    document = tmp_path / "d.lgd"
    document.write_text(FRONT + "# A\n", encoding="utf-8")
    for arguments in (["-s", ESC], ["-s", ESC, "--print-style"]):
        result = _cli(str(document), *arguments, cwd=tmp_path)
        assert result.returncode == 2
        assert "\\x1b" in _clean(result.stderr)


def test_a_locale_is_escaped(tmp_path: Path) -> None:
    document = tmp_path / "d.lgd"
    document.write_text(FRONT + "# A\n", encoding="utf-8")
    result = _cli(str(document), "--locale", "zz\x1b[31m", cwd=tmp_path)
    _clean(result.stderr)


def test_a_set_key_is_escaped_in_argparse_errors(tmp_path: Path) -> None:
    document = tmp_path / "d.lgd"
    document.write_text(FRONT + "# A\n", encoding="utf-8")
    for setting in ("bad\x1b[31m", "no\x1b[31m.such=1"):
        result = _cli(str(document), "--set", setting, cwd=tmp_path)
        assert result.returncode == 2
        assert "\\x1b" in _clean(result.stderr)


def test_an_unrecognized_argument_is_escaped(tmp_path: Path) -> None:
    result = _cli("--bogus\x1b[31m", cwd=tmp_path)
    assert result.returncode == 2
    assert "\\x1b" in _clean(result.stderr)
