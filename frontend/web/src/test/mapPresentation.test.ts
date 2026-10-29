import { describe, it, expect } from "vitest";
import { referenceText, splitDraft } from "../map/presentation";

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
