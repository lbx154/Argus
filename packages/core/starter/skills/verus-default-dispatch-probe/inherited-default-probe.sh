#!/usr/bin/env bash
set -eu
verifier=${1:?pass matching Verus executable}
out=${2:?pass evidence directory}
mkdir -p "$out"
"$verifier" --version > "$out/verus-version.txt" 2>&1
cat > "$out/guarded.rs" <<'RS'
use vstd::prelude::*;
verus! {
trait T { #[verifier::impls_cannot_extend_spec] fn f(&self) {} }
impl T for bool {}
}
fn main() {}
RS
sed 's/#\[verifier::impls_cannot_extend_spec\] //' "$out/guarded.rs" > "$out/control.rs"
"$verifier" "$out/control.rs" > "$out/control.log" 2>&1
set +e
"$verifier" "$out/guarded.rs" > "$out/guarded.log" 2>&1
status=$?
set -e
if [ "$status" -eq 0 ]; then
    echo 'Guarded inherited-default shape checks; actual application replay still required.'
elif rg -q 'sst_to_air_func.rs:618:47|no entry found for key' "$out/guarded.log"; then
    echo 'Guarded inherited default reaches AIR missing-key crash; actual dispatch remains blocked.'
else
    echo 'Different rejection: inspect retained logs.'
    exit "$status"
fi
