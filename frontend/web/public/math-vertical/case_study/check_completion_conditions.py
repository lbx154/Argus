#!/usr/bin/env python3
"""Finite audit of residual compatibility for two four-point candidates."""

from __future__ import annotations

from itertools import combinations


CANDIDATES = ((0, 1, 3, 11), (0, 1, 4, 11))
Q_VALUES = tuple(range(2, 12))


def ordered_differences(points: tuple[int, ...], modulus: int) -> tuple[int, ...]:
    return tuple(
        (x - y) % modulus
        for x in points
        for y in points
        if x != y
    )


def modular_sidon(points: tuple[int, ...], modulus: int) -> bool:
    residues = tuple(sorted({x % modulus for x in points}))
    if len(residues) != len(points):
        return False
    differences = ordered_differences(residues, modulus)
    return 0 not in differences and len(set(differences)) == len(differences)


def compatibility_graph(
    base: tuple[int, ...], modulus: int
) -> tuple[tuple[int, ...], dict[int, set[int]]]:
    residues = tuple(sorted(x % modulus for x in base))
    vertices = tuple(
        x
        for x in range(modulus)
        if x not in residues and modular_sidon(residues + (x,), modulus)
    )
    adjacency = {x: set() for x in vertices}
    for x, y in combinations(vertices, 2):
        if modular_sidon(residues + (x, y), modulus):
            adjacency[x].add(y)
            adjacency[y].add(x)
    return vertices, adjacency


def find_target_clique(
    vertices: tuple[int, ...],
    adjacency: dict[int, set[int]],
    target_size: int,
) -> tuple[int, ...] | None:
    def search(
        chosen: tuple[int, ...], candidates: tuple[int, ...]
    ) -> tuple[int, ...] | None:
        if len(chosen) == target_size:
            return chosen
        if len(chosen) + len(candidates) < target_size:
            return None
        for index, x in enumerate(candidates):
            compatible_tail = tuple(
                y for y in candidates[index + 1 :] if y in adjacency[x]
            )
            result = search(chosen + (x,), compatible_tail)
            if result is not None:
                return result
        return None

    if target_size == 0:
        return ()
    return search((), vertices)


def first_collision(
    points: tuple[int, ...], modulus: int
) -> tuple[int, tuple[int, int], tuple[int, int]] | None:
    seen: dict[int, tuple[int, int]] = {}
    for x in sorted(points):
        for y in sorted(points):
            if x == y:
                continue
            difference = (x - y) % modulus
            if difference in seen:
                return difference, seen[difference], (x, y)
            seen[difference] = (x, y)
    return None


def find_completion(
    base: tuple[int, ...], modulus: int, target_size: int
) -> tuple[int, ...] | None:
    residues = tuple(sorted(x % modulus for x in base))
    if not modular_sidon(residues, modulus):
        return None
    used = set(ordered_differences(residues, modulus))
    candidates = [
        x
        for x in range(modulus)
        if x not in residues and modular_sidon(residues + (x,), modulus)
    ]

    def search(
        chosen: tuple[int, ...],
        remaining: list[int],
        occupied_differences: set[int],
    ) -> tuple[int, ...] | None:
        if len(chosen) == target_size:
            return chosen
        needed = target_size - len(chosen)
        if len(remaining) < needed:
            return None
        current = residues + chosen
        for index, x in enumerate(remaining):
            new_differences = {
                (x - y) % modulus for y in current
            } | {
                (y - x) % modulus for y in current
            }
            if len(new_differences) != 2 * len(current):
                continue
            if new_differences & occupied_differences:
                continue
            result = search(
                chosen + (x,),
                remaining[index + 1 :],
                occupied_differences | new_differences,
            )
            if result is not None:
                return result
        return None

    return search((), candidates, used)


def main() -> None:
    print("finite residual-completion audit")
    print(f"q_range={Q_VALUES[0]}..{Q_VALUES[-1]} (inclusive)")
    print("classification=finite verification only")
    for base in CANDIDATES:
        print(f"A={base}")
        for q in Q_VALUES:
            modulus = q * q + q + 1
            base_ok = modular_sidon(base, modulus)
            if not base_ok:
                print(f"q={q:2d} v={modulus:3d} base_sidon=no")
                continue
            vertices, adjacency = compatibility_graph(base, modulus)
            edge_count = sum(map(len, adjacency.values())) // 2
            target = q - 3
            target_clique = find_target_clique(vertices, adjacency, target)
            completion = find_completion(base, modulus, target)
            completion_text = "none" if completion is None else repr(completion)
            clique_text = "none" if target_clique is None else repr(target_clique)
            collision = (
                None
                if target_clique is None
                else first_collision(base + target_clique, modulus)
            )
            print(
                f"q={q:2d} v={modulus:3d} base_sidon=yes "
                f"|X_A|={len(vertices):3d} edges={edge_count:5d} "
                f"residual_target={target:2d} completion={completion_text}"
            )
            print(f"  target_clique={clique_text} first_collision={collision}")


if __name__ == "__main__":
    main()
