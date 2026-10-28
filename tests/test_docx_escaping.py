"""Nothing a document, a style or the command line supplies makes the DOCX malformed: every part
of the package is well-formed XML for any text, with the characters XML cannot carry (C0
controls, DEL, C1, U+FFFE and U+FFFF, lone surrogates) dropped by the one sanitiser
(docs/architecture.md, Security). ``xml.etree`` alone would write a control character raw and a
lone surrogate as a character reference, and Word then reports unreadable content."""
from __future__ import annotations

import random
import xml.etree.ElementTree as ET

import pytest
import yaml
from docx_support import parts
from test_escaping import ESC, EVIL

from legaldown_render import render

#: Characters that break XML, text, or Word's reading of it.
HOSTILE = [chr(code) for code in (*range(0x20), 0x7F, 0x80, 0x85, 0x9F, 0xD800, 0xDBFF, 0xDC00, 0xDFFF, 0xFFFE, 0xFFFF,
                                   0x2028, 0x2029, 0x202E, 0x200B, 0xFEFF)]
MARKUP = list("<>&\"'`[]()\\*_#|~$%@!") + ["]]>", "<![CDATA[", "<?xml", "&amp;", "&#0;", "&#x1;", "{{ref: x}}"]
TEXT = ["ž", "€", "\U0001f600", " ", "a", "Z"]
ALPHABET = HOSTILE + MARKUP + TEXT

#: Style settings that are free text, so a trusted style may put anything in them.
FREE_TEXT_SETTINGS = ("page.header.left", "page.header.center", "page.header.right", "page.footer.left",
                      "page.footer.center", "page.footer.right")


def soup(rnd: random.Random, length: int) -> str:
    return "".join(rnd.choice(ALPHABET) for _ in range(length))


def document(rnd: random.Random) -> str:
    front = yaml.safe_dump({"legaldown": "0.2", "title": soup(rnd, 25), "subtitle": soup(rnd, 25),
                            "version": soup(rnd, 8), "effective_date": soup(rnd, 8)}, allow_unicode=True)
    return (f"---\n{front}---\n\n{soup(rnd, 40)}\n\n# {soup(rnd, 20)}\n\n{soup(rnd, 80)}\n\n## {soup(rnd, 15)}\n\n"
            f"- {soup(rnd, 30)}\n  - {soup(rnd, 30)}\n\n> {soup(rnd, 30)}\n\n```\n{soup(rnd, 50)}\n```\n\n"
            f"| {soup(rnd, 5)} | {soup(rnd, 5)} |\n|---|---|\n| {soup(rnd, 5)} | {soup(rnd, 5)} |\n\n"
            f"[{soup(rnd, 8)}](https://example.com/{soup(rnd, 8)})\n")


def assert_well_formed(data: bytes) -> None:
    for name, content in parts(data).items():
        try:
            ET.fromstring(content)
            content.decode("utf-8")
        except (ET.ParseError, UnicodeDecodeError) as error:
            pytest.fail(f"{name} is not well-formed UTF-8 XML: {error}")


def test_the_premise_unsanitised_xml_would_be_malformed() -> None:
    element = ET.Element("t")
    element.text = "a\x01b"
    with pytest.raises(ET.ParseError):
        ET.fromstring(ET.tostring(element))
    element.text = "a\ud800b"
    with pytest.raises((UnicodeEncodeError, ET.ParseError)):
        ET.fromstring(ET.tostring(element, encoding="utf-8"))


@pytest.mark.parametrize("seed", range(40))
def test_every_part_is_well_formed_for_any_text(seed: int) -> None:
    rnd = random.Random(seed)
    overrides = {key: soup(rnd, 12) + "{page}" + soup(rnd, 4) + "{pages}{title}" for key in FREE_TEXT_SETTINGS}
    result = render(document(rnd), format="docx", overrides=overrides)
    assert result.binary is not None
    assert_well_formed(result.binary)


@pytest.mark.parametrize("hostile", [ESC, EVIL, "\x1b[2J\x07\x00", "\ud800", "a\ud800b\udfffc", "\ufffe\uffff", "]]>",
                                     "<script>alert(1)</script>", "&#x0;&#1;", "\x85 "])
def test_a_hostile_string_in_each_place_is_harmless(hostile: str) -> None:
    quoted = yaml.safe_dump(hostile, allow_unicode=True).rstrip("\n").removesuffix("\n...").rstrip("\n")
    source = (f"---\nlegaldown: '0.2'\ntitle: {quoted}\nsubtitle: {quoted}\n---\n\n# {hostile}\n\n{hostile} "
              f"[{hostile}](<https://example.com/{hostile}>)\n\n```\n{hostile}\n```\n\n| {hostile} |\n|---|\n| {hostile} |\n")
    overrides = {key: hostile + "{page}" for key in FREE_TEXT_SETTINGS}
    assert_well_formed(render(source, format="docx", overrides=overrides).binary or b"")


def test_nothing_from_the_document_is_left_as_a_control_character() -> None:
    source = "---\nlegaldown: '0.2'\ntitle: 'a\\x01b'\n---\n\n# H\x01\x08\x0b\x0c\x1f\x7f\x85x\n\nbody\x01\n"
    contents = parts(render(source, format="docx").binary or b"")
    for name, content in contents.items():
        text = content.decode("utf-8")
        assert not any(ord(c) < 0x20 and c not in "\t\n\r" or 0x7F <= ord(c) <= 0x9F for c in text), name
        assert "\ud800" not in text, name


def test_a_header_template_with_a_control_character_is_sanitised_not_refused() -> None:
    contents = parts(render("---\ntitle: T\n---\n# A\n", format="docx",
                            overrides={"page.footer.center": "a\x00b\x1bc{page}"}).binary or b"")
    footer = ET.fromstring(contents["word/footer1.xml"])
    assert "abc" in "".join(node.text or "" for node in footer.iter() if node.tag.endswith("}t"))
