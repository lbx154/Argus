import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Snapshot } from '../../../core/src/types';
import { TopBar } from '../components/TopBar';
import { WorkStatusBar } from '../components/WorkStatusBar';

describe('status dot tone', () => {
  it('shows a running dot, not "waiting for you", while the team backs off and retries', () => {
    const snapshot = {
      session: { id: 's-research', display_name: 'Research', objective: '', workdir: '/workspace', last_active: 100 },
      daemon: { alive: true, pid: 1, uptime_seconds: 20, backend: 'pi' },
      roles: [], backlog: [], work_status: { state: 'waiting', reason: 'awaiting_next_step', role: '', roles: [], taskId: '', title: '', foreground: false, activityAt: null, label: { 'zh-CN': '等待下一步工作', en: 'Waiting for the next step' } },
    } as Snapshot;
    const html = renderToStaticMarkup(<WorkStatusBar snapshot={snapshot} connected />);
    expect(html).toContain('data-state="waiting"');
    expect(html).toContain('data-phase="running"');
    expect(html).toContain('data-tone="running"');
  });

  const project = (alive: boolean, roles: Snapshot['roles'] = []) => ({
    session: { id: 's', display_name: 'Timer', objective: 'Make a timer', workdir: '/w', last_active: 100 },
    daemon: { alive, pid: alive ? 1 : null, uptime_seconds: null, backend: 'codex' },
    work_status: { state: alive ? 'running' : 'idle', reason: '', role: alive ? 'engineer' : '', roles: alive ? ['engineer'] : [], taskId: 't1', title: 'Write timer.html', foreground: false, activityAt: null, label: { 'zh-CN': alive ? '1 个任务进行中' : '已完成', en: alive ? '1 task in progress' : 'Done' } },
    roles, backlog: [{ id: 't1', title: 'Write timer.html', objective: '', status: alive ? 'in_progress' : 'done', priority: 0 }], }) as Snapshot;


  it('says where the work stands in one phrase, the same at the top and above the map', () => {
    const done = project(false);
    const bar = renderToStaticMarkup(<WorkStatusBar snapshot={done} connected compact />);
    const top = renderToStaticMarkup(<TopBar snap={done} streamOk onStart={() => {}} onStop={() => {}} onManage={() => {}} busy={false} />);
    for (const html of [bar, top]) {
      expect(html.match(/>Done</g)).toHaveLength(1);
      expect(html).not.toContain('Ready for a new task');
    }
    // How long ago it last moved matters only while it runs.
    expect(bar).not.toContain('Progress');
  });

  it('names who is at work, not each step they take', () => {
    const working = project(true, [{ role: 'engineer', active: true, status: 'running', label: 'Engineer', age_s: null, backend: 'codex', backend_label: 'Codex', model: '', effort: null, tasks: ['t1'] }]);
    const top = renderToStaticMarkup(<TopBar snap={working} streamOk onStart={() => {}} onStop={() => {}} onManage={() => {}} busy={false} />);
    expect(top).toContain('1 task in progress');
    expect(top).not.toContain('dark theme');
  });
  it('leaves the map one project status, and uses the Manager’s words only while the server says it is replying', () => {
    const done = project(false);
    const header = renderToStaticMarkup(<TopBar snap={done} showStatus={false} showRunControl={false}
      streamOk onStart={() => {}} onStop={() => {}} onManage={() => {}} busy={false} />);
    expect(header).not.toContain('topbar-status');
    const message = '已找到长路径的显示入口，正在检查手机详情。';
    const replying = { ...done, work_status: { ...done.work_status!, state: 'running' as const, role: 'manager', roles: ['manager'], foreground: true } };
    expect(renderToStaticMarkup(<WorkStatusBar snapshot={replying} managerMessage={message} connected compact />)).toContain(message);
    expect(renderToStaticMarkup(<WorkStatusBar snapshot={replying} connected compact />)).toContain('Replying to your message');
    const bar = renderToStaticMarkup(<WorkStatusBar snapshot={done} managerMessage={message} connected compact />);
    expect(bar).toContain('>Done<');
    expect(bar).not.toContain(message);
  });
});
