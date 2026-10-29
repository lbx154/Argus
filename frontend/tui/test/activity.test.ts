import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  activityHistory,
  normalizeOperatorEvent,
  reduceOperatorEvent,
  runningActivities,
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




test('operator transcript, local echo, and wire event coalesce to one line', () => {
  let events = reduceOperatorEvent([], {
    type: 'ui.operator', text: '你好', ts: 10,
  });
  events = reduceOperatorEvent(events, {
    type: 'ui.operator', text: '你好', message_id: 'web-1', ts: 10.1,
  });
  events = reduceOperatorEvent(events, {
    type: 'ui.operator', text: '你好', ts: 10.2,
  });
  assert.equal(events.length, 1);
});

test('cold-start durable echo confirms one optimistic operator row in place', () => {
  const optimistic = {
    type: 'ui.operator',
    text: '你好',
    ts: 1,
    event_id: 'local-project-1-operator',
    message_id: 'local-1-operator',
    local_request_id: 1,
    local_optimistic: true,
  };
  let events = reduceOperatorEvent([optimistic], {
    type: 'ui.operator',
    text: '你好',
    ts: 10,
    message_id: 'web-10-operator',
  });

  assert.equal(events.length, 1);
  assert.equal(events[0].event_id, optimistic.event_id);
  assert.equal(events[0].message_id, optimistic.message_id);
  assert.equal(events[0].confirmed_message_id, 'web-10-operator');
  assert.equal(events[0].local_optimistic, false);

  events = reduceOperatorEvent(events, {
    type: 'ui.operator',
    text: '你好',
    ts: 11,
    message_id: 'web-10-operator',
  });
  assert.equal(events.length, 1, 'replay of the confirmed server row stays deduped');
});

test('rapid identical turns with distinct message ids are both preserved', () => {
  let events = reduceOperatorEvent([], {
    type: 'ui.operator', text: '再试一次', message_id: 'turn-1', ts: 10,
  });
  events = reduceOperatorEvent(events, {
    type: 'ui.operator', text: '再试一次', message_id: 'turn-2', ts: 11,
  });

  assert.equal(events.length, 2);
  assert.deepEqual(events.map((event) => event.message_id), ['turn-1', 'turn-2']);
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
  assert.ok(Math.abs((runningActivities(events)[0]?.startedTs ?? 0) - start) < 0.002);
  assert.equal(runningActivities(events)[0]?.model, 'gpt-5.5');
});

test('a missed completion cannot leave a permanent running activity', () => {
  const old = Date.now() / 1000 - 121;
  const events = reduceOperatorEvent([], {
    type: 'agent.io.stream', call_id: `${Math.floor(old * 1000)}-9`,
    run_label: 'skill.compaction_batch', ts: old, line: '{}',
  });
  assert.deepEqual(runningActivities(events), []);
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
  assert.deepEqual(runningActivities(events), []);
});
