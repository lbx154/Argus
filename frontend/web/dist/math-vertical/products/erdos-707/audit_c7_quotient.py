#!/usr/bin/env python3
"""Finite algebra audit for Section 15; not evidence for unbounded claims."""

from math import isqrt


A_STAR = (0, 1, 3, 11)


def correlations(vector: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(
        sum(vector[j] * vector[(j - shift) % 7] for j in range(7))
        for shift in range(7)
    )


def direct_triples(q: int) -> set[tuple[int, int, int]]:
    v = q * q + q + 1
    s = v // 7
    triples = set()
    for x in range(q + 2):
        for y in range((q + 1 - x) // 3 + 1):
            remainder = q + 1 - x - 3 * y
            if remainder < 0 or remainder % 3:
                continue
            z = remainder // 3
            vector = (x, y, y, z, y, z, z)
            expected = (q + s,) + (s,) * 6
            if correlations(vector) == expected:
                triples.add((x, y, z))
    return triples


def parameter_triples(q: int) -> set[tuple[int, int, int]]:
    triples = set()
    bound = 2 * isqrt(q) + 2
    for a in range(-bound, bound + 1):
        remaining = 4 * q - a * a
        if remaining < 0 or remaining % 7:
            continue
        d_abs = isqrt(remaining // 7)
        if 7 * d_abs * d_abs != remaining:
            continue
        for d in ({d_abs, -d_abs} if d_abs else {0}):
            if (a + 2 * (q + 1)) % 7:
                continue
            numerators = (
                2 * (q + 1) - 6 * a,
                2 * (q + 1) + a + 7 * d,
                2 * (q + 1) + a - 7 * d,
            )
            assert all(value % 14 == 0 for value in numerators)
            triple = tuple(value // 14 for value in numerators)
            if min(triple) >= 0:
                triples.add(triple)
    return triples


def orbit_lower_bound(q: int, c: int) -> tuple[int, int, int]:
    v = q * q + q + 1
    closure = {
        (pow(q, exponent, v) * ((label + c) % v)) % v
        for label in A_STAR
        for exponent in range(3)
    }
    fibers = tuple(sum(point % 7 == j for point in closure) for j in range(7))
    assert fibers[1] == fibers[2] == fibers[4]
    assert fibers[3] == fibers[5] == fibers[6]
    return fibers[0], fibers[1], fibers[3]


def explicit_vector(r: int) -> tuple[int, tuple[int, int, int]]:
    t = 14 * r + 9
    q = t * t
    return q, (
        (t * t - 6 * t + 1) // 7,
        (t * t + t + 1) // 7,
        (t * t + t + 1) // 7,
    )


def main() -> None:
    audited = 0
    for q in range(2, 301):
        if q % 7 not in (2, 4):
            continue
        assert direct_triples(q) == parameter_triples(q), q
        v = q * q + q + 1
        for c in range(v):
            assert max(orbit_lower_bound(q, c)) <= 3, (q, c)
        audited += 1

    q, triple = explicit_vector(0)
    vector = (triple[0], triple[1], triple[1], triple[2], triple[1], triple[2], triple[2])
    s = (q * q + q + 1) // 7
    assert correlations(vector) == (q + s,) + (s,) * 6
    assert min(triple) >= 3
    for c in range(q * q + q + 1):
        lower = orbit_lower_bound(q, c)
        assert all(value >= bound for value, bound in zip(triple, lower))

    for r in range(21):
        q, triple = explicit_vector(r)
        assert q % 7 == 4 and min(triple) >= 4

    print(f"C7 audit passed: q<=300, {audited} admissible parameters")
    print("full-lift bounds checked for q<=300; all-c family checked at r=0; formulas checked for r=0..20")


if __name__ == "__main__":
    main()
