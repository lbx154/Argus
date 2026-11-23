use vstd::prelude::*;
verus! {
pub open spec fn word(s: Seq<u32>) -> u32 { if s.len() > 0 { s[0] } else { 0 } }
proof fn in_sequence_context(s: Seq<u32>) {
    assert((word(s) << 0) == word(s)) by(bit_vector);
}
}
fn main() {}
