"""Terms that name each other, and how much is worked out for them (§7.3).

A text worked out for a term is kept and reused only where it would read the
same (what it shows depends on which terms are open around it), so a chain of
terms is worked out once per term, not once per use. Terms that name each
other in a tangle have more texts than any cache holds, so the work is
limited: a text worked out again is charged to a budget, and past it, or past a
depth of nested terms, a reference shows its definition id and the Warning
``render-term-limit`` says so. A term text is also capped in length
(``render-term-too-long``).

The tests of cost do not time the render, which depends on the machine. They
count the directives the resolver resolves, in the text-only pass for headings
as in the body (the ``resolver_work`` fixture), and bound that count, so a lost
cache, budget or depth limit fails the same way everywhere. Each bound sits
25 to 60 % above the work measured when the test was written, far below what
the document costs without the protection.
"""
from __future__ import annotations

import random
from collections.abc import Callable

import pytest
from conftest import FRONT, text

from legaldown_render import RenderResult, render
from legaldown_render.resolve.resolver import Resolver

WorkCounter = Callable[..., tuple[RenderResult, int]]

LIMIT = ("Defined terms refer to each other too deeply or too often to resolve fully; "
         "some {{term:}} references show their definition id.")

#: The most directives a document of k terms that all name each other may cost:
#: the work budget is 200 000 inline nodes, about half of them directives.
TANGLE_DIRECTIVES = 130_000


@pytest.fixture
def resolver_work(monkeypatch: pytest.MonkeyPatch) -> WorkCounter:
    """``work(body, limit, **settings)`` renders *body* as plain text and
    returns the result with the number of directives the resolver resolved.
    A render that resolves more than *limit* is stopped and fails, so that a
    lost protection fails at once instead of running for hours."""
    state = {"directives": 0, "limit": 0}
    real = Resolver._directive

    def counting(self: Resolver, directive: object):  # type: ignore[no-untyped-def]
        state["directives"] += 1
        if state["directives"] > state["limit"]:
            pytest.fail(f"more than {state['limit']} directives resolved: the work is not bounded", pytrace=False)
        return real(self, directive)  # type: ignore[arg-type]

    monkeypatch.setattr(Resolver, "_directive", counting)

    def work(body: str, limit: int, **settings: object) -> tuple[RenderResult, int]:
        state.update(directives=0, limit=limit)
        result = render(FRONT + body, format="text", **settings)  # type: ignore[arg-type]
        return result, state["directives"]

    return work


def messages(result: RenderResult, rule: str) -> list[str]:
    return [d.message for d in result.diagnostics if d.rule == rule]


def _chain(n: int, *, cycle: bool, heading: bool = False) -> str:
    """Terms t0..tn, each reading the next one twice; the last one reads t0 when *cycle*."""
    defs = [f'"T{i} {{{{term: t{i + 1}}}}} {{{{term: t{i + 1}}}}}" {{{{def: t{i}}}}}' for i in range(n)]
    defs.append(f'"T{n}{" {{term: t0}}" if cycle else ""}" {{{{def: t{n}}}}}')
    title = "About {{term: t0}}" if heading else "A"
    return f"# {title}\n\nUsed: {{{{term: t0}}}}\n\n" + "\n\n".join(defs) + "\n"


def _chain_directives(n: int, *, cycle: bool) -> int:
    """A bound on the directives a chain of *n* terms takes: about 6 n; read
    round in a cycle each term has a text for each set of open terms, and the
    work is about 2.5 n squared."""
    return 4 * n * n + 30 if cycle else 8 * n + 30


def _line(n: int, *, heading: bool) -> str:
    """Terms t0..tn, each reading the next one once."""
    defs = [f'"T{i} {{{{term: t{i + 1}}}}}" {{{{def: t{i}}}}}' for i in range(n)] + [f'"End" {{{{def: t{n}}}}}']
    title = "About {{term: t0}}" if heading else "A"
    return f"# {title}\n\nUsed: {{{{term: t0}}}}\n\n" + "\n\n".join(defs) + "\n"


def _tangle(k: int) -> str:
    defs = [f'"T{i} {" ".join(f"{{{{term: t{j}}}}}" for j in range(k))}" {{{{def: t{i}}}}}' for i in range(k)]
    return "# A\n\nUsed: {{term: t0}}\n\n" + "\n\n".join(defs) + "\n"


def _ring(k: int, n: int) -> str:
    """k terms in a cycle, each holding n references to the next."""
    defs = [f'"T{i} {" ".join(f"{{{{term: t{(i + 1) % k}}}}}" for _ in range(n))}" {{{{def: t{i}}}}}' for i in range(k)]
    return "# A\n\nUsed: {{term: t0}}\n\n" + "\n\n".join(defs) + "\n"


def _independent(n: int, *, heading: bool) -> str:
    parts = []
    for i in range(n):
        if heading:
            parts.append(f"## H{i} {{{{term: t{i}}}}}\n\n")
        parts.append(f'"Term {i}" {{{{def: t{i}}}}} used {{{{term: t{i}}}}}\n\n')
    return "# A\n\n" + "".join(parts)


# -- A text is worked out once per term ---------------------------------------


@pytest.mark.parametrize("heading", [False, True])
@pytest.mark.parametrize("scheme", ["decimal", "none"])
@pytest.mark.parametrize("cycle", [False, True])
def test_a_branching_chain_of_terms_is_worked_out_once_per_term(
        resolver_work: WorkCounter, cycle: bool, scheme: str, heading: bool) -> None:
    # Each term reads the next twice: worked out afresh at each use, 20 terms is 2**20 resolutions.
    # The text of t0 is itself 2**n words long (4**n with a cycle, which reads the chain round
    # twice), so the chains cannot be longer. Under the none scheme a heading is read as text
    # before the targets are known (§13.3).
    n = 9 if cycle else 20
    result, _ = resolver_work(_chain(n, cycle=cycle, heading=heading), _chain_directives(n, cycle=cycle),
                              overrides={"numbering.scheme": scheme})
    assert "{{" not in result.output


@pytest.mark.parametrize("scheme", ["decimal", "none"])
def test_doubling_a_chain_of_terms_doubles_the_work(resolver_work: WorkCounter, scheme: str) -> None:
    directives = {n: resolver_work(_chain(n, cycle=False, heading=True), _chain_directives(n, cycle=False),
                              overrides={"numbering.scheme": scheme})[1] for n in (10, 20, 40)}
    assert directives[20] <= 2.2 * directives[10]
    assert directives[40] <= 2.2 * directives[20]


def test_a_chain_without_a_cycle_reads_as_each_term_does() -> None:
    lines = text(_chain(3, cycle=False)).splitlines()
    used = next(line for line in lines if line.startswith("Used: "))
    # t1 as used inside t0 is the same text as at t1's own definition.
    site = next(line for line in lines if line.startswith("T1 "))
    assert used == f"Used: T0 {site} {site}"


@pytest.mark.parametrize(("n", "cycle"), [(40, False), (14, True)])
@pytest.mark.parametrize("scheme", ["decimal", "none"])
def test_a_branching_chain_of_terms_is_capped_and_reported(
        resolver_work: WorkCounter, n: int, cycle: bool, scheme: str) -> None:
    result, _ = resolver_work(_chain(n, cycle=cycle, heading=True), _chain_directives(n, cycle=cycle),
                              overrides={"numbering.scheme": scheme})
    assert len(result.output) < 100_000
    too_long = messages(result, "render-term-too-long")
    assert too_long
    assert len(too_long) == len(set(too_long))  # one per id
    assert all("longer than 1000 characters" in message and "shows the definition id" in message
               for message in too_long)
    assert "{{" not in result.output


@pytest.mark.parametrize("scheme", ["decimal", "none"])
@pytest.mark.parametrize("heading", [False, True])
def test_3000_independent_terms_show_their_text_and_no_warning(
        resolver_work: WorkCounter, scheme: str, heading: bool) -> None:
    result, directives = resolver_work(_independent(3000, heading=heading), 4 * 3000 + 100,
                                  overrides={"numbering.scheme": scheme})
    assert messages(result, "render-term-limit") == []
    lines = result.output.splitlines()
    assert "Term 2999 used Term 2999" in lines
    assert "Term 0 used Term 0" in lines
    assert not any(line.endswith(" used t2999") for line in lines)
    if heading and scheme == "none":
        assert any(line.endswith("H2999 Term 2999") for line in lines)
    _, half = resolver_work(_independent(1500, heading=heading), 4 * 1500 + 100,
                            overrides={"numbering.scheme": scheme})
    assert directives <= 2.2 * half

# -- A cached text is reused only where it would read the same -----------------

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


# -- Terms that all name each other: the work budget ----------------------------


@pytest.mark.parametrize("k", [3, 7, 9, 12, 20, 40])
def test_k_terms_that_each_name_all_k_stay_within_the_work_budget(resolver_work: WorkCounter, k: int) -> None:
    result, _ = resolver_work(_tangle(k), 150 if k == 3 else TANGLE_DIRECTIVES)
    assert "{{" not in result.output
    if k >= 9:
        assert messages(result, "render-term-limit") == [LIMIT]


def test_a_small_cycle_is_resolved_in_full_without_the_warning() -> None:
    assert messages(render(FRONT + _tangle(3), format="text"), "render-term-limit") == []


def test_a_ring_of_terms_with_many_references_costs_a_small_multiple_of_the_document(
        resolver_work: WorkCounter) -> None:
    document = _ring(12, 2000)
    # The same document with the references pointing at a term that names nothing.
    flat = document
    for i in range(12):
        flat = flat.replace(f"{{{{term: t{i}}}}}", "{{term: leaf}}")
    flat += '\n"Leaf" {{def: leaf}}\n'
    _, acyclic = resolver_work(flat, 100_000, overrides={"numbering.scheme": "none"})
    result, _ = resolver_work(document, 10 * acyclic, overrides={"numbering.scheme": "none"})
    assert messages(result, "render-term-limit")


# -- A long chain of terms: the depth limit ------------------------------------


@pytest.mark.parametrize("scheme", ["decimal", "none"])
@pytest.mark.parametrize("heading", [False, True])
def test_a_chain_of_500_terms_does_not_exhaust_the_stack(
        resolver_work: WorkCounter, scheme: str, heading: bool) -> None:
    result, directives = resolver_work(_line(500, heading=heading), 3 * 500 + 50,
                                       overrides={"numbering.scheme": scheme})
    assert messages(result, "render-term-limit") == [LIMIT]
    used = next(line for line in result.output.splitlines() if line.startswith("Used: "))
    assert used.startswith("Used: T0 T1 T2 ")
    assert "{{" not in result.output
    _, half = resolver_work(_line(250, heading=heading), 3 * 250 + 50, overrides={"numbering.scheme": scheme})
    assert directives <= 2.2 * half


def test_a_chain_inside_the_depth_limit_is_resolved_in_full() -> None:
    n = 20
    result = render(FRONT + _line(n, heading=False), format="text")
    assert f"Used: {' '.join(f'T{i}' for i in range(n))} End" in result.output
    assert messages(result, "render-term-limit") == []
