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
  events: [], refresh: vi.fn(),
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
function openGit(overrides: Partial<WorkspaceGit> = {}) {
  client.setQueryData(['workspace-git', 's-A', 'workspace-A'], {
    available: true, branch: 'synthetic', status: ' M demo.txt', diff, log: '', stat: '', remotes: [], upstream: '',
    ahead: 0, behind: 0, identity: { name: '', email: '', valid: false },
    ...overrides,
  });
  act(() => { view = create(<QueryClientProvider client={client}><IdePage {...props} /></QueryClientProvider>); });
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
  expect(JSON.stringify(view.toJSON())).toContain('No workspace diff');
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
it('keeps the existing non-Git fallback and performs no additional queries', () => {
  openGit({ available: false });
  expect(JSON.stringify(view.toJSON())).toContain('Not a Git repository');
  expect(view.root.findAllByProps({ 'data-readonly-diff': true })).toHaveLength(0);
  expect(fetch).not.toHaveBeenCalled();
});

it('reads the active stopped workspace once, then updates tree, git and the open file only on invalidation', async () => {
  vi.useFakeTimers();
  const profiles = vi.spyOn(workspaceApi, 'profiles').mockResolvedValue({ profiles: [{ id: 'workspace-A', label: 'A', path: '/synthetic', canonical: true, source: 'project', project_sid: 's-A' }], default_id: 'workspace-A' });
  const tree = vi.spyOn(workspaceApi, 'tree').mockResolvedValue({ root: '/synthetic', truncated: false, entries: [
    { path: 'demo.txt', name: 'demo.txt', type: 'file', size: 6, mtime: 1, extension: '.txt' },
  ] });
  const git = vi.spyOn(workspaceApi, 'git').mockResolvedValue({ available: false, branch: '', status: '', stat: '', diff: '', log: '', remotes: [], upstream: '', ahead: 0, behind: 0, identity: { name: '', email: '', valid: false } });
  const file = vi.spyOn(workspaceApi, 'file').mockResolvedValue({ root: '/synthetic', workspace_id: 'workspace-A', path: 'demo.txt', content: 'before', size: 6, mtime: 1, extension: '.txt' });
  await act(async () => { view = create(<QueryClientProvider client={client}><IdePage {...props} active /></QueryClientProvider>); await vi.advanceTimersByTimeAsync(10); });
  const select = view.root.findAllByType('button').find(button => button.findAllByType('span').some(span => span.children.includes('demo.txt')))!;
  act(() => select.props.onClick());
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect([profiles, tree, git, file].map(read => read.mock.calls.length)).toEqual([0, 1, 1, 1]);
  file.mockResolvedValue({ root: '/synthetic', workspace_id: 'workspace-A', path: 'demo.txt', content: 'after', size: 5, mtime: 2, extension: '.txt' });
  await act(async () => {
    for (const key of ['workspace-tree', 'workspace-git', 'workspace-file']) await client.invalidateQueries({ queryKey: [key, 's-A'] });
    await vi.advanceTimersByTimeAsync(10);
  });
  expect([profiles, tree, git, file].map(read => read.mock.calls.length)).toEqual([0, 2, 2, 2]);
  expect(view.root.findByProps({ className: 'vscode-code' }).findByType('code').children).toContain('after');
});
