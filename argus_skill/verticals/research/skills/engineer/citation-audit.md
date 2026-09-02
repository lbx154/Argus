---
name: "Citation Verification"
description: "Verify and repair material citations directly against primary sources during final scientific review."
---

# Citation Verification

Use this in Review when a material statement depends on a citation. Work from
the current manuscript and bibliography; do not create a citation report.

For each disputed or claim-critical citation:

1. Read the surrounding sentence and the bibliography entry.
2. Resolve the paper through a primary source such as the publisher, DOI,
   official proceedings, arXiv, OpenReview, or ACL Anthology.
3. Verify title, authors, year, venue, and that the source supports the sentence.
4. Repair the bibliography or prose directly. Remove a citation that cannot be
   resolved or replace it with a real source that supports the claim.
5. Recompile the paper after the fixes.

Do not infer bibliographic facts from model memory, impose a citation-count
quota, or create per-citation JSON. Report any unresolved material citation
through the scientific review so it remains a blocking issue in
`paper/REVIEW.md`.
