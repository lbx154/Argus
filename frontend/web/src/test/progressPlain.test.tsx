import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { EventMsg } from '../api';
import { emptyMissionView } from '../../../core/src/missionView';
import type { MissionRoleWorkItem, Role } from '../../../core/src/types';
import { AgentActivity } from '../components/AgentActivity';
import { liveStaleness } from '../components/agentActivityModel';
import { stepText } from '../components/TurnSteps';
import { plainToolLabel, readableGlob } from '../lib/feedSteps';

const tool = (text: string, tool_name?: string) => ({ type: 'engineer.progress', kind: 'tool_use', text, ...(tool_name ? { tool_name } : {}) }) as unknown as EventMsg;

describe('progress in plain words', () => {
  it('turn steps lead with the plain label and fold the raw arguments', () => {
    const step = { kind: 'tool_use', label: '查阅 main.py', tool: 'view', detail: 'view: {"path": "/abs/proj/main.py"}', status: 'completed', started_ts: 1, ended_ts: 2 };
    expect(stepText(step)).toEqual({ primary: '查阅 main.py', secondary: 'view: {"path": "/abs/proj/main.py"}' });
    expect(stepText({ ...step, kind: 'command_execution', label: '$ pytest -q', tool: 'Run tests', detail: '' }).primary).toBe('pytest -q');
  });

  it('turn steps saved before plain labels existed are relabelled when shown', () => {
    // Stored shape of an older turn: glyph + tool + clipped JSON, full args in detail.
    const detail = '{"cells": null, "includeOutputs": null, "limit": 100, "path": "/data/proj/README.md"}';
    const old = { kind: 'tool_use', label: '⚙ read · {"cells": null, "includeOutputs": null, "limit"…', tool: 'read', detail, status: 'completed', started_ts: 1, ended_ts: 2 };
    const { primary, secondary } = stepText(old, 'zh-CN');
    expect(primary).toBe('查阅 README.md');
    expect(secondary).toBe(detail);
    const view = stepText({ ...old, label: '⚙ view · {"path": "/data/proj/src"}', tool: 'view', detail: '{"path": "/data/proj/src"}' }, 'en');
    expect(view.primary).toBe('Read src');
  });

  it('feed tool rows read as verb + object without JSON or absolute paths', () => {
    const cases: Array<[EventMsg, string]> = [
      [tool('view: {"path": "/data/proj/work/RESEARCH_NOTES.md"}', 'view'), '查阅 RESEARCH_NOTES.md'],
      [tool('glob: {"pattern": "**/*"}', 'glob'), '查找 所有文件'],
      [tool('glob: {"pattern": "src/**/*.ts"}', 'glob'), '查找 src 里的 .ts 文件'],
      [tool('rg: {"pattern": "route", "paths": "/data/proj/a.md"}', 'rg'), '搜索：route'],
      [tool('mystery: {"x": 1}', 'mystery'), '调用 mystery'],
    ];
    for (const [ev, label] of cases) {
      const said = plainToolLabel(ev, 'zh-CN');
      expect(said).toBe(label);
      expect(said).not.toMatch(/[{/]/);
    }
    expect(readableGlob('**/*.py', 'en')).toBe('.py files');
  });

  it('a LIVE badge says how old its news is once it is not fresh', () => {
    expect(liveStaleness(10, true)).toBe('');
    expect(liveStaleness(45, true)).toBe('上次更新 45 秒前');
    expect(liveStaleness(3 * 3600 + 5, true)).toBe('上次更新 3 小时前');
    expect(liveStaleness(2 * 86400 + 5, true)).toBe('上次更新 2 天前');
    expect(liveStaleness(125, false)).toBe('last update 2m ago');
  });

  it('renders the staleness next to LIVE', () => {
    const view = emptyMissionView();
    const now = Date.now() / 1000;
    const rec: MissionRoleWorkItem = { id: 'a', role: 'engineer', kind: 'agent_message', detail: 'x', title: 'x', status: 'active', ts: now - 300, item_id: 't' };
    view.role_work = [rec];
    const role: Role = { role: 'engineer', backend: 'b', backend_label: 'B', model: 'm', effort: 'high', label: 'working', status: 'running', active: true, age_s: 1 };
    const html = renderToStaticMarkup(<AgentActivity view={view} roles={[role]} selectedRole="engineer" />);
    expect(html).toContain('LIVE');
    expect(html).toMatch(/上次更新 [45] 分钟前|last update [45]m ago/);
  });
});

describe('real rows from a dogfood session', () => {
  it('a web search reads as a search, not as opening a page', () => {
    const ev = tool('web_search: {"query": "site:arxiv.org 2026 AMA-Bench Evaluating Long-Horizon Memory"}', 'web_search');
    expect(plainToolLabel(ev, 'zh-CN')).toBe('搜索：site:arxiv.org 2026 AMA-Bench Evaluating Long-H…');
  });

  it('a one-line patch names only its file, never the hunk after it', () => {
    const ev = tool('apply_patch: *** Begin Patch *** Update File: /data/state/config.json @@ { + "SOME_SETTING": "x", "OTHER": "copilot" } *** End Patch', 'apply_patch');
    expect(plainToolLabel(ev, 'zh-CN')).toBe('修改 config.json');
  });
});
