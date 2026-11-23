---
name: verus-result-map
description: Diagnose a missing metadata-to-public-result projection through Result::map without assuming the consumer result.
---
Run `python3 probe.py /absolute/path/to/verus /path/to/evidence`. The three small actual-body probes require that the unannotated closure fails the public postcondition, the explicitly annotated projection verifies, and a wrong projection fails its closure postcondition. The script retains source, exact commands, outputs and exits.

When a callee returns `Ok(metadata)` with a count/frame contract, `Result::map` exports the closure's declared ensures, not arbitrary field semantics inferred from its body. Keep the executable projection and add its checked contract, e.g. `|m: Metadata| -> (n: usize) ensures n == m.count, { m.count }`. Do not assume the public count or trust the wrapper. Check the actual map closure and wrapper, and independently inspect erasure/correspondence if the production closure had inferred types or expression syntax.

Case: t31's actual Engine::decode_slice calls internal_decode and maps decoded metadata to decoded_len. Before the closure contract, both the executed wrapper and retained inner failed their result postconditions; afterwards both checked against explicit lower contracts. The probe catches replacing the field with zero. This supplies no lower codec proof, global effect frame, generic AsRef totality or user Drop guarantee. t31's reported-prefix caller and full evidence remain under the project's .system-proof/t31/.
