import { readFileSync } from 'node:fs';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, expect, it, vi } from 'vitest';
import { conversationRows, projectRecord, type Recorded } from '../../../../packages/core/src/serve/views';
import type { Project } from '../../../../packages/core/src/store';
import { I18nProvider } from '../i18n';
import { EventStream, roleSnapshot } from './serverSnapshot';
import { ProjectRoleLogs } from '../components/EventStream';
import { QueryClientProvider } from '@tanstack/react-query';
import { roleClient } from './serverSnapshot';

const fixture = new URL('../../../../packages/core/test/fixtures/progress-messages/', import.meta.url);
const record = readFileSync(new URL('events.jsonl', fixture), 'utf8').trim().split('\n').map(line => JSON.parse(line) as Recorded);
const project = JSON.parse(readFileSync(new URL('project.json', fixture), 'utf8')) as Project;
afterEach(() => vi.unstubAllGlobals());

it.each(['zh-CN', 'en'])('#6 team call has no request status; role thought fallback stays neutral (%s)', locale => {
  vi.stubGlobal('localStorage', { getItem: () => locale });
  const at = record.findIndex(event => event.type === 'call.start' && event.role === 'manager' && !event.answers);
  // Amplify this actual team turn with an English backend thought: Chinese hides the heading.
  const prefix: Recorded[] = [...record.slice(0, at + 1),
    { type: 'step', role: 'manager', kind: 'thought', text: 'Checking team results', ts: record[at]!.ts }];
  const reading = projectRecord(prefix);
  for (const stopped of [false, true]) {
    const live = stopped ? { since: null, calls: [], requests: [] } : {
      since: 1, calls: [{ role: 'manager' as const, task: undefined, started_ts: 1, words: '核对团队结果', wordsKind: 'process' as const }], requests: [],
    };
    const rows = conversationRows(reading, { ...project, status: stopped ? 'stopped' : 'working' }, live);
    const html = renderToStaticMarkup(<I18nProvider><EventStream events={rows} connected embedded showReasoning={false} /></I18nProvider>);
    expect(html).not.toContain('正在回复你的消息');
    expect(html).not.toContain('Replying to your message');
    expect(html).not.toContain('data-manager-reply="running"');
    if (!stopped) expect(html).toContain('核对团队结果');
  }
  const snap = roleSnapshot(reading.roleEvents);
  const log = renderToStaticMarkup(<QueryClientProvider client={roleClient}><I18nProvider><ProjectRoleLogs snapshot={snap} connected details /></I18nProvider></QueryClientProvider>);
  expect(log).not.toContain('正在回复你的消息');
  expect(log).not.toContain('Replying to your message');
  expect(log).toContain(locale === 'zh-CN' ? '正在处理团队工作' : 'Checking team results');
});

it.each(['zh-CN', 'en'])('#2 a request call\'s role thought fallback says what the status bar says (%s)', locale => {
  vi.stubGlobal('localStorage', { getItem: () => locale });
  const at = record.findIndex(event => event.type === 'call.start' && event.role === 'manager' && event.answers);
  const reading = projectRecord([...record.slice(0, at + 1),
    { type: 'step', role: 'manager', kind: 'thought', text: 'Checking the schedule data', ts: record[at]!.ts }]);
  const snap = roleSnapshot(reading.roleEvents, { work_status: { state: 'running', roles: ['manager'], role: 'manager', taskId: '', title: '', reason: '',
    foreground: true, activityAt: null, label: { 'zh-CN': '正在回复你的消息', en: 'Replying to your message' } } });
  const log = renderToStaticMarkup(<QueryClientProvider client={roleClient}><I18nProvider><ProjectRoleLogs snapshot={snap} connected /></I18nProvider></QueryClientProvider>);
  expect(/data-role-thought[^>]*>([^<]*)</.exec(log)?.[1]).toBe(locale === 'zh-CN' ? '正在回复你的消息' : 'Checking the schedule data');
});
