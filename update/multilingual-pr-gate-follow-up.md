# Multilingual PR Gate Follow-up

## Current policy

The dependency-free local criteria are calibrated for English pull request
descriptions. Common English word forms are matched as complete tokens so that
phrases such as `testing`, `documented`, and `configured` are recognized
without reintroducing substring false positives such as `contest`.

## Future multilingual flow

For a non-English pull request description:

1. Use an explicitly configured LLM step to translate the description into
   English.
2. Preserve both the original description and the translation in the result
   evidence.
3. Run the translated description through the same local criteria and
   thresholds used for English descriptions.
4. Mark the result as incomplete rather than silently falling back if the LLM
   translation is unavailable or fails.

Translation should remain a separate, auditable preprocessing stage. The local
criteria should not attempt to infer multilingual semantics through character
counts, unrestricted substrings, or incomplete per-language keyword lists.
