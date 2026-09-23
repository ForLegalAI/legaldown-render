from __future__ import annotations

from pathlib import Path

import pytest
from conftest import DOCUMENTS

from legaldown_render.cli import main

FEATURES = str(DOCUMENTS / "features.lgd")


def test_render_to_file_infers_the_format(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out.txt"
    assert main([FEATURES, "-o", str(out)]) == 0
    assert out.read_text().startswith("Services Agreement")
    assert "[ref-broken]" in capsys.readouterr().err


def test_style_set_and_locale(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([FEATURES, "-f", "text", "-q", "--style", "outline", "--set", "enumeration.enabled=false",
                 "--locale", "cs-CZ"]) == 0
    output = capsys.readouterr().out
    assert "II. Services" in output
    assert "1. června 2026" in output


def test_bad_setting_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([FEATURES, "--set", "numbring.scheme=none"]) == 2
    assert "did you mean 'numbering'" in capsys.readouterr().err


def test_strict_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([FEATURES, "--strict"]) == 1
    assert "refused" in capsys.readouterr().err


def test_print_and_list_styles(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--print-style", "--style", "continental", "--set", "locale=de"]) == 0
    printed = capsys.readouterr().out
    assert "name: continental" in printed
    assert "locale: de" in printed
    assert main(["--list-styles"]) == 0
    assert "default" in capsys.readouterr().out.split()
