import { describe, expect, it } from 'vitest';
import type { Snapshot } from '../../../core/src/types';
import { currentWorkStatus, workStatusLabel } from '../lib/workStatus';
import { teamPhase } from '../components/TeamControls';
describe("a stopped task in the project header", () => {
  it("keeps the header from saying 已完成 beside a step that did not finish, when work is interrupted", () => {
    const snapshot = (): Snapshot => ({
      session: { id: "s-7d7b7d22", display_name: "", objective: "", workdir: "/w", last_active: 1 },
      daemon: { alive: false, pid: null, uptime_seconds: null, backend: null, resumable: true }, roles: [], work_status: { state: "paused", reason: "not_running", role: "", roles: [], taskId: "", title: "", foreground: false, activityAt: null, label: { "zh-CN": "执行中断：1 个任务暂停", en: "Interrupted: 1 task paused" } },
      backlog: [{ id: "last", title: "收尾", objective: "", status: "failed", priority: 1 } as Snapshot["backlog"][number]],
    }) as Snapshot;
    const failed = currentWorkStatus(snapshot(), 10);
    expect([teamPhase(failed), workStatusLabel(failed, "zh-CN")]).toEqual(["paused", "执行中断：1 个任务暂停"]);
  });
});
