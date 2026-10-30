import { describe, expect, it } from "vitest";
import { buildSubmap, humanizeHarnessNote, noDetails } from "../map/submap";
import type { MapEvent, MapTask } from "../map/model";

// Verbatim from a live Atlas card that users flagged as unreadable.
const LIVE_RECEIPT =
  "One Engineer call used its whole per-call provider-turn allowance (1/3 in a row); " +
  "reviewer skipped. The work so far is kept and the task continues in a fresh session " +
  "from the checkpoint. Runner receipt: Provider turn cap reached: this engineer-r1 call " +
  "used 40 provider turns (allowance 40, ARGUS_SKILL_PROVIDER_TURN_CAP). Each further " +
  "turn would resend the whole grown transcript; the harness continues this work in a " +
  "fresh session instead.";

const task: MapTask = {
  id: "a",
  title: "A",
  status: "running",
  deps: [],
};

it('formats workspace paths before shortening the tool steps in a real map record', () => {
  const workspace = '/fastdata/v-boxiuli/argus-work/scratch/self10/argus/.integration/t3/workspace with spaces';
  const steps = buildSubmap(task, [{ id: 'tool', item_id: task.id, type: 'work.segment', ts: 100,
    role: 'engineer', text: `正在核对 ${workspace}/reports/verification.md。`,
    steps: [{ kind: 'command_execution', label: `$ cat "${workspace}/reports/verification.md"`, ts: 100 }] }], true, workspace);
  const text = steps.map(step => [step.title, step.summary, step.detail].join('\n')).join('\n');
  expect(text).toContain('reports/verification.md');
  expect(text).toContain('cat "reports/verification.md"');
  expect(text).not.toContain('/fastdata/');
});
const event = (
  id: string,
  type: string,
  extra: Partial<MapEvent> = {},
): MapEvent => ({
  id,
  type,
  item_id: "a",
  ts: Number(id.replace(/\D/g, "")) || 0,
  text: id,
  ...extra,
});

it("keeps failed historical SELF work neutral about missing tool details", () => {
  const steps = buildSubmap({ ...task, turn_kind: "qa", status: "failed" }, [
    event("e1", "work.segment", {
      role: "manager", status: "failed", steps: [],
      tool_details_recorded: false, association: "single_active_window",
      text: "Hard idle timeout",
    }),
    event("e2", "turn.replied", { status: "failed", text: "I am building it." }),
  ], false);
  const work = steps.find(step => step.id === "e1")!;
  expect(work.title).toBe("Recorded execution");
  expect(work.detail).toContain("Detailed tool steps were not recorded");
  expect(work.source).toBe("interval");
  expect(work.status).toBe("failed");
  expect(steps.find(step => step.id === "e2")!.status).toBe("failed");
});

describe("humanizeHarnessNote", () => {
  it("turns the provider-turn-cap record into one colleague sentence and moves the receipt aside", () => {
    const en = humanizeHarnessNote(LIVE_RECEIPT, false);
    expect(en.summary).toBe("Continued in a fresh session; earlier progress is kept");
    expect(en.receipt.startsWith("Runner receipt: Provider turn cap reached")).toBe(true);
    expect(en.summary).not.toMatch(/ARGUS_SKILL|provider[- ]turn|reviewer skipped/i);
    expect(humanizeHarnessNote(LIVE_RECEIPT, true).summary).toBe(
      "继续换了个新会话接着做，之前的进展都在",
    );
  });
  it("rewrites backend cooldown retries without transport jargon", () => {
    const note = humanizeHarnessNote(
      "backend failure; retrying in a fresh Codex session after 15.0s",
      false,
    );
    expect(note.summary).toBeTruthy();
    expect(note.summary).not.toMatch(/Codex|15\.0|backend/i);
    expect(note.receipt).toContain("retrying in a fresh Codex session");
  });
  it("rewrites the repeated-failure hold as a wait, not an accounting line", () => {
    const note = humanizeHarnessNote(
      "The backend has failed the same way 3 times in a row (rate limit). Waiting " +
        "120s before the next attempt so a standing failure stops costing money; if " +
        "this is a configuration problem, fix it or stop the mission.",
      true,
    );
    expect(note.summary).toBeTruthy();
    expect(note.summary).not.toContain("120");
  });
  it("rewrites quarantine notes", () => {
    const note = humanizeHarnessNote(
      "The task signature is quarantined out of planner rotation after repeated failures.",
      false,
    );
    expect(note.summary).toBeTruthy();
    expect(note.summary).not.toMatch(/quarantin/i);
  });
  it("always splits a Runner receipt tail even without a known pattern", () => {
    const note = humanizeHarnessNote("Adjusted the sampler bounds. Runner receipt: exit=2", false);
    expect(note.summary).toBe("");
    expect(note.receipt).toBe("Runner receipt: exit=2");
  });
  it("splits the newer technical-record markers the same way, in both languages", () => {
    const en = humanizeHarnessNote(
      "The Reviewer's session ended before it reached a conclusion, so this round was not judged. Technical record: exit=1, fatal_error=Copilot CLI exited with code 1.",
      false,
    );
    expect(en.summary).toBe("The Reviewer's session ended before it reached a conclusion, so this round was not judged");
    expect(en.receipt).toBe("Technical record: exit=1, fatal_error=Copilot CLI exited with code 1.");
    const zh = humanizeHarnessNote("这一轮没有人审阅。技术记录：exit=1", true);
    expect(zh.summary).toBe("");
    expect(zh.receipt).toBe("技术记录：exit=1");
    const rows = buildSubmap(task, [
      event("e1", "round.review.completed", { round_index: 1, status: "failed", text: "Adjusted the sampler bounds. Technical record: exit=2" }),
    ], false);
    const review = rows.find((row) => row.kind === "review")!;
    expect(review.summary).toBe("Adjusted the sampler bounds.");
    expect(review.detail).toContain("— technical record: exit=2");
    expect(review.detail).not.toContain("Technical record:");
  });
  it.each([
    ["operator_pause", "你暂停了工作，这一轮没有做完", "because you paused the work"],
    ["daemon_shutdown", "Argus 后台服务停止或重启了，这一轮没有做完", "because the Argus background service stopped or restarted"],
    ["budget_exhausted", "项目达到了预算上限，这一轮没有做完", "because the project reached its budget limit"],
  ])("names who or what stopped a round by its stop kind (%s)", (stop_kind, zh, en) => {
    // The sentence the runtime wrote says nothing to the reader; the stop kind does.
    const stopped = event("e1", "round.review.completed", {
      round_index: 1, status: "blocked", review_skipped: true, stop_kind,
      text: "You stopped Argus. Technical record: error=External interrupt: daemon stop requested",
    });
    const chinese = buildSubmap(task, [stopped], true).find((row) => row.kind === "review")!;
    expect(chinese.title).toBe("这一轮没做完就被停下");
    expect(chinese.summary).toContain(zh);
    expect(buildSubmap(task, [stopped], false).find((row) => row.kind === "review")!.summary).toContain(en);
  });

  it("leaves plain research prose untouched", () => {
    expect(humanizeHarnessNote("Found the leak in the eval split.", false)).toEqual({
      summary: "",
      receipt: "",
    });
  });
});

describe("step readability in buildSubmap", () => {
  it("distinguishes a successful execution from the unfinished overall goal even with recorded prose", () => {
    const rows = buildSubmap({ ...task, status: "done" }, [
      event("e1", "life.mission.completed", {
        success: true, overall_complete: false, campaign_continues: true,
        stage_certification: "intentionally_skipped", text: "Read one source route.",
      }),
    ], false);
    const result = rows.find((row) => row.kind === "result")!;
    expect(result.title).toBe("This execution ended");
    expect(result.status).toBe("recorded");
    expect(result.summary).toContain("the overall goal was not complete at that time and further work remained");
    expect(result.detail).toContain("Read one source route.");
    expect(result.detail).toContain("Stage certification was skipped.");
    expect(rows.some((row) => row.kind === "review")).toBe(false);
    const legacy = buildSubmap({ ...task, status: "done" }, [
      event("e2", "life.mission.completed", { success: true, text: "" }),
    ], false).find((row) => row.kind === "result")!;
    expect(legacy.status).toBe("done");
    expect(legacy.summary).toBe("The task was finished; its result is on record.");
  });

  it("shows the human sentence on the card and files the receipt after the prose", () => {
    const rows = buildSubmap(
      task,
      [
        event("e1", "round.review.completed", {
          round_index: 1,
          status: "continue",
          review_skipped: true,
          text: LIVE_RECEIPT,
          next_action: "Read the continuation note first.",
        }),
      ],
      false,
    );
    const review = rows.find((r) => r.kind === "review")!;
    expect(review.summary).toBe("Continued in a fresh session; earlier progress is kept");
    expect(review.detail).toContain("— technical record: Provider turn cap reached");
    const prose = review.detail.indexOf("One Engineer call");
    const receipt = review.detail.indexOf("— technical record:");
    expect(prose).toBeGreaterThanOrEqual(0);
    expect(prose).toBeLessThan(receipt);
    expect(review.detail.indexOf("Read the continuation note first.")).toBeLessThan(receipt);
  });
  it("derives execution titles from the first clean clause", () => {
    const rows = buildSubmap(
      task,
      [
        event("e1", "round.start", { round_index: 1, text: "round 1" }),
        event("e2", "round.main.completed", {
          round_index: 1,
          text: "Found the leak in the eval split. The split reused validation seeds across folds.",
        }),
      ],
      false,
    );
    const exec = rows.find((r) => r.kind === "execution")!;
    expect(exec.title).toBe("Found the leak in the eval split");
    expect(exec.summary).toBe("Found the leak in the eval split.");
  });
  it("keeps the generic round label when no clean clause exists", () => {
    const rows = buildSubmap(
      task,
      [
        event("e2", "round.main.completed", {
          round_index: 1,
          text: `${"x".repeat(80)} keeps going without a boundary`,
        }),
      ],
      true,
    );
    expect(rows.find((r) => r.kind === "execution")!.title).toBe("本轮执行记录");
  });
  it("titles finished reviews by verdict", () => {
    const rowFor = (status: string, zh: boolean) =>
      buildSubmap(
        task,
        [event("e1", "round.review.completed", { round_index: 1, status, text: "Reviewed." })],
        zh,
      ).find((r) => r.kind === "review")!;
    expect(rowFor("done", false).title).toBe("Review passed");
    expect(rowFor("engineer", false).title).toBe("Review: another pass asked for");
    expect(rowFor("manager", false).title).toBe("Review: a change of course asked for");
    // What an older release wrote reads as the holder it named.
    expect(rowFor("continue", false).title).toBe("Review: another pass asked for");
    expect(rowFor("blocked", false).title).toBe("Review: a change of course asked for");
    expect(rowFor("failed", false).title).toBe("Review not passed");
    expect(rowFor("done", true).title).toBe("审阅通过");
    expect(rowFor("planner", true).title).toBe("审阅：建议调整方向");
    expect(rowFor("replan_requested", true).title).toBe("审阅：建议调整方向");
  });
  it("keeps the verdict title and latest summary when start and completion merge", () => {
    const rows = buildSubmap(
      task,
      [
        event("e1", "round.review.started", { round_index: 1 }),
        event("e2", "round.review.completed", {
          round_index: 1,
          status: "done",
          text: "Looks solid.",
        }),
      ],
      false,
    );
    const reviews = rows.filter((r) => r.kind === "review");
    expect(reviews).toHaveLength(1);
    expect(reviews[0].title).toBe("Review passed");
    expect(reviews[0].summary).toBe("Looks solid.");
  });
  it("clips long records to one readable first sentence", () => {
    const long = `This first sentence carries the finding of the round. ${"More detail. ".repeat(40)}`;
    const rows = buildSubmap(
      task,
      [event("e2", "round.main.completed", { round_index: 1, text: long })],
      false,
    );
    const exec = rows.find((r) => r.kind === "execution")!;
    expect(exec.summary).toBe("This first sentence carries the finding of the round.");
    expect(exec.summary!.length).toBeLessThanOrEqual(140);
  });
  it("uses one fallback string for missing detail", () => {
    expect(noDetails(false)).toBe("Nothing has been written down for this step yet.");
    expect(noDetails(true)).toBe("这一步还没有留下记录");
    const rows = buildSubmap(
      task,
      [event("e2", "round.main.completed", { round_index: 1, text: "" })],
      false,
    );
    expect(rows.find((r) => r.kind === "execution")!.detail).toBe("Nothing has been written down for this step yet.");
  });
});

it("folds a started-then-failed report of one call into a single failed step", () => {
  // Real rows: a read reported running, then failed, same text, no call id.
  const label = 'read: {"cells": null, "includeOutputs": false, "limit": 100, "offset": 1, "pages": null, "path": "/data/proj/handoffs/0f40/CHECKPOINT.md"}';
  const steps = buildSubmap({ ...task, turn_kind: "qa" }, [
    event("e1", "work.segment", {
      role: "engineer", status: "recorded", text: "",
      steps: [
        { kind: "tool_use", label, ts: 1, tool: "read", status: "running" },
        { kind: "tool_use", label, ts: 2, tool: "read", status: "failed" },
      ],
    }),
  ], true);
  const work = steps.find(step => step.id === "e1")!;
  expect(work.title).toBe("做了 1 步操作");
  expect(work.workCount).toBe(1);
  expect(work.detail.match(/（失败）/g)).toHaveLength(1);
});

it('names an engineer continuation without a self-addressed arrow', () => {
  const steps = buildSubmap(task, [{ id: 'continued', item_id: task.id, type: 'handoff', ts: 1,
    role: 'engineer', to: 'engineer', text: '继续核查边界情况。' }], true);
  expect(steps.find(step => step.id === 'continued')?.title).toBe('工程师接着做');
});
