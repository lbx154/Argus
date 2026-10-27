import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DeliveryReceipt, LaunchHold, MissionView, Snapshot } from '../../../core/src/types';
import { ApiError } from '../../../core/src/http';
import { emptyMissionView } from '../../../core/src/missionView';

vi.mock('../i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../i18n')>();
  return {
    ...actual,
    translate: (key: string, variables?: Record<string, string | number>) => actual.translate(key, variables, 'zh-CN'),
    useI18n: () => ({ locale: 'zh-CN', setLocale: vi.fn(), t: (key: string, variables?: Record<string, string | number>) => actual.translate(key, variables, 'zh-CN') }),
  };
});

const { deliveryHeadingKey, isStagePackage } = await import('../lib/deliveryKind');
const { DEFAULT_LAUNCH_SETTINGS, describeRequirement, isFreshProject, launchSettingsSummary, stripLaunchNote } = await import('../lib/launch');
const { durationLabel, projectOverview, spendEstimateLabel } = await import('../lib/projectOverview');
const { projectAttention } = await import('../lib/attention');
const { errorText } = await import('../lib/format');
const { LaunchHoldCard, LaunchSettingsBar } = await import('../components/LaunchPanel');
const { ProjectOverviewStrip } = await import('../components/ProjectOverviewStrip');
const { TopBar } = await import('../components/TopBar');

afterEach(() => vi.unstubAllGlobals());

function snapshot(patch: Partial<Snapshot> = {}): Snapshot {
  return {
    session: { id: 's-1', display_name: '视频去噪', objective: '研究视频去噪', last_active: 1, cwd: '/state/s-1' },
    daemon: { alive: false, pid: null, uptime_seconds: null, backend: null },
    roles: [],
    backlog: [],
    recent_events: [],
    ...patch,
  } as Snapshot;
}

function delivery(kind: string): DeliveryReceipt {
  return {
    delivery_id: `d-${kind}`, item_id: 'a', kind, title: '第一轮实验结果', summary: '', delivered_at: 10,
    primary_target: { path: 'report.md', kind: 'markdown' }, targets: [], review_status: 'done', review_source: 'reviewer',
  } as unknown as DeliveryReceipt;
}

const hold: LaunchHold = {
  created_at: 100, item_id: 'a', title: '调研视频去噪', request: '帮我研究视频去噪', continuous: false,
  settings: { first_result_hours: 4, target_level: 'exploratory', deliverable: 'report', hold: true },
  understood: {
    objective: '帮我研究视频去噪\n\n【开工设置（我在界面上选的）】\n- 研究定位：探索性。',
    requirements: [{ text: 'research target level: publishable', authority: 'manager' }],
    assumptions: ['公开评测集 (rather than 自采数据)'],
  },
};

describe('stage packages read as interim results everywhere', () => {
  it('labels a stage package as such, never as a finished task', () => {
    expect(deliveryHeadingKey(delivery('stage_package'))).toBe('mission.stagePackage');
    expect(deliveryHeadingKey(delivery('submission_certified'))).toBe('mission.deliveryCertified');
    expect(deliveryHeadingKey(delivery('task_completed'))).toBe('mission.taskCompleted');
    expect(isStagePackage(delivery('stage_package'))).toBe(true);
  });
});

describe('launch settings and the confirm-before-start card', () => {
  it('offers settings only for a project that has not queued or run anything', () => {
    expect(isFreshProject(snapshot())).toBe(true);
    expect(isFreshProject(snapshot({ backlog: [{ id: 'a', title: 't', objective: 't', status: 'done', priority: 1 }] }))).toBe(false);
    expect(isFreshProject(snapshot({ launch_hold: hold }))).toBe(false);
    expect(isFreshProject(snapshot({ backlog: [{ id: 'a', title: 't', objective: 't', status: 'skipped', priority: 1 }] }))).toBe(true);
    expect(DEFAULT_LAUNCH_SETTINGS.hold).toBe(false); // product decision: ask only beyond authority by default
  });

  it('shows the request without the appended settings and marks Argus-judged requirements', () => {
    expect(stripLaunchNote(hold.understood!.objective!)).toBe('帮我研究视频去噪');
    expect(describeRequirement({ text: 'research target level: publishable', authority: 'manager' }, true))
      .toBe('目标级别：可发表（原创研究，通常耗时较长）（Argus 的判断，可调整）');
    expect(describeRequirement({ text: 'target venue: ICLR', authority: 'operator' }, true)).toBe('目标会议/期刊：ICLR');
    expect(launchSettingsSummary(hold.settings, true)).toBe('首个结果 4 小时内 · 探索性 · 报告');
  });

  it('renders what Argus understood, the queued plan and both actions', () => {
    const html = renderToStaticMarkup(<LaunchHoldCard hold={hold} onStart={async () => undefined} onRewrite={async () => undefined}
      backlog={[{ id: 'a', title: '收集近三年的视频去噪方法', objective: 'x', status: 'pending', priority: 1 }]} />);
    expect(html).toContain('计划已排好 · 等你确认后开始');
    expect(html).toContain('帮我研究视频去噪');
    expect(html).not.toContain('开工设置（我在界面上选的）');
    expect(html).toContain('公开评测集 (rather than 自采数据)');
    expect(html).toContain('收集近三年的视频去噪方法');
    expect(html).toContain('开始执行');
    expect(html).toContain('撤回并修改要求');
    const readOnly = renderToStaticMarkup(<LaunchHoldCard hold={hold} backlog={[]} readOnly onStart={async () => undefined} onRewrite={async () => undefined} />);
    expect(readOnly).not.toContain('开始执行');
  });

  it('shows the plan the Manager handed over and the whole goal, not four lines of it', () => {
    // Trial s-99ca9ad6: "the plan" was one queued title and the goal was cut to four lines.
    const planned = { ...hold, plan: { path: 'RL_RESEARCH_PLAN.md', summary: '1. 选 0.5B 模型\n2. 先复现 GRPO 基线' } };
    const html = renderToStaticMarkup(<LaunchHoldCard hold={planned} onStart={async () => undefined} onRewrite={async () => undefined}
      onOpenArtifact={() => undefined}
      backlog={[{ id: 'a', title: '收集近三年的视频去噪方法', objective: 'x', status: 'pending', priority: 1 }]} />);
    expect(html).toContain('1. 选 0.5B 模型\n2. 先复现 GRPO 基线');
    expect(html).toContain('查看完整计划');
    expect(html).not.toContain('收集近三年的视频去噪方法');
    expect(html).toContain('group-open:line-clamp-none');
    expect(html).toContain('展开全部');
  });

  it('reports a held plan as waiting for the operator, not as ready for new work', async () => {
    const { currentWorkStatus, workStatusLabel } = await import('../lib/workStatus');
    const status = currentWorkStatus(snapshot({ launch_hold: hold, backlog: [{ id: 'a', title: 't', objective: 't', status: 'pending', priority: 1 }] }), null, [], 200);
    expect(status.reason).toBe('launch_confirm');
    expect(workStatusLabel(status, 'zh-CN')).toBe('计划已排好，等你确认开始');
  });

  it('renders the settings bar with one ask choice, defaulting to asking only beyond authority', () => {
    const html = renderToStaticMarkup(<LaunchSettingsBar value={DEFAULT_LAUNCH_SETTINGS} onChange={() => undefined}
      ask="beyond_authority" onAskChange={() => undefined} research />);
    expect(html).toContain('开工设置');
    expect(html).toContain('多久先看到结果');
    expect(html).toContain('什么时候问我');
    expect(html).toContain('冲刺可发表（耗时长）');
    expect(html).not.toContain('type="checkbox"');
    expect(DEFAULT_LAUNCH_SETTINGS.hold).toBe(false);
  });
});

describe('project overview', () => {
  const view = { ...emptyMissionView(), mission: { ...emptyMissionView().mission, id: 'a', title: '研究视频去噪' }, delivery: delivery('stage_package') } as MissionView;
  const busy = snapshot({
    daemon: { alive: true, pid: 1, uptime_seconds: 10, backend: null },
    spend_usd: 12.345, spend_status: 'priced',
    backlog: [
      { id: 'a', title: 'one', objective: 'one', status: 'done', priority: 1, ts: 1000, finished_ts: 2000 },
      { id: 'b', title: 'two', objective: 'two', status: 'running', priority: 1, ts: 1500 },
      { id: 'c', title: 'three', objective: 'three', status: 'skipped', priority: 1, ts: 1500 },
    ],
  });

  it('counts tasks, elapsed time and cost', () => {
    const overview = projectOverview(busy, view, 1000 + 7200)!;
    expect(overview).toMatchObject({ done: 1, total: 2, active: 1, elapsedSeconds: 7200, goal: '研究视频去噪' });
    expect(durationLabel(7200, true)).toBe('2 小时');
    expect(durationLabel(90000, true)).toBe('1 天 1 小时');
    expect(spendEstimateLabel(overview, true)).toBe('估算 ≈$12.3（按 API 价）');
    expect(spendEstimateLabel({ spendUsd: 3, spendStatus: 'partial' }, true)).toBe('估算 ≥$3.00（按 API 价）');
    expect(projectOverview(snapshot(), view, 0)).toBeNull();
  });

  it('renders the newest result with its real kind and the alert toggle', () => {
    const html = renderToStaticMarkup(<ProjectOverviewStrip snapshot={busy} view={view}
      notifications={{ supported: true, enabled: false, denied: false, enable: async () => undefined, disable: () => undefined }} />);
    expect(html).toContain('任务 1/2 已完成');
    expect(html).toContain('<span class="shrink-0">阶段成果：</span>');
    expect(html).toContain('开启提醒');
  });
});

describe('attention signals', () => {
  it('puts a held launch and an open question before results', () => {
    expect(projectAttention({ snapshot: snapshot({ launch_hold: hold }), complete: true, zh: true })?.kind).toBe('reply');
    const asking = snapshot({ backlog: [{ id: 'q', title: '选数据集', objective: 'x', status: 'paused_operator', priority: 1, pending_question: '用哪个数据集？' }],
      pending_questions: [{ id: 'q', title: '选数据集', pending_question: '用哪个数据集？' }] });
    const reply = projectAttention({ snapshot: asking, complete: false, zh: true });
    expect(reply?.kind).toBe('reply');
    expect(reply?.body).toContain('用哪个数据集');
  });

  it('reports a finished task and a new stage result once each', () => {
    const view = { ...emptyMissionView(), mission: { ...emptyMissionView().mission, id: 'm1', title: 'T', status: 'done' } } as MissionView;
    expect(projectAttention({ snapshot: snapshot(), view, complete: true, zh: true })).toMatchObject({ kind: 'done', key: 'done:m1' });
    expect(projectAttention({ snapshot: snapshot(), complete: false, delivery: delivery('stage_package'), zh: true }))
      .toMatchObject({ kind: 'stage', key: 'stage:d-stage_package' });
    expect(projectAttention({ snapshot: snapshot(), complete: false, zh: true })).toBeNull();
  });
});

describe('plain error messages', () => {
  it('turns HTTP failures into sentences instead of request lines', () => {
    expect(errorText(new ApiError('GET /api/projects/s → 404', 404, 'GET', '/api/projects/s'))).toBe('要找的内容已不存在，可能被删除或移动了。');
    expect(errorText(new ApiError('POST /x → 409: Nothing is waiting to start.', 409, 'POST', '/x', '', 'Nothing is waiting to start.')))
      .toBe('Nothing is waiting to start.');
    expect(errorText(new ApiError('GET /x → 401', 401, 'GET', '/x'))).toContain('重新打开试用链接');
    expect(errorText(new ApiError('GET /x → 500', 500, 'GET', '/x'))).toBe('Argus 服务出了点问题，请稍后重试。');
    expect(errorText(new Error('plain'))).toBe('plain');
  });
});

describe('pause lets the current task finish; stop now is separate', () => {
  const running = snapshot({
    daemon: { alive: true, pid: 1, uptime_seconds: 10, backend: null, control_available: true },
    backlog: [{ id: 'a', title: 'one', objective: 'one', status: 'running', priority: 1 }],
  });

  it('offers a graceful pause and a separate stop-now control', () => {
    const html = renderToStaticMarkup(<TopBar snap={running} streamOk onStart={() => undefined} onStop={() => undefined}
      onPause={() => undefined} onManage={() => undefined} busy={false} />);
    expect(html).toContain('aria-label="暂停团队"');
    expect(html).toContain('aria-label="立即停止"');
  });

  it('shows the finishing state while a pause is pending', () => {
    const draining = { ...running, daemon: { ...running.daemon, draining: true } };
    const html = renderToStaticMarkup(<TopBar snap={draining} streamOk onStart={() => undefined} onStop={() => undefined}
      onPause={() => undefined} onManage={() => undefined} busy={false} />);
    expect(html).toContain('正在收尾');
    expect(html).toContain('aria-label="立即停止"');
  });
});
