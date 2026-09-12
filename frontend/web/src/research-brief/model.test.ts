import { describe, expect, it } from 'vitest';
import { splitDraft } from '../map/presentation';
import {
  briefCopyKey, briefEvidence, briefInputSignature, briefLiveKey, briefRequest, briefSelection,
  currentBriefData, isReaderBrief, needsBrief, oldBriefService, questionAboutStep,
} from './model';
import { completedCopy, inputs, readerBrief, source } from './testFixtures';

describe('one task’s explanatory evidence', () => {
  it('binds current project, task and attempt, and ignores old or unrelated reviews and tool updates', () => {
    const props = inputs();
    const selection = briefSelection(props.snapshot, props.view);
    expect(selection).toEqual({ mode: 'current', since: 2, eventSince: 2, taskId: 'a' });
    const current = currentBriefData(source, props.sid, 'a');
    const events = briefEvidence(current, current.tasks[0], selection.eventSince);
    expect(current.tasks.map(task => task.id)).toEqual(['a']);
    expect(events.map(event => event.id)).toEqual(['start-a', 'main-a']);
    expect(briefRequest(current.tasks[0], events)).toEqual({ key: 'a', task_id: 'a', kind: 'task', event_ids: ['start-a', 'main-a'] });
    expect(briefCopyKey(props.sid, 'en-US')).toEqual(['map-copy', 'project', 's-research', 'en-US', 's-research']);
    expect(briefLiveKey(props.sid, selection)).not.toEqual(briefLiveKey(props.sid, { ...selection, taskId: 'b' }));
    expect(() => currentBriefData(source, 's-other', 'a')).toThrow(/match/);
  });

  it('only changes the semantic request on real task or milestone changes', () => {
    const task = source.tasks[0];
    const events = briefEvidence(source, task, 2);
    const before = briefInputSignature(task, events);
    const toolChanged = { ...source, cursor: 'cursor-2', events: source.events.map(event => event.id === 'tools-a' ? { ...event, revision: 'tool2', text: 'Read another file' } : event) };
    expect(briefInputSignature({ ...task, revision: 'unrelated-collection-revision' }, briefEvidence(toolChanged, task, 2))).toBe(before);
    const reviewed = { ...source, events: [...source.events, { id: 'review-a', item_id: 'a', type: 'round.review.completed', ts: 6, text: 'Conditions accepted', status: 'done' }] };
    expect(briefInputSignature(task, briefEvidence(reviewed, task, 2))).not.toBe(before);
    expect(briefInputSignature({ ...task, outcome: { review_status: 'skipped' } }, events)).not.toBe(before);
  });

  it('reuses compatible map copy and upgrades missing briefs without treating legacy text as one', () => {
    const task = source.tasks[0], evidence = briefEvidence(source, task, 2);
    const cached = completedCopy(task, evidence.map(event => event.id));
    expect(needsBrief(source, task, evidence, cached)).toBe(false);
    const legacy = { ...cached, version: 9, cards: { a: { ...cached.cards.a, reader_brief: undefined, version: 9 } } };
    expect(needsBrief(source, task, evidence, legacy)).toBe(true);
    expect(oldBriefService(legacy, legacy.cards.a)).toBe(true);
    expect(oldBriefService({ ...legacy, version: 10 }, legacy.cards.a)).toBe(false);
    expect(isReaderBrief(readerBrief)).toBe(true);
    expect(isReaderBrief({ ...readerBrief, concept: {} })).toBe(false);
  });

  it('makes an editable question with exact existing references rather than sending it', () => {
    const events = briefEvidence(source, source.tasks[0], 2);
    const draft = questionAboutStep('s-research', source.tasks[0], events, true);
    const parsed = splitDraft(draft);
    expect(parsed.refs).toEqual([{ source: 'live:s-research', task_id: 'a', task_title: source.tasks[0].title,
      event_ids: ['start-a', 'main-a'], lang: 'zh' }]);
    expect(parsed.text).toContain('还有哪些条件或缺口');
  });
});
