import { useRef } from 'react';
import { act, create } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { EventMsg } from '../../../core/src/types';
import { StepProcessContext, StepProcessDisclosure } from '../components/LiveWorkCard';
import { plainCommand } from '../lib/feedSteps';
import { liveLines } from '../lib/liveActivity';
import { useAutosizeTextarea } from '../useAutosizeTextarea';

const NOW = 10_000;

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

describe('what a task did, in plain words', () => {
  it('lists its actions in its time window, without the reasoning, nor conversation turns', () => {
    const chat = [...work, { type: 'ui.operator', text: '你好', ts: NOW - 5 } as EventMsg, { type: 'daemon.command.completed', ts: NOW - 4 } as EventMsg];
    const lines = liveLines(chat, 'task-2', NOW - 900, 'zh-CN', Infinity, '/w');
    expect(lines.map((line) => line.text)).toEqual([
      '运行 python train.py --lr 1e-4', '查阅 metrics.csv', '修改 train.py', 'loss 已降到 0.12，开始评测',
    ]);
    expect(lines.map((line) => line.kind)).toEqual(['command', 'tool', 'edit', 'message']);
    expect(JSON.stringify(lines)).not.toContain('internal thoughts');
  });

  it('keeps a finished step’s process, including actions that did not name their task', () => {
    const lines = liveLines(work, 'done-1', NOW - 5000, 'en', NOW - 4000);
    expect(lines.map((line) => line.text)).toEqual(['Run ls']);
  });

  it('cuts a role\'s words to a line after their markup goes, and leaves a command as it ran', () => {
    const events = [progress(NOW - 60, 'agent_message', '**尚未实现：取整规则存在歧义。** `sizefmt/core.py` 中舍弃小数。'),
      progress(NOW - 50, 'command_execution', "rg -g '**/*.py' x")];
    expect(liveLines(events, '', 0, 'zh-CN').map((line) => line.text))
      .toEqual(['尚未实现：取整规则存在歧义。 sizefmt/core.py 中舍弃小数。', "运行 rg -g '**/*.py' x"]);
  });

  it('reads a command\'s paths in the project from its root', () => {
    const events = [progress(NOW - 60, 'command_execution', 'rm -r -- /w/build /w2/build')];
    expect(liveLines(events, '', 0, 'zh-CN', Infinity, '/w').map((line) => line.text)).toEqual(['运行 rm -r -- build /w2/build']);
    const moved = [progress(NOW - 60, 'command_execution', 'rm -r -- /work/datefmt/build')];
    expect(liveLines(moved, '', 0, 'zh-CN', Infinity, '/work/datefmt').map((line) => line.text)).toEqual(['运行 rm -r -- build']);
    expect(plainCommand('ls /work/datefmt/', '/work/datefmt/')).toBe('ls .');
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

describe('a finished step\'s process', () => {
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
