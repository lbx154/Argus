import { expect, it } from 'vitest';
import type { KnowledgeEvent, WikiLibraryItem, WikiScope } from '../api';
import { groupKnowledgeEvents, latestLearned, resolveKnowledgeItem } from './KnowledgeFeed';

const roots: Record<string, string> = { project: '.autors/proj/wiki', 'vertical/research': '/shared/wiki/_shared_verticals/research', 'vertical/math': '/shared/wiki/_shared_verticals/math', global: '/shared/wiki/_global' };
const page = (name: string, updated_at: number, scope: WikiScope, vertical = '', extra: Partial<WikiLibraryItem> = {}): WikiLibraryItem => ({
  scope, vertical, root: roots[scope === 'vertical' ? `vertical/${vertical}` : scope],
  path: `pages/${name.toLowerCase().replace(/\s+/g, '-')}.md`, title: name, description: `About ${name}`, updated_at,
  kind: 'page', source: '', created: '', ...extra,
});
const items = [
  page('Fused epilogue', 60, 'project', 'research', { kind: 'fact', source: 's-one/m1', created: '2026-09-17' }),
  page('Baseline table', 50, 'vertical', 'research'),
  page('Lean tactics', 45, 'vertical', 'math', { kind: 'lesson', path: 'pages/lessons/20260917-lean-tactics.md', source: 's-two/m7', created: '2026-09-17' }),
  page('Cluster etiquette', 40, 'global', '', { kind: 'survey', source: 'chat/s-one' }),
  page('Dataset notes', 30, 'project', 'research', { kind: 'lesson' }),
];
const now = Math.floor(Date.now() / 1000);
const event = (kind: KnowledgeEvent['kind'], ts: number, item: WikiLibraryItem, extra: Partial<KnowledgeEvent> = {}): KnowledgeEvent => ({
  ts, kind, scope: item.scope, vertical: item.vertical, path: item.path, title: item.title,
  source_project: item.scope === 'project' ? 'one' : 's-two', mission_id: 'm1', role: kind === 'learned' ? 'host' : 'reviewer', page_kind: item.kind, note: '', ...extra,
});
const events: KnowledgeEvent[] = [
  event('learned', now - 30, items[2], { note: 'Search the Lean library before proving by hand.' }),
  event('recalled', now - 100, items[1]),
  event('recalled', now - 120, items[3]),
  event('recalled', now - 150, items[0]),
  event('recalled', now - 170, items[1], { role: 'engineer' }),
  event('recalled', now - 400, items[4]),
  event('promoted', now - 3600, items[1], { role: 'host' }),
  event('learned', now - 5000, items[4], { source_project: 'other', title: 'Vanished page', path: 'pages/vanished.md' }),
];

it('folds consecutive recalls by one role within a minute and leaves everything else as its own row', () => {
  const rows = groupKnowledgeEvents(events);
  expect(rows.map(row => row.kind === 'single' ? `${row.event.kind}:${row.event.title}` : `recalls:${row.role}:${row.events.length}`))
    .toEqual(['learned:Lean tactics', 'recalls:reviewer:3', 'recalls:engineer:1', 'recalls:reviewer:1', 'promoted:Baseline table', 'learned:Vanished page']);
  // Order on the wire does not matter; the newest line anchors each group.
  expect(groupKnowledgeEvents([...events].reverse())).toEqual(rows);
});

it('resolves a journal line to a catalog page only when level, vertical, path and project agree', () => {
  expect(resolveKnowledgeItem(events[1], items, 'one')).toBe(items[1]);
  expect(resolveKnowledgeItem(events[3], items, 'one')).toBe(items[0]);
  expect(resolveKnowledgeItem({ ...events[3], source_project: 'other' }, items, 'one')).toBeNull();
  expect(resolveKnowledgeItem({ ...events[1], vertical: 'math' }, items, 'one')).toBeNull();
  expect(latestLearned(events, now)?.title).toBe('Lean tactics');
  expect(latestLearned(events.filter(item => item.kind !== 'learned'), now)?.kind).toBe('promoted');
  expect(latestLearned(events, now + 2 * 86_400)).toBeNull();
});
