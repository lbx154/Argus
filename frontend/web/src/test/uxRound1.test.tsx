import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DeliveryReceipt, MissionView, Snapshot } from '../../../core/src/types';
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
const { spendEstimateLabel } = await import('../lib/projectOverview');
const { projectAttention } = await import('../lib/attention');
const { errorText } = await import('../lib/format');
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

describe('stage packages read as interim results everywhere', () => {
  it('labels a stage package as such, never as a finished task', () => {
    expect(deliveryHeadingKey(delivery('stage_package'))).toBe('mission.stagePackage');
    expect(deliveryHeadingKey(delivery('submission_certified'))).toBe('mission.deliveryCertified');
    expect(deliveryHeadingKey(delivery('task_completed'))).toBe('mission.taskCompleted');
    expect(isStagePackage(delivery('stage_package'))).toBe(true);
  });
});

describe('spend estimate', () => {
  it('says the cost is an API-price equivalent', () => {
    expect(spendEstimateLabel({ spendUsd: 12.345, spendStatus: 'priced' }, true)).toBe('估算 ≈$12.3（按 API 价）');
    expect(spendEstimateLabel({ spendUsd: 3, spendStatus: 'partial' }, true)).toBe('估算 ≥$3.00（按 API 价）');
  });
});

describe('attention signals', () => {
  it('puts an open question before results', () => {
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

describe('a parked task can be resumed from the header', () => {
  const parked = (park: string) => snapshot({
    backlog: [{ id: 'a', title: '视频去噪', objective: 'x', status: 'parked', priority: 1,
      outcome: { execution_status: 'paused', review_status: 'not_assessed', stage_certification: 'not_assessed',
        resumable: true, park: { reason: park } } }],
  } as Partial<Snapshot>);

  it('shows Resume as the primary header action when nothing runs the parked work', () => {
    const html = renderToStaticMarkup(<TopBar snap={parked('provider_fence')} streamOk onStart={() => undefined}
      onStop={() => undefined} onManage={() => undefined} busy={false} />);
    expect(html).toContain('data-resume-work');
    expect(html).toContain('brand-button-primary');
    expect(html).toContain('继续运行');
  });

  it('does not offer Resume for a question only an answer ends', () => {
    const html = renderToStaticMarkup(<TopBar snap={parked('operator_question')} streamOk onStart={() => undefined}
      onStop={() => undefined} onManage={() => undefined} busy={false} />);
    expect(html).not.toContain('data-resume-work');
  });
});
