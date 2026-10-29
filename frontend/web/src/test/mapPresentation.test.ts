import { translate } from '../i18n';
import { describe, it, expect } from "vitest";
import { type MapTask } from "../map/model";
import { attentionReason, attentionSummary, referenceText, splitDraft } from "../map/presentation";
import type { MapEvent } from "../map/model";

it("localizes a failed task and keeps its most recent explicit failure as detail", () => {
  const task: MapTask = { id: "a", title: "Routing", objective: "", status: "failed", deps: [] };
  const event: MapEvent = { id: "old", item_id: "a", ts: 1, type: "life.mission.failed", text: "Old failure" };
  const events = [
    { ...event, id: "new", ts: 3, reason: "Route cost differs from the baseline" },
    event,
    { ...event, id: "other", ts: 4, item_id: "b", reason: "Another task" },
    { ...event, id: "progress", ts: 5, type: "round.start", text: "Inspecting files" },
  ];
  expect(attentionReason(task, events, false)).toBe(translate('error.agent_failed', {}, 'en'));
  expect(attentionReason({ ...task, pending_question: "Choose a route" }, events, false)).toBe("Choose a route");
  expect(attentionSummary(task, events, false).detail).toBe('Route cost differs from the baseline');
  expect(attentionSummary(task, [event], false).detail).toBe('Old failure');
  expect(attentionSummary(task, [], false).detail).toBe('');
});

it("explains a held or failed task from its recorded error when no failure event carries a reason", () => {
  const task: MapTask = { id: "a", title: "Experiment", objective: "", status: "failed", deps: [],
    last_error: "manager stage hold: Previous simulated data was invalidated; rerun on real traces" };
  const settled: MapEvent = { id: "s", item_id: "a", ts: 2, type: "life.mission.completed", text: "", success: false };
  expect(attentionReason(task, [settled], false)).toBe(translate('error.agent_failed', {}, 'en'));
  expect(attentionSummary(task, [settled], false).detail).toBe('Previous simulated data was invalidated; rerun on real traces');
  expect(attentionSummary({ ...task, last_error: "disk full" }, [], false).detail).toBe('disk full');
});

describe("map presentation and references", () => {
  it("keeps source and exact node identity in the same composer draft", () => {
    const ref = {
      source: "live:s-science",
      task_id: "a",
      task_title: "覆盖率比较",
      step_id: "e1",
      step_title: "审查与反馈",
      event_ids: ["e1"],
    };
    const draft = referenceText(ref) + "增加极端偏移条件";
    expect(splitDraft(draft)).toEqual({
      refs: [ref],
      text: "增加极端偏移条件",
    });
    expect(splitDraft("[[Argus引用 broken]]").text).toBe(
      "[[Argus引用 broken]]",
    );
  });

});

it("leaves malformed reference fields as editable text", () => {
  const malformed =
    '[[Argus引用 {"source":"live:s","task_id":"a","task_title":{},"event_ids":[]}]]';
  expect(splitDraft(malformed)).toEqual({ refs: [], text: malformed });
});

it("carries the quote-time locale through the marker and rejects bad types", () => {
  const ref = {
    source: "live:s-test",
    task_id: "a",
    task_title: "Coverage",
    event_ids: [],
    lang: "en",
  };
  const parsed = splitDraft(referenceText(ref) + "question");
  expect(parsed.refs).toEqual([ref]);
  expect(parsed.text).toBe("question");
  const bad = referenceText(ref).replace('"en"', "3");
  expect(splitDraft(bad).refs).toEqual([]);
  expect(splitDraft(bad).text).toBe(bad);
});
