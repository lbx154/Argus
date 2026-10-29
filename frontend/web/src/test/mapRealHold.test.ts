import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { latestCertifiedTask, statusKey, type MapEvent, type MapTask } from "../map/model";
import { attentionSummary } from "../map/presentation";

// A real project map: the operator invalidated earlier simulated results and
// asked for a redo; the redo was reviewed, then the Manager held the stage.
const here = dirname(fileURLToPath(import.meta.url));
const payload = JSON.parse(readFileSync(join(here, "fixtures", "s-9b7525df.map.json"), "utf8")) as {
  tasks: MapTask[]; events: MapEvent[];
};
const held = payload.tasks.find((task) => task.id === "63bc3ac26fd5")!;

describe("real stage-hold project", () => {
  it("reads the held stage as held although no terminal event carries its outcome", () => {
    expect(held.outcome).toEqual({});
    expect(statusKey(held)).toBe("held");
    // A plain failure without the Manager's hold record stays a failure.
    expect(statusKey({ ...held, last_error: "runner crashed" })).toBe("failed");
  });

  it("does not headline an earlier acceptance after the goal was redirected", () => {
    expect(latestCertifiedTask(payload.tasks, payload.events)).toBeUndefined();
  });

  it("says the hold reason in the reader's language and keeps the Manager's words as detail", () => {
    const zh = attentionSummary(held, payload.events, true);
    expect(zh.reason).toMatch(/工作已暂停/);
    expect(zh.reason).not.toMatch(/Previous simulated|invalidated/);
    expect(zh.detail).toMatch(/^Previous simulated data was invalidated/);
    const en = attentionSummary(held, payload.events, false);
    expect(en.reason).toMatch(/work paused/i);
  });

  it("says work stopped at the hold instead of promising follow-up work", () => {
    // The bounded run ended at this hold and nothing was scheduled after it.
    for (const zh of [true, false]) {
      const next = attentionSummary(held, payload.events, zh).next;
      expect(next).toMatch(zh ? /停下|不会自动继续/ : /stopped|will not resume/);
      expect(next).not.toMatch(zh ? /等待团队/ : /team plans/);
    }
  });

});
