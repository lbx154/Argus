import { describe, expect, it } from "vitest";
import { mapStatusSentence } from "../map/status";

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
});
