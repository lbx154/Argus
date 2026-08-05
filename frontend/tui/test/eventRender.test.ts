import assert from 'node:assert/strict';
import { test } from 'node:test';

import { renderEvent } from '../src/eventRender.js';
import type { EventMsg } from '../src/api.js';

test('renderEvent reports truthful terminal mission outcomes for new and legacy events', () => {
  assert.deepEqual(
    renderEvent({
      type: 'life.mission.completed',
      status: 'done',
      success: true,
    } as EventMsg),
    {
      role: 'engineer',
      label: 'Engineer',
      glyph: '🎉',
      text: 'Task completed',
      tone: 'ok',
      rule: true,
    },
  );

  assert.deepEqual(
    renderEvent({
      type: 'life.mission.completed',
      status: 'done',
      success: true,
      final_submission_certified: true,
    } as EventMsg),
    {
      role: 'engineer',
      label: 'Engineer',
      glyph: '🎉',
      text: 'Submission certified',
      tone: 'ok',
      rule: true,
    },
  );

  assert.deepEqual(
    renderEvent({
      type: 'life.mission.completed',
      status: 'research_incomplete',
      success: false,
    } as EventMsg),
    {
      role: 'engineer',
      label: 'Engineer',
      glyph: '◌',
      text: 'Mission incomplete',
      tone: 'warn',
      rule: true,
    },
  );

  assert.deepEqual(
    renderEvent({
      type: 'life.mission.completed',
      outcome_class: 'blocked',
      status: 'done',
      success: true,
    } as EventMsg),
    {
      role: 'engineer',
      label: 'Engineer',
      glyph: '⛔',
      text: 'Mission blocked',
      tone: 'err',
      rule: true,
    },
  );

  assert.deepEqual(
    renderEvent({
      type: 'life.mission.completed',
      status: 'legacy_weird_status',
      success: false,
    } as EventMsg),
    {
      role: 'engineer',
      label: 'Engineer',
      glyph: '■',
      text: 'Mission ended · legacy_weird_status',
      tone: 'info',
      rule: true,
    },
  );
});
