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
  (`on: workflow_call`) as the job `ci`, and `build` needs it, so the lint, test (Linux on Python
  3.11-3.13 and Windows on 3.12) and conformance jobs must pass on the commit being published
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
