import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  activityHistory,
  latestRunningActivity,
  normalizeOperatorEvent,
  overlayActiveRole,
  overlayRoleActivities,
  reduceOperatorEvent,
} from '../../core/src/activity.js';

test('raw agent IO becomes a safe semantic activity without prompt or output', () => {
  const event = normalizeOperatorEvent({
    type: 'agent.io.start',
    call_id: '1700000000000-7',
    run_label: 'idea-search',
    model: 'gpt-5.5',
    backend: 'copilot',
    prompt: 'SECRET FULL PROMPT',
    stdout_lines: ['SECRET OUTPUT'],
    ts: 1700000000,
  });
  assert.equal(event?.type, 'role.activity');
  assert.equal(event?.label, 'searching recent papers + generating candidate ideas');
  assert.equal(event?.status, 'running');
  assert.doesNotMatch(JSON.stringify(event), /SECRET/);
});

test('live activities immediately override stale idle role snapshots', () => {
  const now = Date.now() / 1000;
  const callId = `${Math.floor(now * 1000)}-7`;
  const roles = [{
    role: 'engineer', backend: 'copilot', backend_label: 'Copilot', model: 'gpt-5.5',
    effort: 'xhigh', active: false, label: 'idle', status: 'idle', age_s: null,
  }];
  const events = reduceOperatorEvent([], {
    type: 'agent.io.stream', call_id: callId, run_label: 'engineer-r4',
    backend: 'copilot', ts: now, line: '{}',
  });
  const overlaid = overlayRoleActivities(roles, events);
  assert.equal(overlaid[0].active, true);
  assert.equal(overlaid[0].label, 'working on the mission · round 4');
});

test('UI-local Manager work overrides an idle snapshot without journaling', () => {
  const roles = [{
    role: 'manager', backend: 'copilot', backend_label: 'Copilot', model: 'gpt-5.6-sol',
    effort: 'xhigh', active: false, label: 'idle', status: 'idle', age_s: 300,
  }];
  const overlaid = overlayActiveRole(roles, 'manager', 'classifying this message', 2.4);
  assert.equal(overlaid[0].active, true);
  assert.equal(overlaid[0].label, 'classifying this message');
  assert.equal(overlaid[0].status, 'running');
  assert.equal(overlaid[0].age_s, 2.4);
  assert.equal(roles[0].active, false);
});

test('UI-local activity adds a role before the first snapshot arrives', () => {
  const overlaid = overlayActiveRole([], 'manager', 'handling your message');
  assert.deepEqual(overlaid, [{
    role: 'manager',
    backend: '',
    backend_label: '',
    model: '',
    effort: null,
    active: true,
    label: 'handling your message',
    status: 'running',
    age_s: 0,
  }]);
});

test('stream protocol is coalesced by call id and rate-limited to one heartbeat per second', () => {
  const start = Date.now() / 1000 - 2;
  const callId = `${Math.floor(start * 1000)}-7`;
  let events = reduceOperatorEvent([], {
    type: 'agent.io.start', call_id: callId, run_label: 'idea-search', ts: start,
  });
  events = reduceOperatorEvent(events, {
    type: 'agent.io.stream', call_id: callId, run_label: 'idea-search',
    line: 'SECRET TOKEN DELTA', ts: start + 0.2,
  });
  assert.equal(events.length, 1);
  assert.equal(events[0].ts, start);
  events = reduceOperatorEvent(events, {
    type: 'agent.io.stream', call_id: callId, run_label: 'idea-search',
    line: '{"type":"session.tools_updated","data":{"model":"gpt-5.5"}}', ts: start + 1.2,
  });
  assert.equal(events.length, 1);
  assert.equal(events[0].ts, start + 1.2);
  assert.doesNotMatch(JSON.stringify(events), /SECRET/);
  assert.ok(Math.abs((latestRunningActivity(events)?.startedTs ?? 0) - start) < 0.002);
  assert.equal(latestRunningActivity(events)?.model, 'gpt-5.5');
});

test('a missed completion cannot leave a permanent running activity', () => {
  const old = Date.now() / 1000 - 121;
  const events = reduceOperatorEvent([], {
    type: 'agent.io.stream', call_id: `${Math.floor(old * 1000)}-9`,
    run_label: 'skill.compaction_batch', ts: old, line: '{}',
  });
  assert.equal(latestRunningActivity(events), null);
  assert.equal(activityHistory(events)[0].role, 'maintenance');
});

test('matcher and idea-search phases settle in place as concise milestones', () => {
  let events = reduceOperatorEvent([], {
    type: 'match.info', text: 'querying matcher (gpt-5.5) against 30 candidates: noisy list', ts: 10,
  });
  events = reduceOperatorEvent(events, {
    type: 'match.info', text: 'matcher picked: emnlp-paper-writing-playbook  (0 tok)', ts: 22,
  });
  events = reduceOperatorEvent(events, { type: 'idea.search.started', ts: 23 });
  events = reduceOperatorEvent(events, { type: 'idea.search.completed', count: 6, ts: 83 });
  assert.equal(events.length, 2);
  const history = activityHistory(events);
  assert.deepEqual(history.map((row) => row.label), [
    'selected skill · emnlp-paper-writing-playbook',
    'generated 6 candidate ideas',
  ]);
  assert.equal(history[1].elapsedS, 60);
  assert.equal(latestRunningActivity(events), null);
});
