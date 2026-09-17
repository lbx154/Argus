import { describe, it, expect } from "vitest";
import { buildMap, connectMap, type MapTask } from "../map/model";
import { attentionReason, mergeMapCopy, needsCardCopy, referenceText, requestsFor, splitDraft, type MapCopy } from "../map/presentation";
import { buildSubmap } from "../map/submap";
import type { Dataset, MapEvent } from "../map/model";

describe('saved related-task source dependencies', () => {
  const task: MapTask = { id: 'a', title: 'Current task', objective: 'Assess the result', status: 'running', revision: 'a-v1', deps: ['b'] };
  const neighbor: MapTask = { id: 'b', title: 'Neighbor', objective: 'Old neighboring goal', status: 'pending', deps: [] };
  const data: Dataset = { id: 'live:related', kind: 'live', title: '', description: '', read_only: false,
    tasks: [task, neighbor, { ...neighbor, id: 'c', objective: 'An unrelated goal' }], events: [], tasks_complete: true };
  const request = { key: 'a', task_id: 'a', kind: 'task', event_ids: [] };
  const copy: MapCopy = { cards: { a: {
    title: 'Retained explanation', summary: 'Retained summary', detail: 'Retained detail', generated_at: 1,
    task_revision: task.revision, task_status: task.status,
    source_snapshot: { version: 2, card_key: 'a', task_id: 'a', captured_at: 1,
      task: {}, events: [], source_ids: [], related_tasks: [{ ...neighbor }] },
  } }, relations: [] };

  it.each([
    { objective: 'New neighboring goal' }, { title: 'Revised neighbor' },
    { status: 'cancelled' }, { deps: ['c'] },
  ])('requests an update for supplied neighboring fields: %j', change => {
    const before = structuredClone({ data, copy });
    expect(needsCardCopy(request, data, copy)).toBe(false);
    expect(needsCardCopy(request, { ...data, tasks: [task, { ...neighbor, ...change }] }, copy)).toBe(true);
    expect({ data, copy }).toEqual(before);
  });

  it('refreshes after deletion in complete history, and respects explicit removal in a partial view', () => {
    expect(needsCardCopy(request, { ...data, tasks: [task] }, copy)).toBe(true);
    expect(needsCardCopy(request, { ...data, tasks: [task], tasks_complete: false, history_cursor: 'history-page' }, copy)).toBe(true);
    expect(needsCardCopy(request, { ...data, tasks: [task], tasks_complete: false, removed_task_ids: ['b'] }, copy)).toBe(true);
    expect(needsCardCopy(request, { ...data, tasks: [task], tasks_complete: false }, copy)).toBe(false);
  });

  it('ignores other tasks, progress, and fields absent from related model context', () => {
    expect(needsCardCopy(request, { ...data, tasks: [task,
      { ...neighbor, revision: 'b-v2', summary: 'Later progress', acceptance_check: 'A later acceptance edit' },
      { ...data.tasks[2], objective: 'Changed unrelated goal', status: 'cancelled' },
    ] }, copy)).toBe(false);
    const legacy = structuredClone(copy);
    legacy.cards.a.source_snapshot!.version = 1;
    delete legacy.cards.a.source_snapshot!.related_tasks;
    expect(needsCardCopy(request, { ...data, tasks: [task] }, legacy)).toBe(false);
    delete legacy.cards.a.source_snapshot;
    expect(needsCardCopy(request, { ...data, tasks: [task] }, legacy)).toBe(false);
  });

  it('matches bounded Unicode text, dependency order, and source truncation markers', () => {
    const bounded = structuredClone(copy);
    const source = { ...neighbor, objective: '🧪'.repeat(500), objective_truncated: true,
      deps: Array.from({ length: 24 }, (_, index) => `dep-${index}`), deps_truncated: true };
    bounded.cards.a.source_snapshot!.related_tasks = [source];
    const current = { ...data, tasks: [task, { ...neighbor, objective: source.objective + 'a new unseen suffix', deps: [...source.deps, 'another tail'] }] };
    expect(needsCardCopy(request, current, bounded)).toBe(false);
    expect(needsCardCopy(request, { ...current, tasks: [task, { ...current.tasks[1], objective: source.objective }] }, bounded)).toBe(true);
    expect(needsCardCopy(request, { ...current, tasks: [task, { ...current.tasks[1], deps: [...source.deps].reverse() }] }, bounded)).toBe(true);
  });

  it('compares a truncated source identity without repeatedly mistaking its unchanged task for deletion', () => {
    const bounded = structuredClone(copy);
    bounded.cards.a.source_snapshot!.related_tasks = [{ ...neighbor, id: '🧪'.repeat(160), id_truncated: true }];
    const longNeighbor = { ...neighbor, id: '🧪'.repeat(161) };
    const current = { ...data, tasks: [task, longNeighbor] };
    expect(needsCardCopy(request, current, bounded)).toBe(false);
    expect(needsCardCopy(request, { ...current, tasks: [task, { ...longNeighbor, status: 'cancelled' }] }, bounded)).toBe(true);
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
      team_id: 'idea-portfolio',
      team_task_id: 'idea-portfolio-route-01',
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
          evidence: "shared methods",
        },
        { source: "ghost", target: "d", label: "bad", evidence: "bad" },
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
