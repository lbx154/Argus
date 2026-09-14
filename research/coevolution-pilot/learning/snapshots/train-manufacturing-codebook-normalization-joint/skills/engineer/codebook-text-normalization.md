---
name: Product-codebook text normalization
description: Normalize noisy multilingual reason text conservatively against product-specific codebooks while preserving source spans and calibrated ambiguity.
---

## Procedure

1. Load each product's codebook and constrain every candidate by the record's product before comparing text.
2. Detect complete reason phrases rather than splitting blindly on commas or slashes. Treat translated repetitions with the same category and subject as one reason, but retain genuinely different categories as separate segments in source order.
3. Copy each reported span exactly from the source text.
4. Resolve component- or signal-specific classes only when filtering leaves one distinct standard label. A unique codebook association from a named signal to a component may support a lower-confidence inference; multiple distinct labels require `UNKNOWN`.
5. When several codes have the same label and no distinguishing metadata, select one deterministic code and reduce confidence rather than inventing a distinction.
6. Keep all unknown confidences below all accepted-match confidences. Lower confidence for spelling repair, inferred entities, station-scope mismatch, and indistinguishable code aliases.
7. Validate record coverage, sequential segment identifiers, literal span inclusion, product/code/label integrity, confidence bounds, and the separation of known from unknown confidence distributions.
