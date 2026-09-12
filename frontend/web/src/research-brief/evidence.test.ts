import { describe, expect, it } from 'vitest';
import type { MapEvent, MapTask } from '../map/model';
import type { CardCopy, CardSourceSnapshot } from '../map/presentation';
import { evidenceDates, hasTruncatedFields, selectReaderEvidence } from './evidence';

const taskId = 'task-1';
const cardKey = 'review-attempt-1';
const event = (patch: Partial<MapEvent> = {}): MapEvent => ({
  id: 'source-a', item_id: taskId, revision: 'event-v1', type: 'round.review.completed',
  ts: 100, text: 'Complete original finding, including the final condition.', attempt: 1,
  ...patch,
});
const task = (patch: Partial<MapTask> = {}): MapTask => ({
  id: taskId, title: 'Original task', objective: 'Original objective and its complete acceptance condition.',
  status: 'done', revision: 'task-v1', content_revision: 'goal-v1',
  summary: 'Complete task summary.', attempt: 1,
  ...patch,
});
const card = (patch: Partial<CardCopy> = {}): CardCopy => ({
  title: 'Retained explanation', summary: 'Retained summary', detail: 'Retained detail', generated_at: 120,
  task_revision: 'task-v1', task_content_revision: 'goal-v1', event_ids: ['source-a'], event_revisions: ['event-v1'],
  ...patch,
});
const snapshot = (patch: Partial<CardSourceSnapshot> = {}): CardSourceSnapshot => ({
  version: 1, card_key: cardKey, task_id: taskId, captured_at: 110,
  task: { title: 'Original task', objective: 'Original objective', objective_truncated: true },
  events: [{ id: 'source-a', item_id: taskId, revision: 'event-v1', type: 'round.review.completed',
    ts: 100, text: 'Original finding excerpt', text_truncated: true, attempt: 1 }],
  source_ids: ['source-a'],
  ...patch,
});

describe('selectReaderEvidence retained snapshots', () => {
  it('keeps exact retained task and event material when the same event ID and task have newer versions', () => {
    const retained = snapshot({ events_truncated: true });
    const newerSource = event({ revision: 'event-v2', text: 'Revised finding from a later attempt.', ts: 300, attempt: 2 });
    const newEvent = event({ id: 'source-b', revision: 'event-b1', text: 'Another later finding.', ts: 400, attempt: 2 });
    const currentTask = task({ objective: 'Changed objective.', revision: 'task-v2', content_revision: 'goal-v2', attempt: 2 });
    const selected = selectReaderEvidence({ cardKey, taskId,
      card: card({ source_snapshot: retained, event_ids: ['a-different-request'], event_revisions: ['unrelated'] }),
      task: currentTask, loadedEvents: [newerSource, newEvent], currentEvents: [newerSource, newEvent] });

    expect(selected.mode).toBe('snapshot');
    expect(selected.snapshot).toBe(retained);
    expect(selected.usedTask?.record).toBe(retained.task);
    expect(selected.usedTask?.record).not.toHaveProperty('id');
    expect(selected.usedTask?.record).not.toHaveProperty('status');
    expect(selected.usedTask?.fullRecord).toBeUndefined();
    expect(selected.used).toEqual([{ id: 'source-a', revision: 'event-v1', state: 'snapshot', record: retained.events[0] }]);
    expect(selected.used[0].record).toBe(retained.events[0]);
    expect(selected.currentTask).toEqual(currentTask);
    expect(selected.current).toEqual([{ record: newerSource, reason: 'changed' }, { record: newEvent, reason: 'new' }]);
    expect(evidenceDates(selected)).toEqual([100, 100]);
    expect(selected.snapshot?.events_truncated).toBe(true);
  });

  it('preserves a verified complete original separately from the exact bounded event excerpt', () => {
    const retained = snapshot();
    const full = event({ next_action: 'Run the remaining check.', steps: [{ kind: 'tool', label: 'Final condition', ts: 101, tool: 'read' }] });
    const foreign = event({ item_id: 'another-task', text: 'Foreign complete record.' });
    const selected = selectReaderEvidence({ cardKey, taskId, card: card({ source_snapshot: retained }),
      loadedEvents: [foreign, full], currentEvents: [full] });

    expect(selected.used[0]).toMatchObject({ state: 'snapshot', revision: 'event-v1', fullRecord: full });
    expect(selected.used[0].record).toBe(retained.events[0]);
    expect(selected.used[0].record).not.toHaveProperty('steps');
    expect(selected.used[0].record).not.toHaveProperty('next_action');
    expect(selected.used[0].fullRecord?.text).toContain('final condition');
    expect(selected.current).toEqual([]);
  });

  it('finds a verified complete original in current records even when no separate loaded list was supplied', () => {
    const full = event();
    const selected = selectReaderEvidence({ cardKey, taskId, card: card({ source_snapshot: snapshot() }), currentEvents: [full] });
    expect(selected.used[0].fullRecord).toEqual(full);
    expect(selected.current).toEqual([]);
  });

  it.each([
    { label: 'changed loaded revision', savedRevision: 'event-v1', loadedRevision: 'event-v2', owner: taskId },
    { label: 'missing loaded revision', savedRevision: 'event-v1', loadedRevision: undefined, owner: taskId },
    { label: 'empty loaded revision', savedRevision: 'event-v1', loadedRevision: '', owner: taskId },
    { label: 'missing saved revision', savedRevision: undefined, loadedRevision: 'event-v1', owner: taskId },
    { label: 'both revisions missing', savedRevision: undefined, loadedRevision: undefined, owner: taskId },
    { label: 'matching revision from another task', savedRevision: 'event-v1', loadedRevision: 'event-v1', owner: 'another-task' },
  ])('does not attach an unverified full original: $label', ({ savedRevision, loadedRevision, owner }) => {
    const retained = snapshot();
    retained.events[0] = { ...retained.events[0], revision: savedRevision };
    const current = event({ revision: loadedRevision, item_id: owner, text: 'Loaded content must not replace the excerpt.' });
    const selected = selectReaderEvidence({ cardKey, taskId, card: card({ source_snapshot: retained }),
      loadedEvents: [current], currentEvents: [current] });
    expect(selected.used[0].record).toBe(retained.events[0]);
    expect(selected.used[0].fullRecord).toBeUndefined();
    expect(selected.current.map(row => row.record)).toEqual(owner === taskId ? [current] : []);
  });

  it('keeps both a verified old full original and an updated source outside the latest request', () => {
    const oldFull = event();
    const updated = event({ revision: 'event-v2', text: 'Newer version of the source.', ts: 200 });
    const requested = event({ id: 'source-b', revision: 'event-b1', text: 'Latest requested event.' });
    const unrelated = event({ id: 'not-a-source-or-request', text: 'An unrelated loaded event.' });
    const selected = selectReaderEvidence({ cardKey, taskId, card: card({ source_snapshot: snapshot() }),
      loadedEvents: [updated, oldFull, unrelated], currentEvents: [requested] });
    expect(selected.used[0].fullRecord).toEqual(oldFull);
    expect(selected.current).toEqual([{ record: updated, reason: 'changed' }, { record: requested, reason: 'new' }]);
  });

  it('does not fill missing or foreign retained events with currently loaded bodies', () => {
    const retained = snapshot({ source_ids: ['missing-source', 'foreign-source'], events: [
      { id: 'foreign-source', item_id: 'another-task', revision: 'foreign-v1', ts: 100, text: 'Foreign retained body.' },
    ] });
    const available = [event({ id: 'missing-source' }), event({ id: 'foreign-source', revision: 'foreign-v1' })];
    const selected = selectReaderEvidence({ cardKey, taskId, card: card({ source_snapshot: retained }), loadedEvents: available });
    expect(selected.used).toEqual([{ id: 'missing-source', state: 'missing' }, { id: 'foreign-source', state: 'owner-mismatch' }]);
    expect(selected.current).toEqual(available.map(record => ({ record, reason: 'unverified' })));
    expect(evidenceDates(selected)).toEqual([]);
  });

  it('preserves source order even when retained event dictionaries arrive in a different order', () => {
    const first = { ...event({ id: 'first', revision: 'first-v1', ts: 90 }) };
    const second = { ...event({ id: 'second', revision: 'second-v1', ts: 80 }) };
    const retained = snapshot({ source_ids: ['first', 'second'], events: [second, first] });
    const selected = selectReaderEvidence({ cardKey, taskId, card: card({ source_snapshot: retained }) });
    expect(selected.used.map(row => row.record)).toEqual([first, second]);
    expect(evidenceDates(selected)).toEqual([80, 90]);
  });

  it.each([
    { label: 'another card', patch: { card_key: 'another-card' } },
    { label: 'another task', patch: { task_id: 'another-task' } },
  ])('rejects retained material belonging to $label and checks legacy versions instead', ({ patch }) => {
    const full = event();
    const retained = snapshot({ ...patch, task: { objective: 'Foreign goal.' },
      events: [{ ...event(), text: 'Foreign retained source.' }] });
    const selected = selectReaderEvidence({ cardKey, taskId, card: card({ source_snapshot: retained }), loadedEvents: [full] });
    expect(selected.mode).toBe('legacy');
    expect(selected.invalidSnapshot).toBe(true);
    expect(selected.snapshot).toBeUndefined();
    expect(selected.used).toEqual([{ id: full.id, revision: full.revision, state: 'verified', record: full }]);
    expect(selected.usedTask?.record).toBeUndefined();
  });
});

describe('selectReaderEvidence legacy records', () => {
  it('recovers an old attempt only when its event ID, owner and saved revision all match', () => {
    const foreign = event({ item_id: 'another-task' });
    const later = event({ revision: 'event-v2', text: 'Later attempt.', attempt: 2 });
    const old = event({ text: 'Earlier attempt with its original complete condition.', attempt: 1 });
    const retainedCard = card();
    const absent = selectReaderEvidence({ cardKey, taskId, card: retainedCard, loadedEvents: [foreign, later] });
    expect(absent.used).toEqual([{ id: 'source-a', revision: 'event-v1', state: 'changed' }]);
    expect(absent.current).toEqual([{ record: later, reason: 'changed' }]);

    const restored = selectReaderEvidence({ cardKey, taskId, card: retainedCard, loadedEvents: [foreign, later, old] });
    expect(restored.used).toEqual([{ id: 'source-a', revision: 'event-v1', state: 'verified', record: old }]);
    expect(restored.current).toEqual([{ record: later, reason: 'changed' }]);
    expect(evidenceDates(restored)).toEqual([100, 100]);
  });

  it.each([
    { label: 'missing record', savedRevision: 'event-v1', records: [], state: 'missing' },
    { label: 'changed revision', savedRevision: 'event-v1', records: [event({ revision: 'event-v2' })], state: 'changed' },
    { label: 'missing saved revision', savedRevision: undefined, records: [event()], state: 'unverified' },
    { label: 'empty saved revision', savedRevision: '', records: [event()], state: 'unverified' },
    { label: 'missing current revision', savedRevision: 'event-v1', records: [event({ revision: undefined })], state: 'unverified' },
    { label: 'foreign owner', savedRevision: 'event-v1', records: [event({ item_id: 'another-task' })], state: 'owner-mismatch' },
    { label: 'matching revision on a different ID', savedRevision: 'event-v1', records: [event({ id: 'different-id' })], state: 'missing' },
  ])('leaves a body-free placeholder for a $label', ({ savedRevision, records, state }) => {
    const selected = selectReaderEvidence({ cardKey, taskId,
      card: card({ event_revisions: savedRevision === undefined ? undefined : [savedRevision] }), loadedEvents: records });
    expect(selected.used[0]).toMatchObject({ id: 'source-a', state });
    expect(selected.used[0].record).toBeUndefined();
    expect(selected.used[0].fullRecord).toBeUndefined();
    expect(evidenceDates(selected)).toEqual([]);
    expect(selected.current.map(row => row.record)).toEqual(records.filter(record => record.item_id === taskId && record.id === 'source-a'));
  });

  it('pairs saved event revisions by their source ID positions', () => {
    const first = event({ id: 'first', revision: 'first-v1' });
    const second = event({ id: 'second', revision: 'second-v1' });
    const selected = selectReaderEvidence({ cardKey, taskId,
      card: card({ event_ids: ['second', 'first'], event_revisions: ['second-v1', 'first-v1'] }), loadedEvents: [first, second] });
    expect(selected.used.map(row => row.record)).toEqual([second, first]);
    expect(selected.current).toEqual([]);
  });

  it('deduplicates the same current event revision loaded through two lists while preserving its updated body', () => {
    const changed = event({ revision: 'event-v2', text: 'Changed source still available outside the latest request.' });
    const requested = event({ id: 'new-source', revision: 'new-v1' });
    const selected = selectReaderEvidence({ cardKey, taskId, card: card(), loadedEvents: [changed, requested], currentEvents: [requested, changed] });
    expect(selected.used[0].record).toBeUndefined();
    expect(selected.current).toEqual([{ record: changed, reason: 'changed' }, { record: requested, reason: 'new' }]);
  });
});

describe('selectReaderEvidence task goals and full task records', () => {
  it('keeps the exact snapshot task and a same-version complete task independently even when the goal was truncated', () => {
    const retained = snapshot();
    const full = task();
    const selected = selectReaderEvidence({ cardKey, taskId, card: card({ source_snapshot: retained }), task: full });
    expect(selected.usedTask?.state).toBe('snapshot');
    expect(selected.usedTask?.record).toBe(retained.task);
    expect(selected.usedTask?.record).not.toHaveProperty('status');
    expect(selected.usedTask?.fullRecord).toEqual(full);
  });

  it.each([
    { label: 'changed goal despite unchanged task revision', saved: {}, current: { content_revision: 'goal-v2' } },
    { label: 'changed task status revision despite unchanged goal', saved: {}, current: { revision: 'task-v2' } },
    { label: 'missing saved task revision', saved: { task_revision: undefined }, current: {} },
    { label: 'missing current task revision', saved: {}, current: { revision: undefined } },
    { label: 'all versions unknown', saved: { task_revision: undefined, task_content_revision: undefined }, current: { revision: undefined, content_revision: undefined } },
  ])('does not call a current task a verified full original with $label', ({ saved, current }) => {
    const retained = snapshot();
    const currentTask = task(current);
    const selected = selectReaderEvidence({ cardKey, taskId, card: card({ ...saved, source_snapshot: retained }), task: currentTask });
    expect(selected.usedTask?.record).toBe(retained.task);
    expect(selected.usedTask?.fullRecord).toBeUndefined();
    expect(selected.currentTask).toEqual(currentTask);
  });

  it('does not recover a legacy goal using a matching task revision after its content revision changed', () => {
    const currentTask = task({ objective: 'New goal that the old explanation never saw.', content_revision: 'goal-v2' });
    const selected = selectReaderEvidence({ cardKey, taskId, card: card(), task: currentTask });
    expect(selected.usedTask).toEqual({ state: 'changed' });
    expect(selected.currentTask).toEqual(currentTask);
  });

  it('keeps a legacy goal unknown when neither saved version can verify the current task', () => {
    const currentTask = task();
    const selected = selectReaderEvidence({ cardKey, taskId,
      card: card({ task_revision: undefined, task_content_revision: undefined }), task: currentTask });
    expect(selected.usedTask).toEqual({ state: 'unverified' });
    expect(selected.currentTask).toEqual(currentTask);
  });

  it.each([
    { label: 'matching content and task revisions', saved: {}, current: {} },
    { label: 'matching content with a newer status revision', saved: {}, current: { revision: 'task-v2', status: 'failed' } },
    { label: 'matching task revision when saved content version is unavailable', saved: { task_content_revision: undefined }, current: {} },
    { label: 'matching task revision when current content version is unavailable', saved: {}, current: { content_revision: undefined } },
  ])('recovers only the verified legacy goal and title with $label', ({ saved, current }) => {
    const currentTask = task(current);
    const selected = selectReaderEvidence({ cardKey, taskId, card: card(saved), task: currentTask });
    expect(selected.usedTask).toEqual({ state: 'verified', record: { title: currentTask.title, objective: currentTask.objective } });
    expect(selected.usedTask?.fullRecord).toBeUndefined();
    expect(selected.currentTask).toEqual(currentTask);
  });

  it('never borrows a task goal from a different owner', () => {
    const foreign = task({ id: 'another-task' });
    const legacy = selectReaderEvidence({ cardKey, taskId, card: card(), task: foreign });
    expect(legacy.usedTask).toEqual({ state: 'missing' });
    expect(legacy.currentTask).toBeUndefined();
    const retained = snapshot();
    const snapshotted = selectReaderEvidence({ cardKey, taskId, card: card({ source_snapshot: retained }), task: foreign });
    expect(snapshotted.usedTask).toEqual({ state: 'snapshot', record: retained.task });
    expect(snapshotted.currentTask).toBeUndefined();
  });
});

describe('evidence metadata', () => {
  it('shows owned current records without attributing any material when no explanation exists', () => {
    const currentTask = task();
    const ownEvent = event();
    const foreign = event({ id: 'foreign', item_id: 'another-task' });
    const selected = selectReaderEvidence({ cardKey, taskId, task: currentTask, currentEvents: [ownEvent, foreign] });
    expect(selected).toEqual({ cardKey, taskId, mode: 'none', invalidSnapshot: false, used: [], currentTask,
      current: [{ record: ownEvent, reason: 'new' }] });
    expect(evidenceDates(selected)).toEqual([]);
  });

  it('calculates dates from finite positive retained timestamps only, without borrowing current dates', () => {
    const records = [
      { id: 'late', item_id: taskId, ts: 130 }, { id: 'early', item_id: taskId, ts: 50 },
      { id: 'missing', item_id: taskId }, { id: 'string', item_id: taskId, ts: '40' },
      { id: 'infinite', item_id: taskId, ts: Number.POSITIVE_INFINITY }, { id: 'nan', item_id: taskId, ts: Number.NaN },
      { id: 'zero', item_id: taskId, ts: 0 }, { id: 'negative', item_id: taskId, ts: -20 },
    ];
    const selected = selectReaderEvidence({ cardKey, taskId,
      card: card({ source_snapshot: snapshot({ events: records, source_ids: records.map(record => record.id) }) }),
      currentEvents: [event({ id: 'new-source', ts: 1000 })] });
    expect(evidenceDates(selected)).toEqual([50, 130]);
  });

  it.each([
    [{ text: 'Excerpt', text_truncated: true }, true],
    [{ objective_truncated: true }, true],
    [{ text_truncated: false }, false],
    [{ text_truncated: 'true' }, false],
    [{ truncated: true }, false],
    [{ nested: { text_truncated: true } }, false],
    [{}, false],
  ])('recognizes only explicit top-level truncation flags in %j', (record, expected) => {
    expect(hasTruncatedFields(record as Record<string, unknown>)).toBe(expected);
  });
});
