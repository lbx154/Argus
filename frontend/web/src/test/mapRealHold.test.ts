import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { latestCertifiedTask, statusKey, type MapEvent, type MapTask } from "../map/model";
import { attentionSummary, projectBrief } from "../map/presentation";
import { mapStatusSentence } from "../map/status";

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
    expect(zh.reason).toMatch(/阶段暂停/);
    expect(zh.reason).not.toMatch(/Previous simulated|invalidated/);
    expect(zh.detail).toMatch(/^Previous simulated data was invalidated/);
    // Without a reader-language account the Manager's words are the only
    // reason there is, so they are shown rather than folded away.
    expect(zh.showDetail).toBe(true);
    const en = attentionSummary(held, payload.events, false);
    expect(en.reason).toMatch(/on hold/i);
  });

  it("says work stopped at the hold instead of promising follow-up work", () => {
    // The bounded run ended at this hold and nothing was scheduled after it.
    for (const zh of [true, false]) {
      const next = attentionSummary(held, payload.events, zh).next;
      expect(next).toMatch(zh ? /停下|不会自动继续/ : /stopped|will not resume/);
      expect(next).not.toMatch(zh ? /等待团队/ : /team plans/);
    }
  });

  it("uses the written reader-language account of the hold when one exists", () => {
    const written = "早先的模拟数据已作废，论文还没写，方法还要在真实轨迹上和真实基线比。";
    const zh = attentionSummary(held, payload.events, true, written);
    expect(zh.reason).toBe(`阶段暂停：${written}`);
    expect(zh.showDetail).toBe(false);
    expect(zh.detail).toMatch(/^Previous simulated/);
  });

  it("gives an outsider the goal, the conclusion so far, and the next step", () => {
    const brief = projectBrief(payload.tasks, payload.events, true);
    expect(brief.goal).toMatch(/^将既往所有模拟及虚构的实验数据/);
    expect(brief.conclusion).toMatch(/尚无通过验收的最终结论/);
    expect(brief.conclusion).toMatch(/阶段暂停/);
    expect(brief.next).toMatch(/不会自动继续/);
    // The goal already names the task; the conclusion does not repeat it.
    expect(brief.conclusion).not.toContain(held.title.slice(0, 20));
  });

  it("counts every bucket in the same unit", () => {
    const zh = mapStatusSentence({ total: 11, qa: 4, complete: 5, ended: 1, reviewUnavailable: 1, held: 1,
      running: 0, pending: false, paused: true, hasOpenWork: true, zh: true });
    expect(zh).toContain("7 个任务");
    expect(zh).toContain("已完成 5 个");
    expect(zh).toContain("已结束 1 个");
    expect(zh).toContain("审查异常 1 个");
    expect(zh).toContain("阶段暂停 1 个");
    expect(zh).not.toMatch(/次/);
  });
});
