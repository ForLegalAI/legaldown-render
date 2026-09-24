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
        contents="Contents",
        colon=": ",
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
        contents="Obsah",
        colon=": ",
        attachment_file="Soubor: {file}",
        signatures="Podpisy",
        signature_date="Datum",
        signature_place="Místo",
        signature_name="Jméno",
        signature_title="Funkce",
    ),
    "de": Labels(
        drafting_note="Bearbeitungshinweis",
        condition="Nur wenn: {condition}",
        identification_number="Registernummer",
        date_of_birth="Geburtsdatum",
        address="Anschrift",
        represented_by="Vertreten durch",
        effective_date="Inkrafttreten",
        version="Version",
        attachments="Anlagen",
        contents="Inhaltsverzeichnis",
        colon=": ",
        attachment_file="Datei: {file}",
        signatures="Unterschriften",
        signature_date="Datum",
        signature_place="Ort",
        signature_name="Name",
        signature_title="Funktion",
    ),
    # French puts a no-break space before a colon.
    "fr": Labels(
        drafting_note="Note de rédaction",
        condition="Uniquement si\u00a0: {condition}",
        identification_number="N° d'immatriculation",
        date_of_birth="Date de naissance",
        address="Adresse",
        represented_by="Représentée par",
        effective_date="Date d'effet",
        version="Version",
        attachments="Annexes",
        contents="Table des matières",
        colon="\u00a0: ",
        attachment_file="Fichier\u00a0: {file}",
        signatures="Signatures",
        signature_date="Date",
        signature_place="Lieu",
        signature_name="Nom",
        signature_title="Qualité",
    ),
    "pl": Labels(
        drafting_note="Uwaga redakcyjna",
        condition="Tylko jeżeli: {condition}",
        identification_number="Nr rejestrowy",
        date_of_birth="Data urodzenia",
        address="Adres",
        represented_by="Reprezentacja",
        effective_date="Data wejścia w życie",
        version="Wersja",
        attachments="Załączniki",
        contents="Spis treści",
        colon=": ",
        attachment_file="Plik: {file}",
        signatures="Podpisy",
        signature_date="Data",
        signature_place="Miejsce",
        signature_name="Imię i nazwisko",
        signature_title="Stanowisko",
    ),
    "sk": Labels(
        drafting_note="Poznámka pre spracovateľa",
        condition="Iba ak: {condition}",
        identification_number="IČO",
        date_of_birth="Dátum narodenia",
        address="Adresa",
        represented_by="Zastúpenie",
        effective_date="Dátum účinnosti",
        version="Verzia",
        attachments="Prílohy",
        contents="Obsah",
        colon=": ",
        attachment_file="Súbor: {file}",
        signatures="Podpisy",
        signature_date="Dátum",
        signature_place="Miesto",
        signature_name="Meno",
        signature_title="Funkcia",
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
