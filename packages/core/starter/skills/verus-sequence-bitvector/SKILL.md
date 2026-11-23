---
name: verus-sequence-bitvector
description: Diagnose bit-vector queries that unfold sequence-valued specifications and prove the word lemma before instantiating it in the sequence context.
---

Use when `assert(...packed(sequence)...) by(bit_vector)` rejects Seq::len, Seq::index or another sequence operation as `unsupported for bit-vector`. This is a query-language failure, not evidence that the bit identity is false.

Run `python3 run.py --verus /path/to/verus --output-dir /path/to/logs`. The positive control proves a u32 shift identity universally and instantiates it on a word selected from an arbitrary sequence. The negative control uses the same identity directly on the sequence specification and must fail with the unsupported bit-vector diagnostic. The script requires both outcomes.

Prove the identity in a helper whose parameters are machine integers, with a precise ensures clause. Instantiate that helper on the sequence-derived word in the ordinary SMT proof. Keep sequence facts and arithmetic-to-word conversions in the ordinary proof; keep the bit-vector query limited to words. Do not mark the helper external_body or assume its ensures.

T35 case: the actual Base64 suffix has `recovered = ((packed << (j*8)) >> 24) as u8`. The zero-position proof needed `(packed << 0)==packed`; calling `shift_zero(packed(input,start,table))` avoids expanding packed's symbol_count and Seq::len into the bit-vector query. This helper is consumed by the universally checked image_to_suffix theorem and its actual-boundary consumer. The actual suffix body remains a separate proof obligation.
