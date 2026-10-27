"""The exception hierarchy: every error the renderer raises is a
``RenderError``, ``StyleError`` and ``OptionsError`` are still ``ValueError``,
``StyleError`` is importable from where it always was, and the errors survive
pickling (for a process pool)."""
from __future__ import annotations

import pickle
from pathlib import Path

import pytest
from conftest import FEATURES
from legaldown import Diagnostic

from legaldown_render import OptionsError, RenderError, RenderOptions, RenderRefused, StyleError, render

# -- Hierarchy -------------------------------------------------------------------

def test_style_error_is_a_render_error_and_still_a_value_error() -> None:
    assert issubclass(StyleError, RenderError)
    assert issubclass(StyleError, ValueError)


def test_style_error_is_importable_from_where_it_always_was() -> None:
    from legaldown_render import errors
    from legaldown_render.style import StyleError as from_style
    from legaldown_render.style.loader import StyleError as from_loader

    assert errors.StyleError is StyleError is from_style is from_loader


def test_unknown_format_raises_options_error() -> None:
    assert issubclass(OptionsError, RenderError)
    assert issubclass(OptionsError, ValueError)
    with pytest.raises(OptionsError, match="unknown output format 'pdf'"):
        render("# x\n", RenderOptions(format="pdf"))
    with pytest.raises(OptionsError):
        render("# x\n", format="docx")
    with pytest.raises(RenderError):
        render("# x\n", format="docx")


def test_invalid_style_through_the_api_is_a_render_error() -> None:
    with pytest.raises(RenderError):
        render("# x\n", style="no-such-style")


# -- Pickling --------------------------------------------------------------------

def test_render_refused_round_trips_through_pickle() -> None:
    diagnostics = [Diagnostic(rule="ref-broken", level="error", message="No such target", line=3)]
    for reason in (None, "Assembly refused: 1 error(s)."):
        error = RenderRefused(diagnostics, reason)
        copy = pickle.loads(pickle.dumps(error))
        assert type(copy) is RenderRefused
        assert copy.diagnostics == diagnostics
        assert str(copy) == str(error)
        assert copy.reason == error.reason


def test_a_real_refusal_round_trips_through_pickle() -> None:
    with pytest.raises(RenderRefused) as caught:
        render(Path(FEATURES).read_text(encoding="utf-8"), strict=True)
    copy = pickle.loads(pickle.dumps(caught.value))
    assert copy.diagnostics == caught.value.diagnostics
    assert str(copy) == str(caught.value)


def test_style_error_round_trips_through_pickle() -> None:
    error = StyleError("overrides", ["a.b: wrong", "c: also wrong"])
    copy = pickle.loads(pickle.dumps(error))
    assert type(copy) is StyleError
    assert (copy.source, copy.problems) == ("overrides", ["a.b: wrong", "c: also wrong"])
    assert str(copy) == str(error)


def test_options_error_round_trips_through_pickle() -> None:
    error = OptionsError("unknown output format 'pdf'")
    assert str(pickle.loads(pickle.dumps(error))) == str(error)
