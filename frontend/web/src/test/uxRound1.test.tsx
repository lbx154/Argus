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
    expect(errorText(new ApiError('GET /api/projects/s → 404', 404, 'GET', '/api/projects/s'))).toBe('要找的内容已不存在，可能被删除或移动了。请刷新列表后重新选择。');
    expect(errorText(new ApiError('POST /x → 409: Nothing is waiting to start.', 409, 'POST', '/x', '', 'Nothing is waiting to start.')))
      .toBe('当前状态还不能执行这个操作。请刷新页面，确认项目状态后再试。');
    expect(errorText(new ApiError('GET /x → 401', 401, 'GET', '/x'))).toContain('重新打开试用链接');
    expect(errorText(new ApiError('GET /x → 500', 500, 'GET', '/x'))).toBe('Argus 服务出了点问题，请稍后重试。');
    expect(errorText(new Error('plain'))).toBe('操作未能完成。请重试；如果仍然失败，请查看错误详情并联系维护者。');
  });
});

describe('one Stop, in the header, while anything is under way', () => {
  const running = snapshot({
    daemon: { alive: true, pid: 1, uptime_seconds: 10, backend: null },
    backlog: [{ id: 'a', title: 'one', objective: 'one', status: 'running', priority: 1 }],
  });

  it('offers Stop while the team works or a reply is awaited, and neither pause nor resume', () => {
    const header = (snap: Snapshot, pending = false) => renderToStaticMarkup(<TopBar snap={snap} streamOk onStart={() => undefined}
      onStop={() => undefined} pending={pending} onManage={() => undefined} busy={false} />);
    for (const html of [header(running), header({ ...running, daemon: { ...running.daemon, alive: false } }, true)]) {
      expect(html.match(/data-stop-work/g)).toHaveLength(1);
      expect(html).not.toMatch(/暂停|立即停止|data-resume-work/);
    }
  });
});

describe('work stopped short can be resumed from the header', () => {
  const stopped = (resumable: boolean) => snapshot({
    daemon: { alive: false, pid: null, uptime_seconds: null, backend: null, resumable },
    backlog: [{ id: 'a', title: '视频去噪', objective: 'x', status: resumable ? 'in_progress' : 'done', priority: 1 }],
  } as Partial<Snapshot>);

  it('shows Resume as the primary header action when the server says there is work to take up', () => {
    const html = renderToStaticMarkup(<TopBar snap={stopped(true)} streamOk onStart={() => undefined}
      onStop={() => undefined} onManage={() => undefined} busy={false} />);
    expect(html).toContain('data-resume-work');
    expect(html).toContain('brand-button-primary');
    expect(html).toContain('继续运行');
  });

  it('offers no run or resume on finished work', () => {
    const html = renderToStaticMarkup(<TopBar snap={stopped(false)} streamOk onStart={() => undefined}
      onStop={() => undefined} onManage={() => undefined} busy={false} />);
    expect(html).not.toContain('data-resume-work');
    expect(html).not.toMatch(/继续|启动|Run|Resume/);
  });
});
