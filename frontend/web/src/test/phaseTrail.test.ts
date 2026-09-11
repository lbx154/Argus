import { describe, expect, it } from 'vitest';

import {
  appendPhaseStep,
  closePhaseTrail,
  trailToTurnSteps,
  turnStepsElapsedS,
  turnStepsFrom,
  type PhaseStep,
} from '../../../core/src/phaseTrail';

const call = (label: string, callId: string, extra: Record<string, string> = {}) => ({
  label, kind: 'command_execution', callId, status: 'running', tool: `Tool ${callId}`, ...extra,
});

describe('phase trail with tool call ids', () => {
  it('pairs a result with the call that started it instead of adding a row', () => {
    let trail: PhaseStep[] = [];
    trail = appendPhaseStep(trail, call('$ wc -l README.md', 'c1'), 10);
    trail = appendPhaseStep(trail, {
      label: '↳ Tool c1 · completed', kind: 'tool_result', callId: 'c1', status: 'completed', output: '1 README.md',
    }, 12);

    expect(trail).toHaveLength(1);
    expect(trail[0]).toMatchObject({
      label: '$ wc -l README.md', status: 'completed', endedTs: 12, output: '1 README.md', tool: 'Tool c1',
    });
  });

  it('keeps two calls open side by side until each result arrives', () => {
    let trail: PhaseStep[] = [];
    trail = appendPhaseStep(trail, call('$ one', 'c1'), 1);
    trail = appendPhaseStep(trail, call('$ two', 'c2'), 1.1);
    expect(trail.map((step) => step.endedTs)).toEqual([0, 0]);

    trail = appendPhaseStep(trail, { label: 'done', kind: 'tool_result', callId: 'c2', status: 'failed' }, 3);
    expect(trail.map((step) => [step.status, step.endedTs])).toEqual([['running', 0], ['failed', 3]]);
  });

  it('drops a result for a call it never saw and still ends anonymous steps', () => {
    let trail: PhaseStep[] = [];
    trail = appendPhaseStep(trail, { label: 'Deciding…' }, 1);
    trail = appendPhaseStep(trail, { label: 'orphan', kind: 'tool_result', callId: 'ghost', status: 'completed' }, 2);
    trail = appendPhaseStep(trail, { label: '⚙ view', kind: 'tool_use' }, 3);

    expect(trail.map((step) => step.label)).toEqual(['Deciding…', '⚙ view']);
    expect(trail[0].endedTs).toBe(3);
  });

  it('closes every open step when the turn ends', () => {
    let trail: PhaseStep[] = [];
    trail = appendPhaseStep(trail, call('$ one', 'c1'), 1);
    trail = appendPhaseStep(trail, call('$ two', 'c2'), 2);
    expect(closePhaseTrail(trail, 9).map((step) => step.endedTs)).toEqual([9, 9]);
  });
});

describe('turn steps', () => {
  it('keeps only tool work, in the journaled shape', () => {
    let trail: PhaseStep[] = [];
    trail = appendPhaseStep(trail, { label: 'Copilot handling it solo…' }, 1);
    trail = appendPhaseStep(trail, call('$ ls', 'c1', { toolKind: 'execute' }), 2);
    trail = appendPhaseStep(trail, { label: '⚙ view: {"path": "a"}', kind: 'tool_use' }, 4);

    expect(trailToTurnSteps(trail, 6)).toEqual([
      {
        kind: 'command_execution', label: '$ ls', tool: 'Tool c1', tool_kind: 'execute', call_id: 'c1',
        status: 'running', started_ts: 2, ended_ts: 0,
      },
      { kind: 'tool_use', label: '⚙ view: {"path": "a"}', status: 'running', started_ts: 4, ended_ts: 0 },
    ]);
    expect(trailToTurnSteps(trail, 6, true).map((step) => [step.status, step.ended_ts])).toEqual([
      ['completed', 6], ['completed', 6],
    ]);
  });

  it('makes server steps safe to render and measures their span', () => {
    expect(turnStepsFrom(undefined)).toEqual([]);
    expect(turnStepsFrom([null, { kind: 'tool_use' }, 'x'])).toEqual([]);
    const steps = turnStepsFrom([
      { kind: 'command_execution', label: '$ ls', status: 'COMPLETED', started_ts: 10, ended_ts: 12.5, output: 'a b' },
      { label: 'view', started_ts: 11, ended_ts: 'soon' },
    ]);
    expect(steps).toEqual([
      { kind: 'command_execution', label: '$ ls', status: 'completed', started_ts: 10, ended_ts: 12.5, output: 'a b' },
      { kind: 'tool_use', label: 'view', status: 'completed', started_ts: 11, ended_ts: 0 },
    ]);
    expect(turnStepsElapsedS(steps, 20)).toBe(10);
  });
});
