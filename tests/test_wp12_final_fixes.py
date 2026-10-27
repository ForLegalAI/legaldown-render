"""Final review fixes (WP12): terms that name each other are resolved within a
budget (``render-term-limit``), a cached term text is reused only where it would
read the same, long chains of terms do not exhaust the stack, and nothing from a
document reaches stderr as a control character."""
from __future__ import annotations

import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

from legaldown_render import render
from legaldown_render.resolve.resolver import Resolver

SRC = Path(__file__).resolve().parents[1] / "src"
FRONT = "---\ntitle: T\n---\n\n"
LIMIT = ("Defined terms refer to each other too deeply or too often to resolve fully; "
         "some {{term:}} references show their definition id.")


def messages(result, rule: str) -> list[str]:  # noqa: ANN001
    return [d.message for d in result.diagnostics if d.rule == rule]


# -- 1: terms that all name each other ---------------------------------------------


def _tangle(k: int) -> str:
    defs = [f'"T{i} {" ".join(f"{{{{term: t{j}}}}}" for j in range(k))}" {{{{def: t{i}}}}}' for i in range(k)]
    return "# A\n\nUsed: {{term: t0}}\n\n" + "\n\n".join(defs) + "\n"


@pytest.mark.parametrize("k", [3, 9, 12, 20, 40])
def test_k_terms_that_each_name_all_k_render_quickly(k: int) -> None:
    started = time.perf_counter()
    result = render(FRONT + _tangle(k), format="text")
    assert time.perf_counter() - started < 2
    assert "{{" not in result.output
    if k >= 9:
        assert messages(result, "render-term-limit") == [LIMIT]


def test_a_small_cycle_is_resolved_in_full_without_the_warning() -> None:
    assert messages(render(FRONT + _tangle(3), format="text"), "render-term-limit") == []


# -- 3: a long chain of terms ------------------------------------------------------


@pytest.mark.parametrize("scheme", ["decimal", "none"])
@pytest.mark.parametrize("heading", [False, True])
def test_a_chain_of_500_terms_does_not_exhaust_the_stack(scheme: str, heading: bool) -> None:
    n = 500
    defs = [f'"T{i} {{{{term: t{i + 1}}}}}" {{{{def: t{i}}}}}' for i in range(n)] + [f'"End" {{{{def: t{n}}}}}']
    title = "About {{term: t0}}" if heading else "A"
    body = f"# {title}\n\nUsed: {{{{term: t0}}}}\n\n" + "\n\n".join(defs) + "\n"
    started = time.perf_counter()
    result = render(FRONT + body, format="text", overrides={"numbering.scheme": scheme})
    assert time.perf_counter() - started < 2
    assert messages(result, "render-term-limit") == [LIMIT]
    used = next(line for line in result.output.splitlines() if line.startswith("Used: "))
    assert used.startswith("Used: T0 T1 T2 ")
    assert "{{" not in result.output


def test_a_chain_inside_the_depth_limit_is_resolved_in_full() -> None:
    n = 20
    defs = [f'"T{i} {{{{term: t{i + 1}}}}}" {{{{def: t{i}}}}}' for i in range(n)] + [f'"End" {{{{def: t{n}}}}}']
    result = render(FRONT + "# A\n\nUsed: {{term: t0}}\n\n" + "\n\n".join(defs) + "\n", format="text")
    assert f"Used: {' '.join(f'T{i}' for i in range(n))} End" in result.output
    assert messages(result, "render-term-limit") == []


# -- 2: a cached text is reused only where it would read the same ------------------


def _oracle(self: Resolver, cache: object, definition_id: str, work, *, key: str | None = None) -> str:  # noqa: ANN001
    """The term text with the open terms alone deciding, nothing kept."""
    self._terms_open.append(definition_id)
    try:
        return work()
    finally:
        self._terms_open.pop()


def _graph(rng: random.Random) -> str:
    ids = [f"t{i}" for i in range(rng.randint(2, 6))]
    defs = []
    for identifier in ids:
        parts = [identifier.upper()]
        for _ in range(rng.randint(0, 3)):
            target = rng.choice(ids)
            parts.append(f"{{{{term: {target}, label=L{target}}}}}" if rng.random() < 0.15
                         else f"{{{{term: {target}}}}}")
        defs.append(f'"{" ".join(parts)}" {{{{def: {identifier}}}}}')
    rng.shuffle(defs)
    heading = rng.choice(["A", f"About {{{{term: {rng.choice(ids)}}}}}"])
    use = " ".join(f"{{{{term: {rng.choice(ids)}}}}}" for _ in range(rng.randint(1, 4)))
    return f"# {heading}\n\nUsed: {use}\n\n" + "\n\n".join(defs) + "\n"


def test_cached_term_texts_equal_the_texts_worked_out_afresh(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = random.Random(20261027)
    cached_original = Resolver._while_open
    for _ in range(150):
        source = FRONT + _graph(rng)
        for scheme in ("decimal", "none"):
            overrides = {"numbering.scheme": scheme}
            monkeypatch.setattr(Resolver, "_while_open", cached_original)
            cached = render(source, format="text", overrides=overrides)
            monkeypatch.setattr(Resolver, "_while_open", _oracle)
            fresh = render(source, format="text", overrides=overrides)
            assert cached.output == fresh.output, source
            assert [(d.rule, d.message) for d in cached.diagnostics] == \
                [(d.rule, d.message) for d in fresh.diagnostics], source


def test_a_text_that_named_a_term_is_not_reused_where_that_term_is_open() -> None:
    # ca's text names cb. Worked out with nothing open it is "Alpha Beta ca"; with cb open it is
    # "Alpha cb", and that is what a reference inside cb must read.
    body = ('# A\n\nFirst {{term: ca}}. Then {{term: cb}}.\n\n"Alpha {{term: cb}}" {{def: ca}}\n\n'
            '"Beta {{term: ca}}" {{def: cb}}\n')
    out = render(FRONT + body, format="text").output
    assert "First Alpha Beta ca. Then Beta Alpha cb." in out


# -- 4: control characters never reach stderr ----------------------------------------


def _run(source: str, tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    document = tmp_path / "d.lgd"
    document.write_text(source, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    return subprocess.run([sys.executable, "-m", "legaldown_render.cli", str(document), "-f", "text", *arguments],
                          capture_output=True, cwd=tmp_path, env=env, check=False)


def test_no_control_character_from_the_document_reaches_stderr(tmp_path: Path) -> None:
    body = ("# A {#a}\n\n{{include: a\x1b]0;pwn\x07.lgd}}\n\n{{ref: x\x1b[2Jy}}\n\n{{term: q\x1b[31m}}\n\n"
            "See {{ref: a}}.\n")
    result = _run(FRONT + body, tmp_path)
    errors = result.stderr.decode("utf-8")
    assert errors.count("\n") >= 3  # the three findings, one line each
    assert not any(ord(c) < 0x20 and c != "\n" or 0x7F <= ord(c) < 0xA0 for c in errors)
    assert "\\x1b" in errors  # written out, not dropped
    assert "pwn" in errors
