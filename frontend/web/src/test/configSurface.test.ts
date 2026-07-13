import { describe, expect, it } from 'vitest';
import type { ConfigKnob } from '../api';
import {
  compactConfigSource,
  conciseConfigKnobs,
  connectionTopology,
} from '../lib/configSurface';

const knob = (name: string, group = 'internal'): ConfigKnob => ({
  name,
  group,
  value: 'value',
  source: 'default',
  default: 'default',
  doc: 'raw description',
});

describe('conciseConfigKnobs', () => {
  it('keeps only essential controls already not represented by roles or budgets', () => {
    const result = conciseConfigKnobs([
      knob('ARGUS_SKILL_ENGINEER_BACKEND', 'backend'),
      knob('ARGUS_SKILL_ENGINEER_MODEL', 'models'),
      knob('ARGUS_SKILL_PER_MISSION_CAP_USD', 'budget'),
      knob('ARGUS_SKILL_RUNNER_BIN', 'backend'),
      knob('ARGUS_SKILL_TELEGRAM_BOT_TOKEN', 'telemetry'),
      knob('ARGUS_SKILL_CONTROL_PLANE_CALL_CAP_USD', 'budget'),
      knob('ARGUS_SKILL_MAX_ACTIVE_DAEMONS', 'budget'),
      knob('ARGUS_SKILL_UNPRICED_COST_POLICY', 'budget'),
      knob('ARGUS_SKILL_SAFE_MODE', 'lifecycle'),
      knob('ARGUS_SKILL_ENABLE_TELEGRAM', 'telemetry'),
      knob('ARGUS_SKILL_SHOW_REASONING', 'telemetry'),
    ]);

    expect(result.map((item) => item.name)).toEqual([
      'ARGUS_SKILL_CONTROL_PLANE_CALL_CAP_USD',
      'ARGUS_SKILL_MAX_ACTIVE_DAEMONS',
      'ARGUS_SKILL_UNPRICED_COST_POLICY',
      'ARGUS_SKILL_SAFE_MODE',
      'ARGUS_SKILL_ENABLE_TELEGRAM',
      'ARGUS_SKILL_SHOW_REASONING',
    ]);
    expect(result.map((item) => item.group)).toEqual([
      'Limits',
      'Limits',
      'Safety',
      'Safety',
      'Interface',
      'Interface',
    ]);
  });
});
describe('compactConfigSource', () => {
  it('shortens verbose resolver provenance', () => {
    expect(compactConfigSource('persisted:ARGUS_SKILL_MODEL')).toBe('model · persisted');
    expect(compactConfigSource('ARGUS_SKILL_MANAGER_MODEL')).toBe('manager model · env');
    expect(compactConfigSource('default: xhigh')).toBe('default');
    expect(compactConfigSource('capability vault / default: gpt-5.5')).toBe('vault / default');
  });
});

describe('connectionTopology', () => {
  it('shows the browser-facing API and project stream ports', () => {
    expect(connectionTopology('http://127.0.0.1:8799', 's-a/b')).toEqual({
      webApi: 'http://127.0.0.1:8799/api',
      eventStream: 'ws://127.0.0.1:8799/api/projects/s-a%2Fb/stream',
      daemon: 'local process · events.jsonl · no TCP port',
    });
    expect(connectionTopology('https://argus.example', 's-1').eventStream).toBe(
      'wss://argus.example/api/projects/s-1/stream',
    );
  });
});
