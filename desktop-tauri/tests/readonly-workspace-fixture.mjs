import { startSessionFixture } from './session-context-fixture.mjs';

export const NORMAL_DIFF = 'diff --git a/demo.txt b/demo.txt\r\nindex 1111111..2222222 100644\r\n--- a/demo.txt\r\n+++ b/demo.txt\r\n@@ -1,2 +1,2 @@ title\r\n context\r\n-before\r\n+after\r\n';
export const LONG_WORKDIR = `D:\\Synthetic\\研究目录\\${'long-session-directory\\'.repeat(22)}session-A`;
export const DIFF_CASES = {
  normal: NORMAL_DIFF,
  empty: '',
  rename: 'diff --git a/old.txt b/new.txt\nsimilarity index 100%\nrename from old.txt\nrename to new.txt\n',
  binary: 'diff --git a/image.png b/image.png\nBinary files a/image.png and b/image.png differ\n',
  html: 'diff --git a/page.html b/page.html\n--- a/page.html\n+++ b/page.html\n@@ -1 +1 @@\n-<img src=x onerror="alert(1)">\n+<script>globalThis.fixtureExecuted=1</script>\n',
  large: 'diff --git a/large.txt b/large.txt\n@@ -1 +1 @@\n-old\n+' + 'x'.repeat(210000) + '\n',
  unknown: 'An unrecognized Git diagnostic\nOriginal text must remain available.\n',
  nongit: '',
};

export async function startReadonlyWorkspaceFixture(kind = 'normal') {
  const fixture = await startSessionFixture({ read: ({ url, state }) => {
    const sid = url.searchParams.get('sid') || /^\/api\/projects\/(s-[AB])\/status$/.exec(url.pathname)?.[1];
    if (!sid || !state.snapshots.has(sid)) return null;
    const snapshot = state.snapshots.get(sid);
    if (url.pathname === `/api/projects/${sid}/status`) return { body: {
      identity: 'Synthetic', backlog_pending: [], pending_questions: [], journal: [], inbox_pending: 0,
      continuous: snapshot.continuous, daemon: snapshot.daemon, roles: [], active_role: null,
    } };
    if (url.pathname === '/api/v2/workspaces') return { body: { default_id: 'synthetic-workspace', profiles: [{
      id: 'synthetic-workspace', label: 'Synthetic code workspace', path: snapshot.session.workdir,
      source: 'project', project_sid: sid, canonical: true,
    }] } };
    if (url.searchParams.get('workspace_id') !== 'synthetic-workspace') return null;
    if (url.pathname === '/api/v2/workspace/tree') return { body: { root: snapshot.session.workdir, entries: [], truncated: false } };
    if (url.pathname === '/api/v2/workspace/git') return { body: {
      available: kind !== 'nongit', branch: 'synthetic', status: kind === 'empty' ? '' : ' M demo.txt',
      diff: DIFF_CASES[kind], stat: '', log: '', remotes: [], upstream: '', ahead: 0, behind: 0,
      identity: { name: '', email: '', valid: false }, github: { authenticated: false, host: '', login: '', protocol: '', scopes: [] },
      publish_ready: false, truncated: kind === 'normal' || kind === 'large',
    } };
    return null;
  } });
  // Display-only synthetic path: no scan/open of this intentionally long path.
  fixture.state.snapshots.get('s-A').session.workdir = LONG_WORKDIR;
  return fixture;
}
