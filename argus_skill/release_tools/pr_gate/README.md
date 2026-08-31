# PR Gate

The PR gate is a lightweight lexical contributor prompt. It checks whether the
combined pull request title and body contain enough detail for the patch size
and mention any changed test, documentation, or configuration categories.

These local checks do not establish semantic consistency. A sufficiently long
but irrelevant message can satisfy lexical scope scoring, and category checks
only establish that recognized category words are present. Stronger semantic
and multilingual evaluation is reserved for future LLM-based criteria.

Scope scoring uses a continuous description-length ratio relative to text
churn. File-category scoring includes only recognized changed categories;
unknown files do not affect its score.

Binary files currently contribute `0.0` lines of text churn because Git does
not provide meaningful added or deleted line counts for them. A binary-only
patch therefore makes lexical scope scoring not applicable and does not fail
the gate. This is an intentional lightweight policy rather than an estimate of
binary change size.

Malformed event payloads fail closed with a structured `pr-gate/1.0` result,
a GitHub error annotation, and a non-zero exit status.
