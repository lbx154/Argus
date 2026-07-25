import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  appendPhaseStep,
  closePhaseTrail,
  formatStepSeconds,
  stepElapsedS,
  summarizeTrail,
  visibleTrail,
  type PhaseStep,
} from '../../core/src/phaseTrail.js';

const build = (
  fragments: Array<[string, number] | [string, number, Record<string, unknown>]>,
): PhaseStep[] => fragments.reduce<PhaseStep[]>(
  (steps, [label, ts, extra]) => appendPhaseStep(steps, { label, ...(extra ?? {}) }, ts),
  [],
);

test('appendPhaseStep keeps every distinct step and closes the previous one', () => {
  const steps = build([
    ['$ rg cockpit src', 100],
    ['⚙ view · src/App.tsx', 104],
    ['$ pytest tests/x.py', 110],
  ]);
  assert.equal(steps.length, 3);
  assert.deepEqual(steps.map((s) => s.label), [
    '$ rg cockpit src',
    '⚙ view · src/App.tsx',
    '$ pytest tests/x.py',
  ]);
  // Earlier steps are closed with a real duration; only the newest stays open.
  assert.equal(steps[0].endedTs, 104);
  assert.equal(steps[1].endedTs, 110);
  assert.equal(steps[2].endedTs, 0);
  assert.equal(stepElapsedS(steps[0]), 4);
});

test('appendPhaseStep ignores empty labels and refreshes a repeated one in place', () => {
  let steps = appendPhaseStep([], { label: '   ' }, 10);
  assert.deepEqual(steps, []);
  steps = appendPhaseStep(steps, { label: '$ pytest' }, 10);
  steps = appendPhaseStep(steps, { label: '$ pytest…' }, 12);
  assert.equal(steps.length, 1, 'a repeat must not stack a duplicate row');
  assert.equal(steps[0].startedTs, 10);
});

test('heartbeats replace each other rather than flooding the trail', () => {
  let steps = appendPhaseStep([], { label: '$ long build' }, 0);
  steps = appendPhaseStep(steps, { label: 'Manager alive · 5s quiet', heartbeat: true }, 5);
  steps = appendPhaseStep(steps, { label: 'Manager alive · 10s quiet', heartbeat: true }, 10);
  steps = appendPhaseStep(steps, { label: 'Manager alive · 15s quiet', heartbeat: true }, 15);
  assert.equal(steps.length, 2);
  assert.equal(steps[1].label, 'Manager alive · 15s quiet');
  assert.equal(steps[1].heartbeat, true);
});

test('phase detail and kind ride along for the expanded view', () => {
  const steps = appendPhaseStep([], {
    label: '$ cd /x && pytest',
    kind: 'command_execution',
    detail: 'cd /x && pytest -q tests/a.py',
    role: 'manager',
  }, 1);
  assert.equal(steps[0].kind, 'command_execution');
  assert.equal(steps[0].detail, 'cd /x && pytest -q tests/a.py');
  assert.equal(steps[0].role, 'manager');
});

test('closePhaseTrail ends the open step exactly once', () => {
  const steps = closePhaseTrail(build([['$ a', 0], ['$ b', 3]]), 9);
  assert.equal(steps[1].endedTs, 9);
  assert.equal(closePhaseTrail(steps, 20)[1].endedTs, 9, 'already-closed steps stay put');
  assert.deepEqual(closePhaseTrail([], 5), []);
});

test('visibleTrail keeps the newest window', () => {
  const steps = build([['a', 1], ['b', 2], ['c', 3], ['d', 4]]);
  assert.deepEqual(visibleTrail(steps, 2).map((s) => s.label), ['c', 'd']);
  assert.deepEqual(visibleTrail(steps, 99).map((s) => s.label), ['a', 'b', 'c', 'd']);
});

test('formatStepSeconds hides sub-second noise and reads minutes', () => {
  assert.equal(formatStepSeconds(0.4), '');
  assert.equal(formatStepSeconds(7), '7s');
  assert.equal(formatStepSeconds(65), '1m5s');
  assert.equal(formatStepSeconds(120), '2m');
});

test('summarizeTrail records real work and drops waiting rows', () => {
  let steps = build([['$ rg cockpit', 0], ['⚙ view · a.ts', 4]]);
  steps = appendPhaseStep(steps, { label: 'Manager alive · 5s quiet', heartbeat: true }, 9);
  const summary = summarizeTrail(closePhaseTrail(steps, 11));
  assert.match(summary, /^did 2 steps:/);
  assert.match(summary, /\$ rg cockpit · 4s/);
  assert.match(summary, /⚙ view · a\.ts · 5s/);
  assert.doesNotMatch(summary, /quiet/, 'heartbeats are waiting, not work');
});

test('summarizeTrail returns nothing when there was nothing to show', () => {
  assert.equal(summarizeTrail([]), '');
  assert.equal(
    summarizeTrail(appendPhaseStep([], { label: 'alive · 5s quiet', heartbeat: true }, 1)),
    '',
  );
});
