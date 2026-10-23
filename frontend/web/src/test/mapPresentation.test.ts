import { describe, it, expect } from "vitest";
import { buildMap, connectMap, type MapTask } from "../map/model";
import { attentionReason, mergeMapCopy, needsCardCopy, projectBrief, referenceText, requestsFor, splitDraft, type MapCopy } from "../map/presentation";
import { buildSubmap } from "../map/submap";
import type { Dataset, MapEvent } from "../map/model";

describe('card freshness without generation-time source snapshots', () => {
  const task: MapTask = { id: 'a', title: 'Current task', objective: 'Assess the result', status: 'running', revision: 'a-v1', deps: ['b'] };
  const neighbor: MapTask = { id: 'b', title: 'Neighbor', objective: 'Old neighboring goal', status: 'pending', deps: [] };
  const data: Dataset = { id: 'live:related', kind: 'live', title: '', description: '', read_only: false,
    tasks: [task, neighbor, { ...neighbor, id: 'c', objective: 'An unrelated goal' }], events: [], tasks_complete: true };
  const request = { key: 'a', task_id: 'a', kind: 'task', event_ids: [] };
  const copy: MapCopy = { cards: { a: {
    title: 'Retained explanation', summary: 'Retained summary', detail: 'Retained detail', generated_at: 1,
    task_revision: task.revision, task_status: task.status,
  } }, relations: [] };

  it.each([
    { objective: 'New neighboring goal' }, { title: 'Revised neighbor' },
    { status: 'cancelled' }, { deps: ['c'] },
  ])('does not buy another explanation for neighboring fields: %j', change => {
    const before = structuredClone({ data, copy });
    expect(needsCardCopy(request, data, copy)).toBe(false);
    expect(needsCardCopy(request, { ...data, tasks: [task, { ...neighbor, ...change }] }, copy)).toBe(false);
    expect({ data, copy }).toEqual(before);
  });

  it('does not refresh for unrelated deletion, cursor changes or a partial history view', () => {
    expect(needsCardCopy(request, { ...data, tasks: [task] }, copy)).toBe(false);
    expect(needsCardCopy(request, { ...data, tasks: [task], tasks_complete: false, history_cursor: 'history-page' }, copy)).toBe(false);
    expect(needsCardCopy(request, { ...data, tasks: [task], tasks_complete: false, removed_task_ids: ['b'] }, copy)).toBe(false);
    expect(needsCardCopy(request, { ...data, tasks: [task], tasks_complete: false }, copy)).toBe(false);
  });

  it('ignores other tasks, progress, and fields absent from related model context', () => {
    expect(needsCardCopy(request, { ...data, tasks: [task,
      { ...neighbor, revision: 'b-v2', summary: 'Later progress', acceptance_check: 'A later acceptance edit' },
      { ...data.tasks[2], objective: 'Changed unrelated goal', status: 'cancelled' },
    ] }, copy)).toBe(false);
  });

  it('still detects changes to the selected task and selected events', () => {
    expect(needsCardCopy(request, { ...data, tasks: [{ ...task, revision: 'a-v2' }] }, copy)).toBe(true);
    expect(needsCardCopy({ ...request, event_ids: ['review'] }, { ...data,
      events: [{ id: 'review', item_id: task.id, type: 'round.review.completed', ts: 2, text: 'New result' }] }, copy)).toBe(true);
  });
});

it("shows the recorded question or most recent explicit failure, never an unrelated summary", () => {
  const task: MapTask = { id: "a", title: "Routing", objective: "", status: "failed", deps: [] };
  const event: MapEvent = { id: "old", item_id: "a", ts: 1, type: "life.mission.failed", text: "Old failure" };
  const events = [
    { ...event, id: "new", ts: 3, reason: "Route cost differs from the baseline" },
    event,
    { ...event, id: "other", ts: 4, item_id: "b", reason: "Another task" },
    { ...event, id: "progress", ts: 5, type: "round.start", text: "Inspecting files" },
  ];
  expect(attentionReason(task, events, false)).toBe("Route cost differs from the baseline");
  expect(attentionReason({ ...task, pending_question: "Choose a route" }, events, false)).toBe("Choose a route");
  expect(attentionReason(task, [event], false)).toBe("Old failure");
  expect(attentionReason(task, [], false)).toBe("This task did not finish, and the record does not say why.");
});

it("explains a held or failed task from its recorded error when no failure event carries a reason", () => {
  const task: MapTask = { id: "a", title: "Experiment", objective: "", status: "failed", deps: [],
    last_error: "manager stage hold: Previous simulated data was invalidated; rerun on real traces" };
  const settled: MapEvent = { id: "s", item_id: "a", ts: 2, type: "life.mission.completed", text: "", success: false };
  expect(attentionReason(task, [settled], false)).toBe("Previous simulated data was invalidated; rerun on real traces");
  expect(attentionReason({ ...task, last_error: "disk full" }, [], false)).toBe("disk full");
});

it("keeps new model settings when an earlier generation finishes", () => {
  const previous = { cards: {}, relations: [], model_revision: "new-model", available: false };
  const result = { cards: {}, relations: [], model_revision: "old-model" };
  expect(mergeMapCopy(previous, result, "old-model")).toMatchObject({ model_revision: "new-model", available: false });
  expect(mergeMapCopy(result, previous, "old-model")).toMatchObject({ model_revision: "new-model", available: false });
});

it('refreshes stable old cards for a newer server version and keeps late responses from downgrading it', () => {
  const task: MapTask = { id: 'historical', title: 'Historical work', objective: '', status: 'done', revision: 'r1' };
  const data: Dataset = { id: 'live:project', kind: 'live', title: '', description: '', read_only: false, tasks: [task], events: [] };
  const request = { key: task.id, task_id: task.id, kind: 'task', event_ids: [] };
  const card = { title: '旧中文标题', summary: 'Old summary', detail: 'Old details', generated_at: 1,
    version: 14, copy_revision: 1, task_revision: 'r1', task_status: 'done', event_ids: [] };
  const known = { version: 15, cards: { [task.id]: card }, relations: [] };
  expect(needsCardCopy(request, data, known)).toBe(true);
  const updated = { ...card, version: 15, copy_revision: 2, generated_at: 2, title: '新版中文标题' };
  // POST results need not repeat the server's top-level version.
  const current = mergeMapCopy(known, { cards: { [task.id]: updated }, relations: [] });
  expect(current.version).toBe(15);
  expect(needsCardCopy(request, data, current)).toBe(false);
  const late = mergeMapCopy(current, { version: 14, cards: { [task.id]: { ...card, copy_revision: 3, generated_at: 3 } }, relations: [] });
  expect(late.version).toBe(15);
  expect(late.cards[task.id].title).toBe(updated.title);
});

it.each(['task', 'earlier-review'])('requires the current draft/review settings for %s without changing retained copy', (key) => {
  const task: MapTask = { id: 'task', title: 'Recorded task', objective: 'Original objective', status: 'done', revision: 'task-v1', content_revision: 'content-v1' };
  const data: Dataset = { id: 'live:project', kind: 'live', title: '', description: '', read_only: false, tasks: [task],
    events: [{ id: 'event', item_id: task.id, type: 'round.review.completed', ts: 1, text: 'Original event', revision: 'event-v1' }] };
  const request = { key, task_id: task.id, kind: key === task.id ? 'task' : 'review', event_ids: ['event'] };
  const card = { title: 'Retained title', summary: 'Retained summary', detail: 'Original conditions', generated_at: 2,
    version: 21, model_revision: 'previous-pipeline', task_revision: task.revision, task_content_revision: task.content_revision,
    task_status: task.status, event_ids: ['event'], event_revisions: ['event-v1'] };
  const copy = { version: 21, model_revision: 'current-pipeline', cards: { [key]: card }, relations: [] };
  const before = structuredClone({ data, copy });
  expect(needsCardCopy(request, data, copy)).toBe(true);
  expect(needsCardCopy(request, data, { ...copy, model_revision: card.model_revision })).toBe(false);
  expect(needsCardCopy(request, data, { ...copy, cards: { [key]: { ...card, model_revision: undefined } } })).toBe(true);
  expect({ data, copy }).toEqual(before);
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
  it("connects all components without changing real dependencies or inventing missing nodes", () => {
    const tasks: MapTask[] = ["a", "b", "c", "d"].map((id, i) => ({
      id,
      title: id,
      objective: id,
      status: "done",
      deps: id === "b" ? ["a"] : [],
      ts: i,
    }));
    const graph = buildMap(tasks);
    const links = connectMap(
      graph,
      [
        {
          source: "b",
          target: "c",
          label: "对比实验",
        },
        { source: "ghost", target: "d", label: "bad" },
      ],
      true,
    );
    expect(links).toHaveLength(3);
    expect(links.find((l) => l.kind === "semantic")?.source).toBe("b");
    expect(links.find((l) => l.kind === "context")?.target).toBe("d");
    expect(graph.links).toHaveLength(1);
    expect(tasks[2].deps).toEqual([]);
  });
});

it("leads the brief with a question waiting on the reader, then the work under way, then the latest finish", () => {
  // 2026-10-21: the first task waited on the reader's answer while a later
  // one finished, and the brief only said the later one was done.
  const asked: MapTask = { id: "t", title: "Settle truncate", objective: "Pick A or B for truncate",
    status: "parked", deps: [], ts: 1, pending_question: "A or B?" };
  const counted: MapTask = { id: "c", title: "Add char_count", objective: "Add char_count",
    status: "done", deps: [], ts: 2, finished_ts: 5 };
  const waiting = projectBrief([asked, counted], [], true);
  expect([waiting.goal, waiting.current]).toEqual(["Pick A or B for truncate", "等你回答"]);
  expect(waiting.next).toMatch(/回答这个问题/);
  const answered = { ...asked, status: "running", pending_question: "" };
  const working = projectBrief([answered, counted], [], true);
  expect([working.goal, working.current]).toEqual(["Pick A or B for truncate", "正在处理：Settle truncate"]);
  const finished = projectBrief([{ ...answered, status: "done", finished_ts: 9 }, counted], [], true);
  expect([finished.goal, finished.current]).toEqual(["Pick A or B for truncate", "已完成"]);
});

it("keeps a waiting question at the head of the brief over what the runtime reports live", () => {
  // Trial s-934a4569: with char_count finished, the live status "waiting for
  // the next step" covered the brief's "waiting on you".
  const asked: MapTask = { id: "t", title: "Settle truncate", objective: "Pick A or B", status: "parked", deps: [], ts: 1, pending_question: "A or B?" };
  const counting: MapTask = { id: "c", title: "Add char_count", objective: "Add char_count", status: "running", deps: [], ts: 2 };
  expect(projectBrief([asked, { ...counting, status: "done", finished_ts: 5 }], [], true, () => "", "等待下一步工作").current).toBe("等你回答");
  expect(projectBrief([asked, counting], [], true, () => "", "运行终端命令：Add char_count").current).toBe("等你回答");
  expect(projectBrief([{ ...asked, pending_question: "", status: "done" }, counting], [], true, () => "", "运行终端命令：Add char_count").current)
    .toBe("运行终端命令：Add char_count");
});

it("says a result that passed review while a question waits, and what an accepted task found", () => {
  // Trial s-36ca9747: format_percent passed review while format_bytes waited on
  // the reader, and the brief said there was no checked result while its card
  // said 审阅通过. Once all was done it read 已确认：确定 format_bytes 的换算标准并实现,
  // the request's title rather than what was found.
  const asked: MapTask = { id: "b", title: "确定 format_bytes 的换算标准并实现", objective: "A or B", status: "parked",
    deps: [], ts: 1, pending_question: "选 A 还是 B？" };
  const percent: MapTask = { id: "p", title: "新增百分比格式化函数及测试", objective: "format_percent", status: "done",
    deps: [], ts: 2, finished_ts: 5, outcome: { review_status: "done", stage_certification: "certified" } };
  const completion: MapEvent = { id: "e", item_id: "p", type: "life.mission.completed", ts: 6,
    text: "format_percent 已按要求交付。", overall_complete: true };
  const waiting = projectBrief([asked, percent], [completion], true);
  expect(waiting.conclusion).toBe("审阅通过（还有一项在等你回答）：format_percent 已按要求交付。");
  expect(waiting.next).toBe("回答这个问题，团队才会继续。");
  expect(projectBrief([{ ...asked, status: "done", pending_question: "" }, percent], [completion], true).conclusion)
    .toBe("已确认：format_percent 已按要求交付。");
  expect(projectBrief([asked, percent], [completion], false).conclusion)
    .toBe("Reviewed (another item is waiting for your answer): format_percent 已按要求交付。");
});

it("says what was found as plain words, cut after its markup goes", () => {
  // Trial s-f0e1b2f0: the banner read "已确认：…实现复用 `_check`…`from sizefmt import format_mib` 可用".
  const done: MapTask = { id: "m", title: "实现 format_mib", objective: "format_mib", status: "done", deps: [], ts: 1, finished_ts: 2,
    outcome: { review_status: "done", stage_certification: "certified" } };
  const completion: MapEvent = { id: "e", item_id: "m", type: "life.mission.completed", ts: 3, overall_complete: true,
    text: "**已通过独立审阅。**\n\n实现复用 `_check`；`from sizefmt import format_mib` 可用，`__all__` 包含该名称。" };
  expect(projectBrief([done], [completion], true).conclusion)
    .toBe("已确认：已通过独立审阅。 实现复用 _check；from sizefmt import format_mib 可用，__all__ 包含该名称。");
});

it("says the next step once, under the brief's own Next label", () => {
  // 2026-10-21: the map read 接下来：下一步：回答这个问题.
  const asked: MapTask = { id: "q", title: "Settle truncate", objective: "Pick A or B", status: "parked", deps: [], ts: 1, pending_question: "A or B?" };
  const failed: MapTask = { id: "f", title: "Port the parser", objective: "Port it", status: "failed", deps: [], ts: 1, finished_ts: 2 };
  const queued: MapTask = { id: "p", title: "Write the notes", objective: "Notes", status: "pending", deps: [], ts: 3 };
  expect([[asked], [failed], [queued]].map((tasks) => projectBrief(tasks, [], true).next))
    .toEqual(["回答这个问题，团队才会继续。", "查看原因后重试，或在对话里调整任务。", "待做任务：Write the notes"]);
  for (const tasks of [[asked], [failed], [queued]]) expect(projectBrief(tasks, [], false).next).not.toMatch(/^Next/);
});

it("leaves malformed reference fields as editable text", () => {
  const malformed =
    '[[Argus引用 {"source":"live:s","task_id":"a","task_title":{},"event_ids":[]}]]';
  expect(splitDraft(malformed)).toEqual({ refs: [], text: malformed });
});

it("does not use a later successful attempt as evidence for an earlier failed result", () => {
  const task: MapTask = {
    id: "a",
    title: "比较覆盖率",
    objective: "比较方法",
    status: "done",
    deps: [],
  };
  const events: MapEvent[] = [
    "life.mission.started",
    "round.main.completed",
    "round.review.completed",
    "life.mission.failed",
    "life.mission.started",
    "round.main.completed",
    "round.review.completed",
    "life.mission.completed",
  ].map((type, ts) => ({ id: `e${ts}`, type, ts, item_id: "a", text: "" }));
  const data = { id: "live:s-test", tasks: [task], events } as Dataset;
  const requests = requestsFor(data, buildSubmap(task, events, true), task.id);
  expect(requests.find((r) => r.key === "e3")?.event_ids).toEqual([
    "e1",
    "e2",
    "e3",
  ]);
  expect(requests.find((r) => r.key === "e7")?.event_ids).toEqual([
    "e5",
    "e6",
    "e7",
  ]);
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
