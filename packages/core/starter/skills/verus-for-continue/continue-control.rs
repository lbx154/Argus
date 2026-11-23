use vstd::prelude::*;
verus! {
fn original_continue() -> (n: usize)
    ensures n == 3,
{
    let mut n = 0;
    for i in 0..4usize
        invariant n == if i <= 2 { i as int } else { i - 1 },
    {
        if i == 2 { continue; }
        n += 1;
    }
    n
}
}
fn main() {}
