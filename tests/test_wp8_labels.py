"""WP8 (S13): the party identifier label is neutral, since §3.4's
``identification_number`` also covers a natural person's national ID."""
from __future__ import annotations

import pytest

from legaldown_render import render
from legaldown_render.style.labels import BUILTIN_LABELS

DOCUMENT = """---
legaldown: "0.2"
title: T
language: {language}
sides:
  - name: s1
    parties:
      - name: a
        type: natural_person
        legal_name: Jan Novak
        identification_number: "800101/1234"
        representatives:
          - {{name: Eva, title: Jednatel}}
---
# A
"""


@pytest.mark.parametrize(("language", "identifier", "represented_by"), [
    ("cs", "Identifikační číslo", "Zastoupení"),
    ("sk", "Identifikačné číslo", "Zastúpenie"),
    ("fr", "N° d'immatriculation", "Représenté(e) par"),
    ("pl", "Nr rejestrowy", "Reprezentacja"),
])
def test_party_labels_in_each_language(language: str, identifier: str, represented_by: str) -> None:
    colon = BUILTIN_LABELS[language].colon
    output = render(DOCUMENT.format(language=language), format="text").output
    assert f"{identifier}{colon}800101/1234" in output
    assert f"{represented_by}{colon}Eva, Jednatel" in output


def test_cs_and_sk_do_not_call_every_party_identifier_an_ico() -> None:
    assert "IČO" not in {BUILTIN_LABELS["cs"].identification_number, BUILTIN_LABELS["sk"].identification_number}
