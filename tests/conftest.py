from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from legaldown_render import render

ROOT = Path(__file__).parent
SRC = Path(__file__).resolve().parents[1] / "src"
DOCUMENTS = ROOT / "documents"
GOLDEN = ROOT / "golden"
FEATURES = str(DOCUMENTS / "features.lgd")

#: The smallest valid header, for tests whose body is all that matters.
FRONT = "---\ntitle: T\n---\n"


def text(body: str, **settings: object) -> str:
    """The plain-text rendering of *body* under :data:`FRONT`."""
    return render(FRONT + body, format="text", **settings).output


def html(body: str, **settings: object) -> str:
    """The HTML rendering of *body* under :data:`FRONT`."""
    return render(FRONT + body, format="html", **settings).output


def run_cli(*args: str, stdin: bytes | str | None = None, cwd: Path | None = None,
            narrow: bool = False) -> subprocess.CompletedProcess[bytes]:
    """Run ``legaldown-render`` as a program on this checkout's sources.

    *narrow* gives it a Windows-like console, where neither stream is UTF-8."""
    env = dict(os.environ, PYTHONPATH=str(SRC))
    if narrow:
        env.update(PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
    data = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
    return subprocess.run([sys.executable, "-m", "legaldown_render.cli", *args], input=data,
                          capture_output=True, cwd=cwd, env=env, check=False)


def run_cli_text(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Like :func:`run_cli`, with UTF-8 text for the streams."""
    env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONUTF8="1")
    return subprocess.run([sys.executable, "-m", "legaldown_render.cli", *args], input=stdin,
                          capture_output=True, text=True, env=env, check=False)


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
