"""Test helper: the validator's own template decision, for checking the
renderer's copy of its formula (validator_bridge._is_template)."""
from __future__ import annotations

import inspect
from unittest import mock

from legaldown import Document, validate_document
from legaldown.validator import core


def validator_template(document: Document) -> bool:
    """The ``template`` value validate_document computes and passes to its
    Units — captured with a spy, since the validator does not expose it yet
    (ForLegalAI/legaldown-validator#26)."""
    seen: list[bool] = []
    original = core.Units
    signature = inspect.signature(original)
    assert "template" in signature.parameters, "Units no longer takes 'template'; update this spy"

    def spy(*args: object, **kwargs: object) -> object:
        # However the validator passes it, by position or by name.
        seen.append(bool(signature.bind(*args, **kwargs).arguments["template"]))
        return original(*args, **kwargs)

    with mock.patch.object(core, "Units", spy):
        validate_document(document)
    assert seen, "validate_document no longer builds Units; update this spy"
    return seen[-1]
