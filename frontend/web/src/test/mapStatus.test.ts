import { describe, expect, it } from "vitest";
import { completionScope, mapStatusSentence } from "../map/status";

describe("map status sentence", () => {
  it("says how much is done and who is working", () => {
    expect(mapStatusSentence({ total: 6, complete: 6, running: 0, pending: false, paused: true, hasOpenWork: false, zh: true }))
      .toBe("6 个任务 · 已全部完成");
    expect(mapStatusSentence({ total: 3, complete: 1, running: 1, pending: false, paused: false, hasOpenWork: true, role: "engineer", zh: true }))
      .toBe("3 个任务 · 已完成 1 · 进行中 1 · Engineer 正在工作");
    expect(mapStatusSentence({ total: 3, complete: 1, running: 0, pending: false, paused: true, hasOpenWork: true, zh: false }))
      .toBe("3 tasks · 1 done · paused");
    expect(mapStatusSentence({ total: 0, complete: 0, running: 0, pending: true, paused: true, hasOpenWork: false, zh: false }))
      .toBe("0 tasks · working on your message");
  });

  it("counts partial executions separately and never calls them overall completion", () => {
    const input = { total: 2, complete: 1, ended: 1, running: 0, pending: false, paused: true, hasOpenWork: false };
    expect(mapStatusSentence({ ...input, zh: false }))
      .toBe("2 tasks · 1 done · 1 execution ended · nothing in progress · execution completion is not overall completion");
    expect(mapStatusSentence({ ...input, zh: true })).toContain("1 次执行已结束");
    expect(mapStatusSentence({ ...input, zh: true })).not.toContain("已全部完成");
  });

  it("uses explicit completion flags, not execution success or a missing flag", () => {
    const event = { id: "e", item_id: "a", type: "life.mission.completed", ts: 1, text: "", success: true };
    expect(completionScope(event, false)).toBe("");
    expect(completionScope({ ...event, overall_complete: true, campaign_continues: false }, false)).toBe("");
    expect(completionScope({ ...event, overall_complete: false }, false))
      .toBe("This execution ended; the overall goal is not complete.");
    expect(completionScope({ ...event, campaign_continues: true }, true)).toContain("仍需后续工作");
  });
});
