import { expect, it } from "vitest";
import { formationWidths, type MapEvent } from "../map/model";

const formed = (overrides: Partial<MapEvent>): MapEvent => ({
  id: "e",
  item_id: "t1",
  type: "idea.portfolio.formed",
  ts: 1,
  text: "",
  ...overrides,
});

it("keeps the latest declared width per task and ignores junk", () => {
  const widths = formationWidths([
    formed({ id: "a", width: 4, ts: 1 }),
    formed({ id: "b", width: 8, ts: 5 }),
    formed({ id: "c", item_id: "t2", width: 3 }),
    formed({ id: "d", item_id: "t3" }),
    formed({ id: "e", item_id: "t4", width: 0 }),
    formed({ id: "f", item_id: "t5", type: "team.task", width: 9 }),
  ]);
  expect(widths.get("t1")).toBe(8);
  expect(widths.get("t2")).toBe(3);
  expect(widths.has("t3")).toBe(false);
  expect(widths.has("t4")).toBe(false);
  expect(widths.has("t5")).toBe(false);
});

it("prefers the newest event even when it arrives out of order", () => {
  const widths = formationWidths([
    formed({ id: "late", width: 6, ts: 9 }),
    formed({ id: "early", width: 2, ts: 3 }),
  ]);
  expect(widths.get("t1")).toBe(6);
});
