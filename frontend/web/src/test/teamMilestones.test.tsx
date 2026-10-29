import type { ReactNode } from 'react';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EventMsg } from '../api';
import type { MissionMilestone, MissionView } from '../../../core/src/types';
import { emptyMissionView } from '../../../core/src/missionView';
import { EventStream, SessionIdContext, WorkspaceRootsContext } from '../components/EventStream';
import { milestoneLines, workFile } from '../lib/milestones';

vi.mock('../lib/motion', () => ({ useGsapMotion: () => {} }));
vi.mock('../components/MarkdownContent', () => ({
  MarkdownContent: ({ children }: { children: ReactNode }) => <div data-markdown>{children}</div>,
  WorkspaceImage: ({ path }: { path: string }) => <img data-workspace-image={path} alt="" />,
}));

// Hosted trial s-dacac579 (2026-10-29): for an hour the conversation showed only
// "正在执行：…" while the record held these.
const SENT_BACK = '已选定计算机视觉研究方向并建立真实数据运行环境，但切换后的关键比较路径尚未在实际短跑中验证，需补齐后才能确认本轮完成。';
const TASK = '2873b58e7a44';
const row = (id: string, ts: number, fields: Partial<MissionMilestone>): MissionMilestone =>
  ({ id, ts, item_id: TASK, kind: 'round_started', ...fields }) as MissionMilestone;
const TRIAL: MissionMilestone[] = [
  row('requeue', 200, { kind: 'task_requeued', title: '确定视觉研究方向', error: 'orphaned: previous process did not finish' }),
  row('files-1', 201, { kind: 'files', round: 1, paths: [
    '/tenant/state/projects/s-dacac579/handoffs/2873b58e7a44/CHECKPOINT.md',
    '.autors/s-dacac579/wiki/pages/venues/cvpr.md',
    'METHOD.md', '/w/figures/ablation.png', '.argus/evidence/experiment/smoke/summary.png', '.argus/live/view.json',
  ] }),
  row('done-1', 202, { kind: 'round_finished', round: 1 }),
  row('review-1', 203, { kind: 'review', round: 1, status: 'engineer', source: 'reviewer', reason: SENT_BACK }),
  row('round-2', 204, { kind: 'round_started', round: 2 }),
  row('done-2', 205, { kind: 'round_finished', round: 2 }),
  row('round-3', 206, { kind: 'round_started', round: 3 }),
];

describe('the team milestones as conversation lines', () => {
  it('says each recorded milestone plainly, keeps the reason as recorded, and keeps only the work\'s files', () => {
    const lines = milestoneLines(TRIAL, 'zh-CN', ['/w']);

    expect(lines.map(line => line.text)).toEqual([
      '上一次运行没有正常结束，任务重新开始',
      '第 1 轮写入了 METHOD.md、ablation.png、summary.png',
      '第 1 轮工作完成',
      '检查退回：第 1 轮需要再改',
      '第 2 轮开始',
      '第 2 轮工作完成',
      '第 3 轮开始',
    ]);
    expect(lines[3]).toMatchObject({ tone: 'waiting', detail: SENT_BACK });
    expect(lines[1].files).toEqual(['METHOD.md', 'figures/ablation.png', '.argus/evidence/experiment/smoke/summary.png']);
    expect(workFile('/elsewhere/x.png', ['/w'])).toBe('');
  });

  it('tells an accepted review, the Engineer\'s own hand-over and a hold apart', () => {
    const lines = milestoneLines([
      row('ok', 1, { kind: 'review', round: 2, status: 'done', source: 'reviewer', reason: 'Accepted.' }),
      row('own', 2, { kind: 'review', round: 1, status: 'manager', source: 'engineer_handoff', reason: 'Tool failed.' }),
      row('pause', 3, { kind: 'stopped', type: 'life.budget.pause', item_id: '', text: 'Daily budget spent.' }),
    ], 'en', []);

    expect(lines.map(line => [line.text, line.tone])).toEqual([
      ['Review accepted round 2', 'done'],
      ['The Engineer handed round 1 to the Manager to decide', 'waiting'],
      ['budget cap reached — paused · Daily budget spent.', 'failed'],
    ]);
  });
});

let renderer: ReactTestRenderer | undefined;
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('window', {
    requestAnimationFrame: () => 1, cancelAnimationFrame: () => undefined,
    setInterval: vi.fn(() => 1), clearInterval: vi.fn(),
  });
});
afterEach(() => {
  act(() => renderer?.unmount());
  renderer = undefined;
  vi.unstubAllGlobals();
});

const text = (node: ReactTestInstance): string =>
  node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
const chat: EventMsg[] = [
  { type: 'ui.operator', text: 'Write a CVPR paper', ts: 100, message_id: 'q-1' },
  { type: 'ui.argus', text: 'On it.', ts: 101, message_id: 'a-1' },
  { type: 'ui.operator', text: 'How is it going?', ts: 300, message_id: 'q-2' },
  { type: 'ui.argus', text: 'Round 1 was sent back; round 3 is running.', ts: 301, message_id: 'a-2' },
];
const view = (milestones: MissionMilestone[]): MissionView => ({ ...emptyMissionView(), milestones });
const stream = (milestones: MissionMilestone[]) => <SessionIdContext.Provider value="s-trial">
  <WorkspaceRootsContext.Provider value={['/w']}>
    <EventStream events={chat} missionView={view(milestones)} connected showReasoning={false} onToggleReasoning={() => {}} />
  </WorkspaceRootsContext.Provider>
</SessionIdContext.Provider>;
const order = () => renderer!.root.findAll(node => typeof node.type === 'string' && (
  node.props['data-team-milestones'] !== undefined || node.props['data-conversation-ts'] !== undefined))
  .map(node => node.props['data-team-milestones'] !== undefined ? `milestones:${node.props['data-team-milestones']}` : `message:${node.props['data-conversation-ts']}`);

describe('the team milestones in the conversation', () => {
  it('shows what the team recorded between two messages, in time order, folding a long stretch', () => {
    act(() => { renderer = create(stream(TRIAL)); });

    expect(order()).toEqual(['message:100', 'message:101', 'milestones:7', 'message:300', 'message:301']);
    const group = renderer!.root.findByProps({ 'data-team-milestones': 7 });
    // The newest three and the latest verdict with its reason stay in view; the older three fold.
    const shown = () => group.findAll(node => node.props['data-milestone'] !== undefined && typeof node.type === 'string')
      .map(node => text(node));
    expect(shown()).toHaveLength(4);
    expect(shown()[0]).toContain('Review sent round 1 back for another pass');
    expect(text(group.findByProps({ 'data-milestone-detail': true }))).toBe(SENT_BACK);
    expect(text(group.findByProps({ 'data-milestones-folded': 3 }))).toBe('3 earlier updates folded · Show');
    act(() => group.findByProps({ 'data-milestones-folded': 3 }).props.onClick());
    expect(shown()).toHaveLength(7);
    // A figure the round wrote shows as itself; one in a dot folder the workspace does not serve stays a name.
    expect(group.findAll(node => node.props['data-milestone-figure'] !== undefined && typeof node.type === 'string')
      .map(node => node.props['data-milestone-figure'])).toEqual(['figures/ablation.png']);
  });

  it('keeps an opened stretch open while new milestones arrive on later polls', () => {
    act(() => { renderer = create(stream(TRIAL)); });
    act(() => renderer!.root.findByProps({ 'data-milestones-folded': 3 }).props.onClick());

    // The next poll: round 3 wrote a figure, and a milestone came after the latest reply.
    act(() => renderer!.update(stream([...TRIAL,
      row('files-3', 207, { kind: 'files', round: 3, paths: ['figures/curve.png'] }),
      row('review-3', 400, { kind: 'review', round: 3, status: 'done', source: 'reviewer', reason: 'Accepted.' }),
    ])));

    expect(order()).toEqual(['message:100', 'message:101', 'milestones:8', 'message:300', 'message:301', 'milestones:1']);
    const group = renderer!.root.findByProps({ 'data-team-milestones': 8 });
    // Still open: the same block, not a new one.
    expect(group.findAll(node => node.props['data-milestones-folded'] !== undefined)).toHaveLength(0);
    expect(group.findAll(node => node.props['data-milestone'] !== undefined && typeof node.type === 'string')).toHaveLength(8);
    expect(group.findAll(node => node.props['data-milestone-figure'] !== undefined && typeof node.type === 'string')
      .map(node => node.props['data-milestone-figure'])).toEqual(['figures/ablation.png', 'figures/curve.png']);
  });
});
