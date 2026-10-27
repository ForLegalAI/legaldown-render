"""Golden files: the test documents rendered under each built-in style,
compared byte for byte. Regenerate on purpose with ``pytest --update-golden``
and review the diff like any other change."""
from __future__ import annotations

import pytest
from conftest import DOCUMENTS, GOLDEN

from legaldown_render import render

CASES = [
    ("features", "default", "text"),
    ("features", "default", "html"),
    ("features", "continental", "text"),
    ("features", "outline", "text"),
    ("template", "default", "text"),
    ("template", "default", "html"),
]


@pytest.mark.parametrize(("document", "style", "output_format"), CASES)
def test_golden(document: str, style: str, output_format: str, update_golden: bool) -> None:
    source = (DOCUMENTS / f"{document}.lgd").read_text(encoding="utf-8")
    output = render(source, format=output_format, style=style).output
    extension = "txt" if output_format == "text" else output_format
    golden = GOLDEN / f"{document}.{style}.{extension}"
    if update_golden or not golden.exists():
        # LF on every platform: the CLI writes LF, and .gitattributes checks the goldens out as LF.
        golden.write_text(output, encoding="utf-8", newline="\n")
        if not update_golden:
            pytest.fail(f"{golden.name} did not exist and was written; review it and run again")
    # Bytes, not read_text: universal newlines would hide a golden checked out with CRLF.
    assert output == golden.read_bytes().decode("utf-8")
