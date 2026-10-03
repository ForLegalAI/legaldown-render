# 0004. Output formats: text and HTML, then DOCX, then PDF

- Status: Accepted (DOCX numbering and the PDF engine are still open)
- Date: 2026-09-23

## Context

§13.6 recommends PDF, DOCX, and HTML, and makes plain text optional. Rendering level requires at
least one of the three recommended formats (§17.3). The formats differ a lot in effort and in
the dependencies they bring.

## Decision

- **v0.1: `text` and `html`.** HTML has no native dependencies, supports links, and is easy to
  inspect. Plain text costs almost nothing and is the test oracle for resolution.
- **v0.3: `docx`** with python-docx, behind the `[docx]` extra.
- **v0.4: `pdf`**, behind the `[pdf]` extra.

## Open questions

**DOCX numbering.** Lawyers who receive a DOCX will edit it. Static text numbers become wrong as
soon as they insert a clause. Word-native multilevel numbering, with `REF` fields for
cross-references, keeps working after edits but is much harder to produce and test. This must be
decided before v0.3 work starts.

**PDF engine.**

| Option | For | Against |
|---|---|---|
| WeasyPrint (HTML + paged-media CSS) | Reuses the HTML writer. CSS supports running headers, page counters, and `target-counter()` for "see page N" | Needs Pango/Cairo system libraries |
| Typst | Excellent typography and a single binary | A third writer with its own layout model |
| LibreOffice (DOCX → PDF) | Matches the DOCX output exactly | Heavy, slow, and hard to install in CI and serverless environments |

The leading option is WeasyPrint. It will be decided with a spike when v0.4 starts.

## Consequences

- The first release has a small dependency footprint and fast tests.
- The HTML writer's markup should be designed with print CSS in mind from the start, so the PDF
  path can reuse it.
- 2026-10-03: v0.3 became the release built on legaldown-validator 0.4.0, so `docx` moves to
  v0.4 and `pdf` to v0.5 (see the [roadmap](../roadmap.md)).
