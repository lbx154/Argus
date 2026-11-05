#!/usr/bin/env bash
set -eu
verifier=${1:?pass matching Verus executable}
out=${2:?pass evidence directory}
mkdir -p "$out"
"$verifier" --version > "$out/verus-version.txt" 2>&1
cat > "$out/guarded.rs" <<'RS'
use vstd::prelude::*;
verus! {
trait Dispatch {
    #[verifier::impls_cannot_extend_spec]
    fn value(&self) -> usize;
    #[verifier::impls_cannot_extend_spec]
    fn default_call(&self) -> usize { connected(self) }
}
fn connected<E: Dispatch + ?Sized>(engine: &E) -> usize { engine.value() }
}
fn main() {}
RS
"$verifier" "$out/guarded.rs" > "$out/guarded.log" 2>&1
python3 - "$out" <<'PY'
import sys
from pathlib import Path
p=Path(sys.argv[1]);s=(p/'guarded.rs').read_text();s=s.replace('\n}\nfn main()', '\nimpl Dispatch for bool { fn value(&self) -> (r: usize) ensures r == 0 { 0 } }\n}\nfn main()');(p/'forbidden-ensures.rs').write_text(s)
PY
if "$verifier" "$out/forbidden-ensures.rs" > "$out/forbidden-ensures.log" 2>&1; then
 echo 'ERROR: guarded implementation strengthening unexpectedly accepted'; exit 1
fi
rg -q 'trait method implementation cannot declare ensures clauses' "$out/forbidden-ensures.log"
echo 'Guarded executable shape checks and implementation-ensures guard rejects. No application bridge established.'
