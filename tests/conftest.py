from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
DOCUMENTS = ROOT / "documents"
GOLDEN = ROOT / "golden"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--update-golden", action="store_true",
                     help="rewrite the golden files from the current output instead of comparing")


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))


def spec_root() -> Path | None:
    """A checkout of the LegalDown specification, for the conformance tests:
    $LEGALDOWN_SPEC_DIR, else a sibling ../LegalDown checkout."""
    candidates = [os.environ.get("LEGALDOWN_SPEC_DIR"), ROOT.parent.parent / "LegalDown"]
    for candidate in candidates:
        if candidate and (Path(candidate) / "fixtures").is_dir():
            return Path(candidate)
    return None
