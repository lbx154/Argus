import { useRef } from 'react';
import { act, create } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { emptyMissionView } from '../../../core/src/missionView';
import type { EventMsg, Snapshot } from '../../../core/src/types';
import { LiveWorkCard, StepProcessContext, StepProcessDisclosure } from '../components/LiveWorkCard';
import { plainCommand } from '../lib/feedSteps';
import { liveActivity, liveLines, QUIET_AFTER_S } from '../lib/liveActivity';
import { useAutosizeTextarea } from '../useAutosizeTextarea';

const NOW = 10_000;

function running(): Snapshot {
  return {
    session: { id: 's-live', display_name: 'Live', objective: '', cwd: '/w', last_active: NOW },
    daemon: { alive: true, pid: 1, uptime_seconds: 600, backend: 'pi' },
    roles: [{ role: 'engineer', active: true, backend: 'pi', backend_label: 'Pi', model: 'm', effort: null, label: 'run', status: 'running', age_s: 1 }],
    backlog: [
      { id: 'done-1', title: '整理基准', objective: '', status: 'done', priority: 1, started_ts: NOW - 5000, finished_ts: NOW - 4000 },
      { id: 'skip-1', title: '跳过的', objective: '', status: 'skipped', priority: 1 },
      { id: 'task-2', title: '新方法第三轮训练', objective: '', status: 'running', priority: 1, started_ts: NOW - 900 },
      { id: 'task-3', title: '修复评测脚本', objective: '', status: 'pending', priority: 1 },
    ],
    recent_events: [],
  };
}

const progress = (ts: number, kind: string, text: string, extra: Record<string, unknown> = {}): EventMsg =>
  ({ type: 'engineer.progress', kind, text, agent_layer: 'engineer', ts, ...extra }) as EventMsg;

const work: EventMsg[] = [
  progress(NOW - 4500, 'command_execution', 'ls', { item_id: 'done-1' }),
  progress(NOW - 300, 'reasoning', 'internal thoughts that are not shown'),
  progress(NOW - 290, 'command_execution', '/bin/bash -lc "python train.py --lr 1e-4"'),
  progress(NOW - 200, 'tool_use', 'view: {"path": "/w/results/metrics.csv"}'),
  progress(NOW - 120, 'file_change', '*** Update File: src/train.py'),
  progress(NOW - 30, 'agent_message', 'loss 已降到 0.12，开始评测', { message_id: 'm1' }),
];

describe('what the project is doing right now', () => {
  it('names the step, its place in the plan, and its recent actions in plain words', () => {
    const activity = liveActivity(running(), emptyMissionView(), work, NOW, 'zh-CN');
    expect(activity).toMatchObject({ show: true, tone: 'working', role: 'engineer', headline: '正在执行：新方法第三轮训练', position: { index: 2, total: 3 }, note: '' });
    expect(activity.startedAt).toBe(NOW - 900);
    expect(activity.lines.map((line) => line.text)).toEqual([
      '运行 python train.py --lr 1e-4', '查阅 metrics.csv', '修改 train.py', 'loss 已降到 0.12，开始评测',
    ]);
    expect(activity.lines.map((line) => line.kind)).toEqual(['command', 'tool', 'edit', 'message']);
    expect(JSON.stringify(activity.lines)).not.toContain('internal thoughts');
  });

  it('stays hidden when nothing runs or when the Manager reply already shows its steps', () => {
    const stopped = running();
    stopped.daemon.alive = false;
    expect(liveActivity(stopped, null, work, NOW, 'zh-CN').show).toBe(false);
    const chatting = running();
    chatting.manager_requests = [{ request_id: 'r', status: 'running' }];
    expect(liveActivity(chatting, null, work, NOW, 'zh-CN').show).toBe(false);
  });

  it('says a long command is still running instead of looking stuck', () => {
    const snapshot = running();
    snapshot.backlog[2].started_ts = NOW - 3000;
    const events = [
      progress(NOW - 1900, 'tool_use', 'view: {"path": "/w/train.py"}'),
      progress(NOW - 1800, 'command_execution', 'python train.py', { status: 'running' }),
    ];
    const activity = liveActivity(snapshot, null, events, NOW, 'zh-CN');
    expect(activity.lines.at(-1)).toMatchObject({ running: true, text: '运行 python train.py' });
    expect(activity.note).toBe('命令仍在运行，已运行 30 分钟');
    // Anything after the command means it is no longer the thing in flight.
    const later = liveActivity(snapshot, null, [...events, progress(NOW - 60, 'reasoning', 'next')], NOW, 'zh-CN');
    expect(later.lines.at(-1)).toMatchObject({ running: false });
  });

  it('is honest when nothing moves: quiet first, stuck only when the daemon says so', () => {
    const old = work.map((event) => ({ ...event, ts: Number(event.ts) - QUIET_AFTER_S - 600 }));
    const snapshot = running();
    snapshot.backlog[2].started_ts = NOW - 5000;
    const quiet = liveActivity(snapshot, null, old, NOW, 'zh-CN');
    expect(quiet.tone).toBe('quiet');
    expect(quiet.note).toMatch(/分钟没有新动作/);
    snapshot.daemon.health = { stalled: true };
    snapshot.roles = [];
    const stalled = liveActivity(snapshot, null, old, NOW, 'zh-CN');
    expect(stalled.tone).toBe('stalled');
    expect(stalled.note).toMatch(/可能卡住了/);
  });

  it('counts only work, not conversation turns, as actions', () => {
    const chat = [...work, { type: 'ui.operator', text: '你好', ts: NOW - 5 } as EventMsg, { type: 'daemon.command.completed', ts: NOW - 4 } as EventMsg];
    const activity = liveActivity(running(), null, chat, NOW, 'zh-CN');
    expect(activity.lines).toHaveLength(4);
    expect(activity.lastAt).toBe(NOW - 30);
  });

  it('keeps a finished step’s process, including actions that did not name their task', () => {
    const lines = liveLines(work, 'done-1', NOW - 5000, 'en', NOW - 4000);
    expect(lines.map((line) => line.text)).toEqual(['Run ls']);
  });

  it('unwraps shell boilerplate from commands', () => {
    expect(plainCommand("/bin/bash -lc 'pytest -q tests'")).toBe('pytest -q tests');
    expect(plainCommand('bash -c "nvidia-smi"')).toBe('nvidia-smi');
    expect(plainCommand('python eval.py\nsecond line')).toBe('python eval.py');
  });

  it("cuts a path into a backend session's scratch files to its last name, and keeps the work's own paths", () => {
    const files = '/srv/state/copilot-home/session-state/09822296-0a3e/files';
    expect(plainCommand(`mkdir -p ${files} && pytest -q | tee ${files}/pytest.log`)).toBe('mkdir -p …/files && pytest -q | tee …/pytest.log');
    expect(plainCommand('git -C /work/repo status')).toBe('git -C /work/repo status');
  });

  it("cuts a path into Argus' own package or a shared wiki, and keeps a project's docs/wiki", () => {
    expect(plainCommand("rg -n '^name:' /opt/argus-src/argus/builtin_skills /srv/home/wiki/_shared_verticals/software -g SKILL.md"))
      .toBe("rg -n '^name:' …/builtin_skills …/software -g SKILL.md");
    expect(plainCommand('ls /repo/docs/wiki/pages')).toBe('ls /repo/docs/wiki/pages');
  });
});

describe('the live work card', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('pins the step, the clocks and the last actions, and opens to the whole process', () => {
    vi.stubGlobal('localStorage', { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
    const events = [...work, ...[1, 2, 3].map((n) => progress(NOW - 20 + n, 'tool_use', `view: {"path": "/w/f${n}.py"}`))];
    vi.spyOn(Date, 'now').mockReturnValue(NOW * 1000);
    let tree!: ReturnType<typeof create>;
    act(() => { tree = create(<LiveWorkCard snapshot={running()} view={null} events={events} />); });
    const text = () => JSON.stringify(tree.toJSON());
    expect(text()).toContain('新方法第三轮训练');
    expect(tree.root.findAll((node) => node.props.className === 'live-work-line')).toHaveLength(3);
    const toggle = tree.root.find((node) => node.type === 'button' && node.props.className === 'live-work-toggle');
    act(() => toggle.props.onClick());
    expect(tree.root.findAll((node) => node.props.className === 'live-work-line')).toHaveLength(7);
    act(() => tree.unmount());
    vi.restoreAllMocks();
  });

  it('names the Manager as every other surface does', () => {
    vi.stubGlobal('localStorage', { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
    vi.spyOn(Date, 'now').mockReturnValue(NOW * 1000);
    const snapshot = running();
    snapshot.roles = [{ ...snapshot.roles[0], role: 'manager' }];
    let tree!: ReturnType<typeof create>;
    act(() => { tree = create(<LiveWorkCard snapshot={snapshot} view={null} events={work} />); });
    const meta = tree.root.find((node) => node.props.className === 'live-work-meta');
    expect(meta.findAllByType('span')[0].children).toEqual(['Manager']);
    act(() => tree.unmount());
    vi.restoreAllMocks();
  });

  it('renders nothing while the project is stopped', () => {
    const stopped = running();
    stopped.daemon.alive = false;
    let tree!: ReturnType<typeof create>;
    act(() => { tree = create(<LiveWorkCard snapshot={stopped} view={null} events={work} />); });
    expect(tree.toJSON()).toBeNull();
  });

  it('lets a finished step open its process under the report', () => {
    const lookup = (id: string) => liveLines(work, id, NOW - 5000, 'zh-CN', NOW - 4000);
    let tree!: ReturnType<typeof create>;
    act(() => { tree = create(<StepProcessContext.Provider value={lookup}><StepProcessDisclosure taskId="done-1" /></StepProcessContext.Provider>); });
    expect(JSON.stringify(tree.toJSON())).toContain('Show the process (1 action)');
    act(() => tree.root.findByType('button').props.onClick());
    expect(JSON.stringify(tree.toJSON())).toContain('运行 ls');
  });
});

describe('the message box follows its text both ways', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('shrinks back after text is deleted, even from a scrolled maximum', () => {
    vi.stubGlobal('window', { addEventListener: vi.fn(), removeEventListener: vi.fn() });
    const LINE = 28;
    const element = {
      value: '', style: {} as Record<string, string>, clientWidth: 400,
      get clientHeight() { return parseFloat(this.style.height || '0'); },
      // Like a browser: never less than the box itself.
      get scrollHeight() { return Math.max(this.value.split('\n').length * LINE, this.clientHeight); },
      getClientRects: () => [{}], addEventListener: vi.fn(), removeEventListener: vi.fn(),
    };
    function Box({ value }: { value: string }) {
      const ref = useRef<HTMLTextAreaElement>(null);
      element.value = value;
      useAutosizeTextarea(ref, value, 28, 132);
      return <textarea ref={ref} value={value} readOnly />;
    }
    let tree!: ReturnType<typeof create>;
    act(() => { tree = create(<Box value="one" />, { createNodeMock: () => element }); });
    expect(element.style.height).toBe('28px');
    act(() => tree.update(<Box value={'1\n2\n3\n4\n5\n6\n7'} />));
    expect(element.style.height).toBe('132px');
    expect(element.style.overflowY).toBe('auto');
    act(() => tree.update(<Box value={'1\n2'} />));
    expect(element.style.height).toBe('56px');
    expect(element.style.overflowY).toBe('hidden');
    act(() => tree.update(<Box value="" />));
    expect(element.style.height).toBe('28px');
  });
});
