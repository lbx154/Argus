#!/usr/bin/env bash
set -eu
verifier=${1:?pass matching Verus executable}
probe_output=${2:?pass evidence output directory}
mkdir -p "$probe_output"
cat > "$probe_output/default-helper.rs" <<'RS'
use vstd::prelude::*;
verus! {
trait Dispatch {
    fn value(&self) -> usize;
    fn default_call(&self) -> usize { connected(self) }
}
fn connected<E: Dispatch + ?Sized>(engine: &E) -> usize { engine.value() }
}
fn main() {}
RS
"$verifier" --version > "$probe_output/verus-version.txt" 2>&1
if "$verifier" "$probe_output/default-helper.rs" > "$probe_output/default-helper.log" 2>&1; then
  echo 'This toolchain accepts the minimal default/helper shape; proceed to actual-body checking. No codec fact is established.'
else
  rg -q 'cyclic self-reference' "$probe_output/default-helper.log" || { cat "$probe_output/default-helper.log"; exit 2; }
  echo 'Reproduced default -> helper<same trait> -> trait dependency-cycle rejection. Do not hoist repeatedly or infer codec correctness. Inspect soundness rationale before a tool repair.'
fi
