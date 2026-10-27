"""The HTML writer's stylesheet for print (§13.8): a page break goes before the
attachments container, not inside it, and only where the separator asks for
one."""
from __future__ import annotations

from conftest import run_cli_text

ATTACHED = (
    "---\ntitle: T\nattachments:\n"
    "  - id: one\n    title: First attachment\n    file: one.pdf\n"
    "  - id: two\n    title: Second attachment\n    file: two.pdf\n"
    "---\n\n# A {#a}\n\nBody.\n"
)


def test_a_standalone_page_break_render_has_the_attachment_break_rules() -> None:
    done = run_cli_text("-", "--set", "attachments.separator=page-break", stdin=ATTACHED)
    assert done.returncode == 0, done.stderr
    assert "<!DOCTYPE html>" in done.stdout
    assert 'class="ld-attachments ld-separator-page-break"' in done.stdout
    # The break goes before the container (heading included), then before each later attachment.
    assert ".ld-attachments.ld-separator-page-break { break-before: page; }" in done.stdout
    assert ".ld-separator-page-break .ld-attachment + .ld-attachment { break-before: page; }" in done.stdout
    assert ".ld-attachments-heading { break-after: avoid; }" in done.stdout
    # The old rule broke before every attachment, the first one included, inside the container.
    assert ".ld-separator-page-break .ld-attachment { break-before: page; }" not in done.stdout


def test_without_a_page_break_separator_the_container_is_not_broken() -> None:
    done = run_cli_text("-", "--set", "attachments.separator=none", stdin=ATTACHED)
    assert done.returncode == 0, done.stderr
    # The rules are scoped by the separator class, which no element carries here.
    assert 'class="ld-attachments ld-separator-none"' in done.stdout
    assert 'ld-separator-page-break"' not in done.stdout
