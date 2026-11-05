# Verus: pitfalls and how they showed

Seen with Verus 0.2026.09.29 (tangruize/verus d85f7578), Rust 1.98.1, Z3 4.16.0. Recheck on another release.

## Building and patching Verus

- **Build with `vargo`, not `cargo`.** `vargo build --release` also rebuilds and verifies vstd with the new
  compiler (it ran 2059 vstd items here). `cargo build -p rust_verify` rebuilds the compiler alone and leaves
  vstd as the old compiler built it. Verus's `tools/activate` replaces `cargo` with a shell function that
  refuses to run for this reason; `command cargo` gets past it, which is how the check gets skipped.
- **A patch to Verus counts only if vstd still builds with it.** A 28-line patch that dropped a synthesized
  `MetaSized` bound from external trait specifications let a base64 proof through (179/0), but with it applied
  vstd itself failed with 52 `external_trait_specification trait bound mismatch` errors. The proof had used a
  vstd built by the unpatched compiler. Check: apply the patch to a clean checkout and run `vargo build --release`.
- Prefer a behavior-preserving rewrite of the code being verified (in the verification copy, argued equivalent)
  to a verifier patch: the rewrite costs a reviewer one diff; the patch puts the verifier itself in question.

## Resource limits

- Default `--rlimit 10` gives each SMT query a limit of 30,000,000 resource units.
- **A proof that needs a raised rlimit is fragile**, not finished: it slows larger projects and breaks again on
  the next code change. A base64 decoder's two loops exceeded the default (177 verified, 2 errors) after a run
  had fixed one verifier error at a time; pulling the facts each loop step needs into three small lemmas, and
  making one definition opaque with an explicit `reveal` where used, brought the loops to 11.0M and 6.4M units
  and 182/0 at the default limit, with the theorem unchanged.
- Per-query cost: run with `--log smt --log-dir DIR` and replay the module's `.smt2` (the `verus-query-timing`
  skill does this). Verus's own `--time` reports only per-function totals.

## Rust that Verus rejects (seen)

- A `for` loop containing `continue` (see the `verus-for-continue` skill); a `while` loop is accepted.
- `u64::to_be_bytes` / `u32::to_be_bytes`: unsupported, and an `assume_specification` for them does not attach,
  because the return array's length is an anonymous constant that Verus will not match against `8` or
  `size_of::<u64>()` (see `verus-endian-interface`). Writing the byte packing out explicitly works.
- A trait default method calling a generic helper can be rejected as a dependency cycle (see
  `verus-default-dispatch-probe`). Expanding the helper call inline in the verification copy got past it.

## Caller-side conditions

- A library call taking `impl AsRef<[u8]>` cannot promise success unconditionally: the caller's `as_ref` or
  `Drop` may panic after the library has done its work correctly. State it as a premise ("the caller's
  conversion and drop return normally"); a test with a panicking `Drop` shows the counterexample.
