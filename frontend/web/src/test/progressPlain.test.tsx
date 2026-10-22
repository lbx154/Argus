import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { EventMsg } from '../api';
import { emptyMissionView } from '../../../core/src/missionView';
import type { MissionRoleWorkItem, Role } from '../../../core/src/types';
import { AgentActivity } from '../components/AgentActivity';
import { EventStream } from '../components/EventStream';
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
      [tool('glob: {"pattern": "**/*"}', 'glob'), '查找所有文件'],
      [tool('glob: {"pattern": "src/**/*.ts"}', 'glob'), '查找 src 里的 .ts 文件'],
      [tool('glob: {"pattern": "src/**/*"}', 'glob'), '查找 src 里的所有文件'],
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
    expect(html).toContain('In progress');
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

  it("names no file of Argus' own plumbing", () => {
    const ev = tool('view: {"path": "/fastdata/argus/state/projects/7f066b2919cb"}', 'view');
    expect(plainToolLabel(ev, 'zh-CN')).toBe('查阅文件');
    expect(plainToolLabel(tool('view: {"path": "/repo/strtools/.autors"}', 'view'), 'zh-CN')).toBe('查阅文件');
  });

  it('a recalled wiki page, a spilled tool output and the project folder read as what they are', () => {
    const view = (path: string) => tool(`view: {"path": "${path}"}`, 'view');
    const cases: Array<[string, string, string]> = [
      ['/repo/.autors/learn/wiki/pages/deepinfer-inference-architecture.md', '查阅知识页 deepinfer-inference-architecture', 'Read wiki page deepinfer-inference-architecture'],
      ['/repo/.autors/learn/wiki', '查阅知识库', 'Read the wiki'],
      ['/runtime/tmp/1792644657527-copilot-tool-output-87d59816852246238961139c2f724073.txt', '查阅工具输出', 'Read a saved tool output'],
      ['/srv/state/workspaces/s-8d313a94', '查阅项目目录', 'Read the project folder'],
    ];
    for (const [path, zh, en] of cases) {
      expect(plainToolLabel(view(path), 'zh-CN')).toBe(zh);
      expect(plainToolLabel(view(path), 'en')).toBe(en);
    }
  });

  it("a feed command row cuts a backend session's paths and keeps the raw command for the tooltip", () => {
    const files = '/srv/state/copilot-home/session-state/09822296/files';
    const command = `mkdir -p ${files} && pytest -q | tee ${files}/pytest.log`;
    const events = [{ type: 'engineer.progress', kind: 'command_execution', agent_layer: 'engineer', text: command, ts: 1, status: 'running' }] as unknown as EventMsg[];
    const html = renderToStaticMarkup(<EventStream events={events} connected showReasoning={false} onToggleReasoning={() => undefined} />);
    expect(html).toContain('>mkdir -p …/files &amp;&amp; pytest -q | tee …/pytest.log<');
    expect(html).toContain(`title="${command.replace(/&/g, '&amp;')}"`);
  });

  it('a regular expression is not shown raw', () => {
    const ev = tool('rg: {"pattern": "^(name|description):.*(engineer|Python)", "paths": ["/srv/state/skills"]}', 'rg');
    expect(plainToolLabel(ev, 'zh-CN')).toBe('搜索文件内容');
    expect(plainToolLabel(ev, 'en')).toBe('Search file contents');
  });

  it("the Reviewer's verdict reads as submitting the review, not as an internal tool", () => {
    const ev = tool('argus_review_actions-submit_review: {"decision": "approve"}', 'argus_review_actions-submit_review');
    expect(plainToolLabel(ev, 'zh-CN')).toBe('提交审阅结论');
    expect(plainToolLabel(ev, 'en')).toBe('Submit the review');
  });
});
