"""Built-in labels per document language (§13.7: generated words follow the
document's language). A style's ``labels`` override these one by one."""
from __future__ import annotations

from dataclasses import fields, replace

from .model import Labels

BUILTIN_LABELS: dict[str, Labels] = {
    "en": Labels(
        drafting_note="Drafting note",
        condition="Only if: {condition}",
        identification_number="Registration No.",
        date_of_birth="Date of birth",
        address="Address",
        represented_by="Represented by",
        effective_date="Effective date",
        version="Version",
        attachments="Attachments",
        attachment_file="File: {file}",
        signatures="Signatures",
        signature_date="Date",
        signature_place="Place",
        signature_name="Name",
        signature_title="Title",
    ),
    "cs": Labels(
        drafting_note="Poznámka pro zpracovatele",
        condition="Pouze pokud: {condition}",
        identification_number="IČO",
        date_of_birth="Datum narození",
        address="Adresa",
        represented_by="Zastoupení",
        effective_date="Datum účinnosti",
        version="Verze",
        attachments="Přílohy",
        attachment_file="Soubor: {file}",
        signatures="Podpisy",
        signature_date="Datum",
        signature_place="Místo",
        signature_name="Jméno",
        signature_title="Funkce",
    ),
}


def effective_labels(labels: Labels, language: str) -> Labels:
    """*labels* with every unset label taken from the built-in labels for
    *language* (its primary subtag, e.g. ``cs`` for ``cs-CZ``), falling back
    to English."""
    primary = (language or "en").replace("_", "-").split("-")[0].lower()
    builtin = BUILTIN_LABELS.get(primary, BUILTIN_LABELS["en"])
    english = BUILTIN_LABELS["en"]
    values = {}
    for item in fields(Labels):
        value = getattr(labels, item.name)
        if value is None:
            value = getattr(builtin, item.name) or getattr(english, item.name)
        values[item.name] = value
    return replace(labels, **values)
