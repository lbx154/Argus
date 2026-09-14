---
name: "Progressive PDF Reading"
description: "按页或章节读取 PDF，核对页码和版本。 Read document text progressively with the Argus PDF CLI; use rendered-page inspection for images/layout, and answer the user with only the relevant supported content."
---

# Progressive PDF Reading

Use for a question about a local PDF or versioned arXiv paper. For source editing,
read the source too; the rendered PDF remains evidence of what readers see.
Text extraction cannot verify figures, layout or clipped content: inspect actual
rendered pages for those questions.

Use the supplied Argus interpreter to run
`python -m argus.tools.pdf_chat <subcommand> <source> [options]`.
`<source>` is a local file or arXiv identifier. Prefer an explicit arXiv version
when claims depend on that version; a cached unversioned identifier may be stale.

| Need | Command |
| --- | --- |
| Locate pages/sections | `head <source>` |
| Decide paper relevance | `brief <source>` |
| Read one section | `section <source> "<name>"` |
| Read a specific page/range | `page <source> --start N --end M` |
| A task truly needs the full extracted text | `full <source>` |

Start with the smallest command that answers the question. Section detection is
heuristic; a missing section name should lead to the relevant page range, not an
absence claim. Long `full` output may be truncated and is not proof of complete
coverage. Compare the PDF/source version before attributing a quote or number.

Extraction uses `pdftotext` or a `pypdf` fallback. Surface unavailable files,
parsing errors or access restrictions honestly. Cached PDFs are under
`ARGUS_SKILL_PDF_CACHE` (default `~/.argus-skill/pdf_cache`); refresh an obsolete cache
entry only when necessary and within the configured cache, preserving user files.

The CLI returns JSON. Use it as evidence and answer the actual user question with
page/section references and concise supported content; return raw JSON only when
requested. Stop once the question is answered, rather than dumping the full PDF
or producing an unsolicited question list or summary file.
