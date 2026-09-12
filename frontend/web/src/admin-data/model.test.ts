import { describe, expect, it } from 'vitest';
import {
  collectionLabel, episodeRole, eventView, mergeObservationPages, normalizeRole,
  projectIdentityLabel, projectKey, projectName, publicMessagesText, publicText,
  qualityLabel, selectTaskFromLink, taskKey,
} from './model';
import type { CollaborationTask, ObservedEpisode, ObservedEvent, ObservedPage } from './types';

function event(sequence: number, kind = 'message_delta', payload = { type: 'text_delta', delta: 'same' }): ObservedEvent {
  return { id: `observation-${sequence}`, sequence, kind, observed_at: 100 + sequence, payload };
}

function episode(overrides: Partial<ObservedEpisode> = {}): ObservedEpisode {
  return {
    episode_id: 1, tenant_id: 'trial-01', sid: 's-shared', task_id: null,
    role: 'planner', label: '规划', session_id: 'pi-1', runtime: { run_label: 'planner' },
    capture_policy: 'observed_public_v2', state: 'capturing', started_at: 100, updated_at: 110,
    collection: { event_count: 10, event_counts: { message_delta: 10 }, complete: false, issues: [], historical_data_unavailable: false },
    quality: { state: 'not_evaluated', approved: false },
    tool_pairs: [], tool_pairs_total: 0, tool_pairs_truncated: false, events: [],
    ...overrides,
  };
}

function page(episodes: ObservedEpisode[], overrides: Partial<ObservedPage> = {}): ObservedPage {
  return {
    tenant_id: 'trial-01', sid: 's-shared', task_id: null, purpose: 'internal_training',
    episodes, global_complete: false,
    pagination: { has_more: true, next_cursor: '1:2', returned_events: 2, limit: 2 },
    scope: 'received_public_observations', quality_status: 'not_automatically_approved', ...overrides,
  };
}

function task(overrides: Partial<CollaborationTask> = {}): CollaborationTask {
  return {
    tenant_id: 'trial-01', sid: 's-shared', task_id: 'mission-1', id: 'task-hash',
    title: '同名研究任务', mission_title: null, objective: null, request: null, mission_brief_source: null,
    roles: [], task_outcome: { state: 'unknown', label: '任务结果未确认', evidence_event_ids: [], last_lifecycle: null },
    collection: { states: {}, accepted_episodes: 0, quarantined_episodes: 0, gaps: 0, retained_episodes: 0, observed_events: 0 },
    quality: { approved_samples: 0, candidates: 0 }, last_observed_at: 100, unassigned_observations: 0,
    global_complete: false, ...overrides,
  };
}

describe('retained observation pagination', () => {
  it('merges a split episode by sequence and retains equal public deltas at different sequences', () => {
    const first = page([episode({ events: [event(0), event(1)] })]);
    const second = page([episode({ updated_at: 120, events: [event(1), event(2)] }),
      episode({ episode_id: 2, role: 'engineer', task_id: 'mission-1', events: [event(0)] })]);
    const merged = mergeObservationPages([first, second]);
    expect(merged).toHaveLength(2);
    expect(merged[0].events.map((item) => item.sequence)).toEqual([0, 1, 2]);
    expect(merged[0].events.map((item) => eventView(item).text)).toEqual(['same', 'same', 'same']);
    expect(merged[0].updated_at).toBe(120);
    expect(merged[1].events).toHaveLength(1);
    expect(first.episodes[0].events).toHaveLength(2);
    expect(second.episodes[0].events).toHaveLength(2);
  });

  it('keeps project-level roles alongside task-associated episodes', () => {
    const merged = mergeObservationPages([page([
      episode({ role: 'manager', task_id: null }),
      episode({ episode_id: 2, role: 'planner', task_id: null }),
      episode({ episode_id: 3, role: 'engineer', task_id: 'mission-1' }),
    ])]);
    expect(merged.map((item) => [item.role, item.task_id])).toEqual([
      ['manager', null], ['planner', null], ['engineer', 'mission-1'],
    ]);
  });

  it('does not confuse loading the last page with capture completion or training approval', () => {
    const source = page([episode({ events: [event(0)] })], {
      pagination: { has_more: false, next_cursor: null, returned_events: 1, limit: 200 },
    });
    const merged = mergeObservationPages([source])[0];
    expect(merged.collection.complete).toBe(false);
    expect(merged.collection.event_count).toBe(10);
    expect(merged.quality.approved).toBe(false);
    expect(source.global_complete).toBe(false);
    expect(collectionLabel(merged)).toBe('采集中');
    expect(qualityLabel(merged)).toBe('质量尚未验收');
    expect(collectionLabel(episode({ state: 'complete', collection: { ...merged.collection, complete: true } })))
      .toBe('本段采集已结束');
  });

  it('rejects cross-tenant, cross-purpose, or cross-task page merges', () => {
    const first = page([episode()]);
    for (const changed of [
      { tenant_id: 'trial-02' }, { sid: 's-other' }, { purpose: 'external_sharing' as const }, { task_id: 'mission-1' },
    ]) expect(() => mergeObservationPages([first, page([], changed)])).toThrow(/different/);
    expect(() => mergeObservationPages([page([episode({ tenant_id: 'trial-02' })])])).toThrow(/scope/);
    expect(() => mergeObservationPages([page([episode()], { task_id: 'mission-1' })])).toThrow(/scope/);
  });

  it('retains explicit recovery limits and handles an empty first page', () => {
    const recovered = episode({
      state: 'interrupted', runtime: { recovery: { provider_requests_available: false, tool_schemas_available: false } },
      events: [event(0, 'session_message', { type: 'text_delta', delta: 'old message' })],
    });
    const merged = mergeObservationPages([page([]), page([recovered])])[0];
    expect(merged.runtime.recovery).toEqual(recovered.runtime.recovery);
    expect(collectionLabel(merged)).toBe('历史会话已恢复');
    expect(merged.collection.complete).toBe(false);
    expect(mergeObservationPages([])).toEqual([]);
  });
});

describe('project and task identity', () => {
  it('uses tenant and SID, with a separate subtitle for identically named projects', () => {
    const one = { title: '同名项目', tenant_id: 'trial-01', sid: 's-shared' };
    const two = { ...one, tenant_id: 'trial-02' };
    expect(projectName(one)).toBe(projectName(two));
    expect(projectKey(one)).not.toBe(projectKey(two));
    expect(projectIdentityLabel(one)).toBe('trial-01 / s-shared');
    expect(projectIdentityLabel(two)).toBe('trial-02 / s-shared');
    expect(taskKey(task())).not.toBe(taskKey(task({ tenant_id: 'trial-02' })));
  });

  it('selects the explicit tenant, SID, and task even when another same-name task is newer', () => {
    const expected = task();
    const newer = task({ tenant_id: 'trial-02', id: 'newer', last_observed_at: 200 });
    const tasks = [newer, expected, task({ sid: 's-else', last_observed_at: 300 })];
    expect(selectTaskFromLink(tasks, '?tenant=trial-01&sid=s-shared&task_id=mission-1')).toBe(expected);
    expect(selectTaskFromLink(tasks, { tenant: 'trial-01', sid: 's-shared' })).toBe(expected);
    expect(selectTaskFromLink(tasks, '?sid=s-shared')).toBeNull();
    expect(selectTaskFromLink(tasks, '?tenant=trial-01&sid=s-shared&task_id=missing')).toBeNull();
  });

  it('can explicitly select project activity and does not silently switch a missing project', () => {
    const activity = task({ task_id: null });
    expect(selectTaskFromLink([task(), activity], '?tenant=trial-01&sid=s-shared&task_id=')).toBe(activity);
    expect(selectTaskFromLink([task()], '?tenant=trial-02&sid=s-missing')).toBeNull();
  });
});

describe('roles and public event presentation', () => {
  it('does not guess old unknown roles from prose, translated labels, or another role hint', () => {
    expect(episodeRole({ role: 'unknown', runtime: { run_label: 'engineer' } })).toBe('unknown');
    expect(episodeRole({ runtime: {} })).toBe('unknown');
    expect(normalizeRole('Please act as a reviewer')).toBe('unknown');
    expect(normalizeRole('执行')).toBe('unknown');
    expect(episodeRole({ runtime: { run_label: 'planner.plan_next' } })).toBe('planner');
  });

  it('preserves application instructions and literal reasoning words, excluding private structured content', () => {
    const text = publicMessagesText([
      { role: 'system', content: 'Application system instructions' },
      { role: 'developer', content: 'Application developer instructions' },
      { role: 'user', content: 'Analyze the variable named thinking.' },
      { role: 'assistant', thinking: 'private-field', reasoning_content: 'private-reason', content: [
        { type: 'thinking', thinking: 'private-block', text: 'private-block' },
        { type: 'reasoning', text: 'private-reasoning' },
        { type: 'signature', text: 'private-signature' },
        { type: 'text', text: 'Public result' },
      ] },
      { role: 'assistant', channel: 'analysis', content: 'private-channel' },
      { role: 'analysis', content: 'private-role' },
    ]);
    expect(text).toContain('Application system instructions');
    expect(text).toContain('Application developer instructions');
    expect(text).toContain('Analyze the variable named thinking.');
    expect(text).toContain('Public result');
    expect(text).not.toContain('private-');
    expect(publicText({ type: 'thinking', text: 'hidden' })).toBe('');
    expect(publicText({ type: 'unknown', text: 'not declared public text' })).toBe('');
  });

  it('keeps tool arguments as public data and renders the two observed call formats', () => {
    expect(publicText({ type: 'toolCall', name: 'read', arguments: { path: 'analysis.txt' } })).toContain('analysis.txt');
    expect(publicMessagesText([{ role: 'assistant', tool_calls: [
      { id: 'call-1', function: { name: 'write', arguments: { analysis: 'public file data' } } },
    ] }])).toContain('public file data');
    expect(publicText([{ type: 'text', text: 'first' }, { type: 'text', text: 'second' }])).toBe('first\nsecond');
  });

  it('renders an assistant message containing only tool calls without a content field', () => {
    const message = { role: 'assistant', tool_calls: [
      { id: 'call-1', type: 'function', function: { name: 'read', arguments: { path: 'results.csv', limit: 20 } } },
    ] };
    expect(publicText(message)).toBe('read\n{\n  "path": "results.csv",\n  "limit": 20\n}');
  });

  it('renders only public deltas and never turns an absent result flag into success', () => {
    const call: ObservedEvent = { sequence: 1, kind: 'tool_call', payload: { toolCallId: 'c1', toolName: 'bash', input: { command: 'pytest' } } };
    const result: ObservedEvent = { sequence: 2, kind: 'tool_result', payload: { toolCallId: 'c1', content: [{ type: 'text', text: 'output' }] } };
    expect(eventView(call)).toMatchObject({ result: null, toolName: 'bash', timestamp: null });
    expect(eventView(result)).toMatchObject({ result: 'unknown', text: 'output' });
    expect(eventView({ ...result, payload: { ...result.payload, isError: false } }).result).toBe('success');
    expect(eventView({ ...result, payload: { ...result.payload, isError: true } }).result).toBe('error');
    expect(eventView(event(3, 'message_delta', { type: 'thinking_delta', delta: 'private' })).text).toBe('');
    expect(eventView(event(4, 'message_delta', { type: 'toolcall_delta', delta: '{"path":' })).text).toBe('{"path":');
    expect(eventView({ sequence: 5, kind: 'settled', payload: {} }).result).toBeNull();
  });
});
