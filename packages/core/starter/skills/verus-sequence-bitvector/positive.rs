use vstd::prelude::*;
verus! {
pub open spec fn word(s: Seq<u32>) -> u32 { if s.len() > 0 { s[0] } else { 0 } }
proof fn shift_zero(x:u32)
    ensures (x << 0) == x,
{ assert((x << 0) == x) by(bit_vector); }
proof fn in_sequence_context(s: Seq<u32>)
    ensures (word(s) << 0) == word(s),
{ shift_zero(word(s)); }
}
fn main() {}
