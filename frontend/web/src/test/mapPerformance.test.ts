import { expect, it } from "vitest";
import { edgeLanes } from "../map/graphLayout";
import { livePollInterval } from "../map/incremental";
import type { Dataset, MapEvent } from "../map/model";
import type { Snapshot } from "../api";

// The quadratic prefix filter edgeLanes replaced; kept as the reference.
const referenceLanes = (links: { source: string; target: string }[]) =>
  links.map((e, index) =>
    links.slice(0, index).filter((l) => l.source === e.source || l.target === e.target).length,
  );

it("assigns identical lanes to the quadratic prefix filter, including shared pairs", () => {
  const cases: { source: string; target: string }[][] = [
    [],
    [{ source: "a", target: "b" }],
    [
      { source: "a", target: "b" },
      { source: "a", target: "c" },
      { source: "b", target: "c" },
      { source: "a", target: "b" },
      { source: "d", target: "b" },
      { source: "a", target: "b" },
    ],
  ];
  for (const links of cases) expect(edgeLanes(links)).toEqual(referenceLanes(links));
  // Deterministic pseudo-random fan-in/fan-out graphs.
  let seed = 42;
  const next = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
  for (let round = 0; round < 20; round++) {
    const links = Array.from({ length: 120 }, () => ({
      source: `n${Math.floor(next() * 12)}`,
      target: `n${Math.floor(next() * 12)}`,
    }));
    expect(edgeLanes(links)).toEqual(referenceLanes(links));
  }
});

const snapshot = (alive: boolean) =>
  ({ daemon: { alive }, roles: [] }) as unknown as Snapshot;
const dataset = (history_loading: boolean, events: Partial<MapEvent>[] = []) =>
  ({ history_loading, events }) as unknown as Pick<Dataset, "history_loading" | "events">;

it("polls fast only while history pages load, then falls back to a slow SSE safety net", () => {
  expect(livePollInterval(false, dataset(false), snapshot(true))).toBe(false);
  expect(livePollInterval(true, dataset(true), snapshot(true))).toBe(400);
  expect(livePollInterval(true, undefined, snapshot(true))).toBe(15_000);
  expect(livePollInterval(true, dataset(false), snapshot(true))).toBe(15_000);
  expect(livePollInterval(true, dataset(false), snapshot(false))).toBe(false);
  expect(livePollInterval(true, dataset(false, [
    { type: "team.task", status: "running" },
  ]), snapshot(false))).toBe(15_000);
  expect(livePollInterval(true, dataset(false, [
    { type: "team.task", status: "done" },
  ]), snapshot(false))).toBe(false);
});
