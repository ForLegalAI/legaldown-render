# Publishing (maintainers)

Release mechanics for this repository. Nothing here is needed to *use* the
renderer — see the [README](../README.md) for that.

`legaldown-render` is published to PyPI by the [`publish`](workflows/publish.yml)
workflow using **Trusted Publishing** (OIDC). No API token is stored in this repository — PyPI
verifies the identity of the workflow run itself.

## One-time PyPI setup

The trusted publisher is already set up for `legaldown-render`, which is on PyPI. The steps below
are for a fork or a rename: the publisher must exist before the first publish, and be registered by
a PyPI account that will own the project. Until the project exists on PyPI, register it as a
**pending** publisher.

1. Sign in to <https://pypi.org> → **Your account** → **Publishing** →
   *Add a new pending publisher*.
2. Fill in exactly:

   | Field | Value |
   |---|---|
   | PyPI project name | `legaldown-render` |
   | Owner | `ForLegalAI` |
   | Repository name | `legaldown-render` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

3. Repeat on <https://test.pypi.org> with environment name `testpypi` if you want the dry-run path.

The environment names must match, or PyPI rejects the upload.

## One-time GitHub setup

Create the two environments under **Settings → Environments**:

| Environment | Purpose | Suggested protection |
|---|---|---|
| `pypi` | Real releases | Required reviewers, and restrict to the `main` branch / tags |
| `testpypi` | Dry runs | None needed |

Adding a required reviewer to `pypi` means a human approves each upload — worth it, since a PyPI
version number can never be reused once published.

## Cutting a release

1. Bump `__version__` in [`src/legaldown_render/__init__.py`](../src/legaldown_render/__init__.py) — it is the single
   source of truth; `pyproject.toml` reads it via `[tool.hatch.version]`.
2. Update everything else that names the version. The two HTML goldens embed it
   (`tests/golden/*.default.html`, in `<meta name="generator">`): regenerate them with
   `pytest --update-golden tests/test_golden.py`, and check that `git diff tests/golden` shows
   only that line. Also update the version named in [`CONFORMANCE.md`](../CONFORMANCE.md) and the
   milestone in [`docs/roadmap.md`](../docs/roadmap.md).
3. Commit, then publish a GitHub Release with the tag `vX.Y.Z`. Its notes are where this
   project records what changed in a release.

The workflow first runs the CI workflow on the tagged commit. Only if that passes does it build the
sdist and wheel, run `twine check --strict`, **verify the tag matches `__version__`** (a mismatch
fails the run rather than publishing the wrong version), smoke-test the built wheel by installing it
and invoking the CLI, and upload to PyPI.

## Dry run

To rehearse a release, exercise the whole path against TestPyPI:

```
Actions → Publish → Run workflow → target: testpypi
```

Then verify the result installs cleanly:

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ legaldown-render
legaldown-render --version
```

The extra index is needed because the dependencies (`legaldown-validator`, Babel, PyYAML,
markdown-it-py) come from PyPI.

## Checking the DOCX in Word (before a release)

CI proves the DOCX against the ISO/IEC 29500 schemas and in LibreOffice; it cannot run Word. Before a release, a maintainer
opens the golden documents in Word (the desktop application, a current version) and checks what only Word can show.
Make them with the Python API, which is the same on Linux, macOS and Windows (run it from the repository root, in an
environment with the package installed):

```python
from pathlib import Path
from legaldown_render import render

out = Path("word-check")          # any folder; created here
out.mkdir(exist_ok=True)
contents = {"contents.enabled": True, "contents.depth": 3}
for doc in ("features", "template"):
    source = Path(f"tests/documents/{doc}.lgd").read_text(encoding="utf-8")
    for style in ("default", "continental", "outline"):
        (out / f"{doc}.{style}.docx").write_bytes(render(source, format="docx", style=style, overrides=contents).binary)
    # the settings whose effect only Word shows (see item 7)
    for name, extra in {
        "attachments-not-listed": {"contents.attachments": False},
        "depth-1": {"contents.depth": 1},
        "no-attachments-label": {"labels.attachments": ""},
        "separator-none": {"attachments.separator": "none"},
        "separator-rule": {"attachments.separator": "rule"},
        "scheme-none": {"numbering.scheme": "none"},
        "paragraphs-numbered": {"paragraphs.numbered": True},      # `1.1` paragraphs on the heading list (item 9)
    }.items():
        data = render(source, format="docx", overrides={**contents, **extra}).binary
        (out / f"{doc}.{name}.docx").write_bytes(data)
```

(On the command line, `legaldown-render tests/documents/features.lgd -s continental --set contents.enabled=true
--set contents.depth=3 -o word-check/features.continental.docx` makes one of them; create the folder first with
`mkdir -p word-check` on Linux and macOS or `mkdir word-check` on Windows.)

For each file:

1. **Opens clean.** No "unreadable content" or repair prompt, and Word does not open it in Compatibility Mode.
   Answer *Yes* to the prompt to update the fields (`docx.update_fields_on_open`), or press Ctrl+A, F9.
2. **Table of contents.** It lists exactly the entries of the HTML contents (numbers and titles; the condition
   labels of a template after the title), indented by level, with a dotted leader and page numbers that are right.
   The attachments heading and each attachment's title are listed (at the levels the HTML shows) and nothing else is:
   not the "Contents" label, no quote or note. A click on an entry (Ctrl+click) goes to the heading. Right-click the
   TOC, *Update field*, *Update entire table*: the same entries come back.
3. **References.** Every `{{ref:}}` reads as in the HTML before and after the update (Ctrl+A, F9). Insert a clause
   (a heading, a list item, a numbered paragraph) above a referenced one, update the fields: the numbers after it move
   and every reference and the contents follow. A reference to an attachment (`{{attach:}}`) shows its title and links
   to it.
4. **Numbering after inserting a clause.** Press Enter at the end of a numbered item and at the start of one: the new
   paragraph is numbered, the rest renumbers, nested lists restart under it. In the continental style a new
   sub-item continues `a)`, `b)`.
5. **Zero-length bookmarks.** A `REF \n` to the zero-length (point) bookmark at the start of a numbered paragraph
   returns the number. Press Enter at the start of that paragraph and update the fields: the references still
   point at the paragraph they pointed at and read right.
6. **`none` scheme** (the `scheme-none` files): a heading referenced with `{{ref:}}` shows its title text
   (`REF \h`), and the references inside a heading that refers to itself are plain text: no "Error! Reference source
   not found" and no self-reference.
7. **Front and back matter.** The title block (header rows, parties) is two aligned columns without borders, the
   labels in italics; each attachment starts on a new page with `attachments.separator: page-break` and has a rule above
   it with `rule`; with `none` (`separator-none`) there is no break and no rule; the signature blocks are borderless,
   with a signing line above each name, and stay on one page when one page can hold them (try a longer document). The
   settings variants:
   - `attachments-not-listed`: the attachments heading and titles are **absent** from the table of contents and from
     the navigation pane (outline level 9, body text).
   - `depth-1`: the attachments are still in the contents, as first-level entries (the HTML indents the titles one
     level more, the documented difference), and no second-level heading is.
   - `no-attachments-label`: there is no attachments heading, and each title is a first-level entry.
   - `\u` honours the outline levels of the attachment styles and paragraphs: the attachment titles are entries, at
     the level the table of contents gives them, in every variant that lists them. If Word does not list them, the
     fix is a `\t "Attachment Title,2"` list in the field (locale list separator permitting); report it.
8. **The numbering corner cases.** Make the check document with `python tests/word_check.py
   word-check/numbering-assumptions.docx` (from the repository root, in the project's environment): one row for each label
   form the planner relies on, each with the planner's assumption beside it, taken from the planner's own code, so that
   a change to the model shows in the file. Ctrl+A, F9, then compare each row's `REF \n` and `REF \n \t` with the last
   column:
   - `\t` with words before the number (rows 1 to 4: `Article %1`, `§ %1`, Microsoft's own `Section %1.%2` with a
     decimal and with a roman `%1`): Word shows the bare number (`I`, `1`, `1.1`, `I.1`) where LibreOffice ignores `\t`
     and shows the words. This is what the default `docx.reference_text_switch: documented` relies on.
   - a period after letters before the number (rows 5 and 6: `Čl. %1`, `No. %1`): the planner does not use `\t` for
     them under `documented`, and takes it to give the bare number under `guess`. If Word gives `1`, a style with such a
     label may set `docx.reference_text_switch: guess`; if it gives anything else, report it.
   - delimiters around the counter (rows 7 to 10: `(%1)`, `%1)`, `%1:`, `%1.%2(%3)`): the planner never uses `\t` for
     them (what Word does is not documented). Note what Word returns: if it keeps the delimiter, `\t` gives the plain
     number and the planner's refusal is only cautious.
   - the reference to such an item reads as the HTML reads it, or the render reported `render-docx-reference` and the
     reference is fixed text (with the class it falls in and the setting that could change it).
   - skipped heading levels (a `###` straight after a `#`, an Error for the validator but rendered; part C of the check
     document): the numbers after it, and the references to them, read as the plain-text output reads them, but the
     planner caches the skipped level as 1, as LibreOffice and the renderer count it, and writes that level's part of a
     reference as text (`render-docx-reference` says so). Word is expected to draw 0 for the level a heading skips: record
     what it draws. The rest of such a reference is fields and follows edits.
   - counters shared across nums: a nested list of the same kind restarts under each new parent; a list of another
     kind restarts by a fixed setting (the render reported `render-docx-static-restart`).
   - Roman numerals of 4000 and over, and letters past 26: Word's text (`render-docx-roman-overflow`,
     `render-docx-letter-overflow`) is the one the render reported, not a different one.
9. **Numbering that shares a list or a counter** (`template.continental.docx`, the continental `features` file, and
   the `paragraphs-numbered` files; the planner models these from Word's documented behaviour and LibreOffice, and they are
   what only Word can confirm). After Ctrl+A, F9:
   - Two `w:num` of one abstract list share the counter, and a `startOverride` restarts only its own level: where a section with clauses `1.1`, `1.2` is followed by a sub-heading
     (add one to the file if the golden documents have none), the sub-heading reads `1.1` for the
     sub-heading (restarted at a fixed value; the render reported `render-docx-shared-counter`), and the next heading
     after it reads `1.2`, not `1.3`. Deleting that sub-heading makes the headings after it read higher (the documented
     limit).
   - Clauses in the continental style (`5.1`, `5.2`) are not in the table of contents and not in the navigation pane
     (they have no heading style). Under each clause the nested list reads `a)`, `b)`, and a new clause (Enter at the end
     of a clause, typing) restarts it at `a)` under that clause; so does a sub-sub-list (`i)`). A list nested three
     levels under a clause (`1.1.1`) is a separate list that restarts by a fixed setting (reported `render-docx-static-restart`).
   - A level of the heading list whose text names levels around ones nothing numbers (part B of the check document: levels
     3 to 5 unused, the nested level `%1.%2(%6)`): the nested items read `1.1(a)`, `1.1(b)`, and `1.2(a)` under the next
     item, never `1.1.0.0.0(a)` and never continuing after a new item. This is the layout of a `{path}` label under a
     clause.
   - A list in an ordered item that shows the path of its item (the style `enumeration.levels` with `{path}` labels) is on
     detached levels: Word draws the item's own counter alone (`(A)`, not `1(a)(1)(i)(A)`; the render reported
     `render-docx-section-label` with what Word draws), and the references read as the HTML reads them. A second
     such list under a new parent restarts at `(A)` by a fixed setting; a list made new in Word by copying an item
     of another goes on counting (the documented limit).
   - Items nested past nine levels continue in a further list: the tenth level restarts at `(a)` by a fixed setting and its
     own items restart by structure; the references to them read as the HTML reads them.
   - In `template.continental.docx` a later alternative (a paragraph, clause or heading that shares a number: the
     second "Disputes", say) shows the first one's number: a `numId 0` paragraph that starts with a `REF \n \h` field,
     with the same hanging indent as a numbered one and its text after a tab. Update the field: it still reads the
     holder's number. Its entry in the table of contents reads the shared number, a tab and the title (`2.<tab>Title`).
     The numbers after the alternatives continue as the renderer's. Press Enter at the start and at the end of a
     later alternative: the new paragraph has no number (it copies `numId 0`; expected, apply a list style by hand). **Delete
     the first alternative:** every later alternative, their children, and every reference to the shared identifier read
     "Error! Reference source not found" (expected: the render reported `render-docx-alternative`, and the final document
     is made by rendering with the condition values).
   - In the `paragraphs-numbered` files the paragraphs read `1.1`, `1.2` on the heading list; a sub-heading after a
     list of clauses in the same section is restarted (as above).
10. **Page layout.** The running footer shows `n / N`; a header template with `{title}`, `{version}` and `{date}` shows
   them; a landscape page is landscape when printed (print preview).

Record what differs in the release notes. A difference from LibreOffice is expected where the documentation says Word
and LibreOffice differ (`\t`, `lvlRestart`, `SEQ \s`, `w:updateFields`).

## Notes

- **Version numbers are permanent.** PyPI does not allow re-uploading a version, even after
  deletion. Use TestPyPI, or a `.devN`/`.rcN` suffix, to rehearse.
- The distribution is named `legaldown-render`; the import name is `legaldown_render`
  (`pip install legaldown-render` → `import legaldown_render`).
- A release of `legaldown-validator` with a new minor version needs a renderer release too: the
  dependency is pinned to one minor version (`pyproject.toml`). Publish it once that validator
  release is on PyPI, since until then nothing can satisfy the range (the `validator-main` CI job
  overrides the installed validator with `main`, so it tests main regardless of the range). The
  tests fail on any `legaldown.LegaldownDeprecationWarning`, so a release never calls what the
  validator's next minor version removes.
- **The upload is gated on the tests.** `publish.yml` first calls [`ci.yml`](workflows/ci.yml)
  (`on: workflow_call`) as the job `ci`, and `build` needs it, so the lint, test
  (Linux and Windows on Python 3.13) and conformance jobs must pass on the commit being published
  before anything is built, and `testpypi` and `pypi` need `build`. The `validator-main` job is
  informational (`continue-on-error`) and never blocks a release. The caller grants the called
  workflow `contents: read`; a called workflow cannot hold more permissions than its caller gives.
  In the called workflow `github.workflow` is the caller's name, so its concurrency group
  (`ci-${{ github.workflow }}-${{ github.ref }}`) is distinct from the push and pull-request runs
  and from `publish.yml`'s own group.
- The conformance job checks out the specification release named once, in `LEGALDOWN_SPEC_REF` at the
  top of `ci.yml`. Change it there when the renderer moves to a later specification.
- The license is declared once, as the SPDX expression `license = "MIT"` in `pyproject.toml`
  (PEP 639), so the deprecated `License :: OSI Approved :: MIT License` classifier is not listed.
- The package ships a `py.typed` marker, so type checkers use its annotations directly.
