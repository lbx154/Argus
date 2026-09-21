import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { IdePage } from '../research-workbench/pages/IdePage';
import type { ActiveWorkbenchPageProps } from '../research-workbench/pages/pageTypes';
import type { WorkspaceGit } from '../research-workbench/workspaceApi';

const diff = 'diff --git a/demo.txt b/demo.txt\nindex 1111111..2222222 100644\n--- a/demo.txt\n+++ b/demo.txt\n@@ -1 +1 @@\n-before\n+after\n';
const props: ActiveWorkbenchPageProps = {
  sid: 's-A', active: false,
  project: { id: 's-A', label: 'A', objective: '', last_active: 1, daemon_alive: false, daemon_pid: null, uptime_seconds: null },
  snapshot: { session: { id: 's-A', display_name: 'Synthetic A', cwd: '/synthetic', objective: '', last_active: 1 },
    daemon: { alive: false, pid: null, uptime_seconds: null, backend: 'memory', global_daily_cap_usd: null },
    roles: [], backlog: [], recent_events: [] },
  events: [], connected: false, snapshotUpdatedAt: 1, refresh: vi.fn(),
  controls: { start: vi.fn(), stop: vi.fn(), busy: false, error: '' }, navigate: vi.fn(),
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
afterEach(() => { if (view) act(() => view.unmount()); client.clear(); vi.unstubAllGlobals(); });
function openGit(overrides: Partial<WorkspaceGit> = {}) {
  client.setQueryData(['workspace-git', 's-A', 'workspace-A'], {
    available: true, branch: 'synthetic', status: ' M demo.txt', diff, log: '', stat: '', remotes: [], upstream: '',
    ahead: 0, behind: 0, identity: { name: '', email: '', valid: false },
    github: { authenticated: false, host: '', login: '', protocol: '', scopes: [] }, publish_ready: false, ...overrides,
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
