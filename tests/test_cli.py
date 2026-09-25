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


def test_final_with_strict_exits_1(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    template = str(DOCUMENTS / "template.lgd")
    assert main([template, "--final", "--quiet", "-o", str(tmp_path / "out.html")]) == 0
    assert main([template, "--final", "--strict"]) == 1
    assert "template-construct-present" in capsys.readouterr().err


def test_answers_file(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    template = str(DOCUMENTS / "template.lgd")
    answers = tmp_path / "answers.yaml"
    answers.write_text("client-name: Beta Ltd\nfee: '5000.00'\nnon-solicit: false\nforum: courts\n", encoding="utf-8")
    assert main([template, "--answers", str(answers), "-f", "text", "-q"]) == 0
    output = capsys.readouterr().out
    assert "Disputes are resolved by the courts." in output and "arbitration" not in output

    answers.write_text("forum: mediation\n", encoding="utf-8")
    assert main([template, "--answers", str(answers), "-f", "text"]) == 1
    assert "answer-invalid" in capsys.readouterr().err


@pytest.mark.parametrize(("content", "message"), [
    (None, "cannot read"),
    ("- a list\n", "must be a mapping"),
    ("date: 2026-13-45\n", "cannot read the answers"),
])
def test_unreadable_answers_exit_1(capsys: pytest.CaptureFixture[str], tmp_path: Path, content: str | None,
                                   message: str) -> None:
    answers = tmp_path / "answers.yaml"
    if content is not None:
        answers.write_text(content, encoding="utf-8")
    assert main([str(DOCUMENTS / "template.lgd"), "--answers", str(answers)]) == 1
    assert message in capsys.readouterr().err


def test_print_and_list_styles(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--print-style", "--style", "continental", "--set", "locale=de"]) == 0
    printed = capsys.readouterr().out
    assert "name: continental" in printed
    assert "locale: de" in printed
    assert main(["--list-styles"]) == 0
    assert "default" in capsys.readouterr().out.split()


def test_set_keeps_text_values_as_written(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([FEATURES, "-f", "text", "-q", "--set", "placeholders.blank=[__]",
                 "--set", "references.format=§ {designation}"]) == 0
    assert "under Section § 2.1." in capsys.readouterr().out


def test_quote_bounded_literal_setting_is_kept(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([FEATURES, "-f", "text", "-q", "--set", 'references.format="§" {designation} "x"']) == 0
    assert 'under Section "§" 2.1 "x".' in capsys.readouterr().out
