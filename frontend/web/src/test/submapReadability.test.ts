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
  objective: "Measured objective",
  status: "running",
  deps: [],
};
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
  it("rewrites budget pauses", () => {
    const note = humanizeHarnessNote(
      "Paused because this project reached its budget limit: sampling study.\n" +
        "Existing work is saved; raise the project budget or narrow the task to continue.",
      true,
    );
    expect(note.summary).toContain("预算");
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
  it("leaves plain research prose untouched", () => {
    expect(humanizeHarnessNote("Found the leak in the eval split.", false)).toEqual({
      summary: "",
      receipt: "",
    });
  });
});

describe("step readability in buildSubmap", () => {
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
    expect(rows.find((r) => r.kind === "execution")!.title).toBe("完成一轮工作");
  });
  it("titles finished reviews by verdict", () => {
    const rowFor = (status: string, zh: boolean) =>
      buildSubmap(
        task,
        [event("e1", "round.review.completed", { round_index: 1, status, text: "Reviewed." })],
        zh,
      ).find((r) => r.kind === "review")!;
    expect(rowFor("done", false).title).toBe("The Reviewer was satisfied");
    expect(rowFor("continue", false).title).toBe("The Reviewer asked for another pass");
    expect(rowFor("blocked", false).title).toBe("The Reviewer asked to change course");
    expect(rowFor("failed", false).title).toBe("The Reviewer did not accept this round");
    expect(rowFor("done", true).title).toBe("审阅通过");
    expect(rowFor("replan", true).title).toBe("审阅者建议调整方向");
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
    expect(reviews[0].title).toBe("The Reviewer was satisfied");
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
