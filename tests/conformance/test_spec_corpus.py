"""Render the LegalDown specification's examples and fixtures corpus.

Needs a checkout of the specification: set $LEGALDOWN_SPEC_DIR, or check it
out next to this repository as ../LegalDown. Skipped otherwise.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from conftest import spec_root

from legaldown_render import DocumentError, render

pytestmark = pytest.mark.conformance

SPEC = spec_root()
if SPEC is None:
    pytest.skip("LegalDown specification checkout not found", allow_module_level=True)

EXAMPLES = sorted((SPEC / "examples").rglob("*.lgd"))
FIXTURES = sorted((SPEC / "fixtures").rglob("*.lgd"))

#: The failure marker a fixture for each rule must render (§11.5, §13).
MARKERS = {
    "ref-broken": "[BROKEN REF: ",
    "ref-targets-attachment": "[BROKEN REF: ",
    "term-undefined": "[UNDEFINED: ",
    "date-invalid": "[INVALID DATE: ",
    "money-invalid-amount": "[INVALID AMOUNT",
    "money-unknown-currency": "[UNKNOWN CURRENCY: ",
    "party-unknown": "[UNKNOWN PARTY: ",
    "side-unknown": "[UNKNOWN SIDE: ",
    "duration-invalid-value": "[INVALID DURATION: ",
    "duration-invalid-unit": "[INVALID DURATION UNIT",
    "field-type-missing": "[INVALID FIELD",
    "directive-unknown": "[UNKNOWN DIRECTIVE: ",
    "attach-undeclared": "[UNKNOWN ATTACHMENT: ",
    "placeholder-unknown-currency": "[UNKNOWN CURRENCY: ",
    "placeholder-type-invalid": "[INVALID PLACEHOLDER",
    "placeholder-id-malformed": "[INVALID PLACEHOLDER",
}


def _name(path: Path) -> str:
    return str(path.relative_to(SPEC))


@pytest.mark.parametrize("path", EXAMPLES + FIXTURES, ids=_name)
def test_renders_without_crashing(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    try:
        html = render(source).output
        render(source, format="text")
    except DocumentError:
        assert "frontmatter-invalid-yaml" in str(path) or "frontmatter" in str(path)
        return
    ids = set(re.findall(r' id="([^"]+)"', html))
    for target in re.findall(r'href="#([^"]+)"', html):
        assert target in ids, f"link to missing anchor #{target}"


def _entries(folder: Path) -> list[Path]:
    """The documents a fixture folder's expectations name: ``<case>.lgd``
    beside ``<case>.expected.json``, or the ``entry`` of a multi-file case."""
    entries = [case for case in folder.glob("*.lgd") if case.with_suffix(".expected.json").exists()]
    multi = folder / "expected.json"
    if multi.exists():
        entries.append(folder / json.loads(multi.read_text(encoding="utf-8")).get("entry", "main.lgd"))
    return sorted(entries)


@pytest.mark.parametrize("rule", sorted(MARKERS))
def test_invalid_fixtures_render_their_markers(rule: str) -> None:
    cases = _entries(SPEC / "fixtures" / "invalid" / rule)
    if not cases:
        pytest.skip(f"no fixture for {rule}")
    for case in cases:
        output = render(case.read_text(encoding="utf-8"), format="text").output
        assert MARKERS[rule] in output, f"{case.name} does not render {MARKERS[rule]!r}"


def test_raw_html_fixture_emits_no_html() -> None:
    for case in _entries(SPEC / "fixtures" / "invalid" / "raw-html"):
        result = render(case.read_text(encoding="utf-8"), standalone=False)
        body = re.sub(r"<(/?)(article|header|h\d|p|span|section|div|dl|dt|dd|a|dfn|mark|em|strong|code|pre|ol|ul|li|table|thead|tbody|tr|th|td|blockquote|aside|hr|br)\b[^>]*>", "", result.output)
        assert "<" not in body, case.name
        assert "raw-html" in {d.rule for d in result.diagnostics}
