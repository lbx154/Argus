import { expect, it } from "vitest";
import { completionScope } from "../map/status";

it("uses explicit completion flags, not execution success or a missing flag", () => {
  const event = { id: "e", item_id: "a", type: "life.mission.completed", ts: 1, text: "", success: true };
  expect(completionScope(event, false)).toBe("");
  expect(completionScope({ ...event, overall_complete: true, campaign_continues: false }, false)).toBe("");
  expect(completionScope({ ...event, overall_complete: false }, false))
    .toBe("This execution ended; the overall goal was not complete at that time.");
  expect(completionScope({ ...event, campaign_continues: true }, true)).toContain("仍需后续工作");
});

it("counts accepted intermediate work as done without claiming overall completion", () => {
  const event = { id: "end", item_id: "task", type: "life.mission.completed", ts: 1, text: "",
    overall_complete: false, campaign_continues: true,
    outcome: { execution_status: "completed", review_status: "done", stage_certification: "deferred" } };
  expect(completionScope(event, true)).toBe("");
  expect(completionScope({ ...event, outcome: { review_status: "not_assessed" } }, true)).toContain("当时");
});
