import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { IdePage } from '../research-workbench/pages/IdePage';
import type { ActiveWorkbenchPageProps } from '../research-workbench/pages/pageTypes';
import { workspaceApi, type WorkspaceGit } from '../research-workbench/workspaceApi';

const diff = 'diff --git a/demo.txt b/demo.txt\nindex 1111111..2222222 100644\n--- a/demo.txt\n+++ b/demo.txt\n@@ -1 +1 @@\n-before\n+after\n';
const props: ActiveWorkbenchPageProps = {
  sid: 's-A', active: false,
  snapshot: { session: { id: 's-A', display_name: 'Synthetic A', workdir: '/synthetic', objective: '', last_active: 1 },
    daemon: { alive: false, pid: null, uptime_seconds: null, backend: 'memory'},
    roles: [], backlog: [], },
  refresh: vi.fn(),
};
let client: QueryClient;
let view: ReactTestRenderer;
const fetch = vi.fn();
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  vi.stubGlobal('fetch', fetch); fetch.mockClear();
  client.setQueryData(['workspace-profiles', 's-A'], { profiles: [{ id: 'workspace-A', label: 'A', path: '/synthetic', canonical: true }], default_id: 'workspace-A' });
  client.setQueryData(['workspace-tree', 's-A', 'workspace-A'], { root: '/synthetic', entries: [], truncated: false });
});
afterEach(() => { if (view) act(() => view.unmount()); client.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });
function openWorkspace(overrides: Partial<WorkspaceGit> = {}) {
  client.setQueryData(['workspace-git', 's-A', 'workspace-A'], {
    available: true, branch: 'synthetic', status: ' M demo.txt', diff, log: '',
    ...overrides,
  });
  act(() => { view = create(<QueryClientProvider client={client}><IdePage {...props} /></QueryClientProvider>); });
}
function openGit(overrides: Partial<WorkspaceGit> = {}) {
  openWorkspace(overrides);
  const changes = view.root.findAllByType('button').find(button => button.children.join('') === 'Changes')!;
  act(() => changes.props.onClick());
}

it('wires the existing workspace diff to classified text and preserves backend truncation', () => {
  openGit({ truncated: true });
  expect(view.root.findAllByProps({ 'data-diff-kind': 'addition' })).toHaveLength(1);
  expect(JSON.stringify(view.toJSON())).toContain('The backend truncated this diff');
  expect(JSON.stringify(view.toJSON())).toContain('Current workspace diff');
  expect(fetch).not.toHaveBeenCalled();
});
it('represents an empty diff without claiming an Agent has finished or changed files', () => {
  openGit({ diff: '', status: '' });
  expect(view.root.findByProps({ className: 'readonly-diff__hint' }).children).toEqual(['No differences.']);
  expect(fetch).not.toHaveBeenCalled();
});
it('uses a Windows workspace basename without losing the full root or covering tree controls', () => {
  const path = `D:\\Synthetic\\${'long-parent\\'.repeat(25)}session-A\\`;
  client.setQueryData(['workspace-profiles', 's-A'], { profiles: [{ id: 'workspace-A', label: 'A', path, canonical: true }], default_id: 'workspace-A' });
  openGit();
  const name = view.root.findByProps({ className: 'vscode-root' }).findByType('strong');
  expect(name.children.join('')).toBe('session-A');
  expect(name.props.title).toBe(path);
  expect(fetch).not.toHaveBeenCalled();
});
it('omits source control for a non-Git workspace while keeping files readable without additional queries', () => {
  client.setQueryData(['workspace-tree', 's-A', 'workspace-A'], { root: '/synthetic', entries: [
    { path: 'demo.txt', type: 'file' },
  ], truncated: false });
  client.setQueryData(['workspace-file', 's-A', 'workspace-A', 'demo.txt'], {
    content: 'plain workspace content',
  });
  openWorkspace({ available: false });
  expect(view.root.findAllByProps({ className: 'vscode-source-control' })).toHaveLength(0);
  expect(view.root.findAllByProps({ 'aria-label': 'Source Control' })).toHaveLength(0);
  expect(view.root.findAllByProps({ 'data-readonly-diff': true })).toHaveLength(0);
  const file = view.root.findByProps({ className: 'workspace-tree' }).findAllByType('button')
    .find(button => button.findAllByType('span').some(span => span.children.includes('demo.txt')))!;
  act(() => file.props.onClick());
  expect(view.root.findByProps({ className: 'vscode-code' }).findByType('code').children).toEqual(['plain workspace content']);
  expect(view.root.findAllByProps({ className: 'vscode-terminal' })).toHaveLength(0);
  expect(fetch).not.toHaveBeenCalled();
});

it('reads the active stopped workspace once, then updates tree, git and the open file only on invalidation', async () => {
  vi.useFakeTimers();
  const profiles = vi.spyOn(workspaceApi, 'profiles').mockResolvedValue({ profiles: [{ id: 'workspace-A', label: 'A', path: '/synthetic', canonical: true, source: 'project', project_sid: 's-A' }], default_id: 'workspace-A' });
  const tree = vi.spyOn(workspaceApi, 'tree').mockResolvedValue({ root: '/synthetic', truncated: false, entries: [
    { path: 'demo.txt', type: 'file' },
  ] });
  const git = vi.spyOn(workspaceApi, 'git').mockResolvedValue({ available: false, branch: '', status: '', diff: '', log: '' });
  const file = vi.spyOn(workspaceApi, 'file').mockResolvedValue({ content: 'before' });
  await act(async () => { view = create(<QueryClientProvider client={client}><IdePage {...props} active /></QueryClientProvider>); await vi.advanceTimersByTimeAsync(10); });
  const select = view.root.findAllByType('button').find(button => button.findAllByType('span').some(span => span.children.includes('demo.txt')))!;
  act(() => select.props.onClick());
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect([profiles, tree, git, file].map(read => read.mock.calls.length)).toEqual([0, 1, 1, 1]);
  file.mockResolvedValue({ content: 'after' });
  await act(async () => {
    for (const key of ['workspace-tree', 'workspace-git', 'workspace-file']) await client.invalidateQueries({ queryKey: [key, 's-A'] });
    await vi.advanceTimersByTimeAsync(10);
  });
  expect([profiles, tree, git, file].map(read => read.mock.calls.length)).toEqual([0, 2, 2, 2]);
  expect(view.root.findByProps({ className: 'vscode-code' }).findByType('code').children).toContain('after');
});

it('opens source control on commit history and reads the selected commit without repository settings', () => {
  const hash = 'a'.repeat(40);
  client.setQueryData(['workspace-commit', 's-A', 'workspace-A', hash], { diff, truncated: false });
  openWorkspace({ log: `${hash}\t2026-10-29T12:00:00Z\tAuthor\tSaved report` });
  expect(view.root.findAllByType('button').filter(button => button.children.join('') === 'Repository')).toHaveLength(0);
  expect(view.root.findByProps({ className: 'vscode-sc-tabs' }).findAllByType('button').map(button => button.children.join(''))).toEqual(['Changes', 'Timeline']);
  expect(view.root.findByProps({ className: 'vscode-commits' }).findAllByType('strong')[0].children).toEqual(['Saved report']);
  act(() => view.root.findByProps({ className: 'vscode-commits' }).findByType('button').props.onClick());
  expect(view.root.findAllByProps({ 'data-readonly-diff': true })).toHaveLength(1);
  expect(JSON.stringify(view.toJSON())).toContain('Commit diff');
  expect(fetch).not.toHaveBeenCalled();
});
