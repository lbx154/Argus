import type { EventMsg, MissionView, Snapshot, ProjectWorkStatus } from '../../../core/src/types';
import type { Locale } from '../i18n';

export type WorkStatus = Omit<ProjectWorkStatus, 'state'> & {
  state: ProjectWorkStatus['state'] | 'unknown';
  activityAgeSeconds: number | null;
};

/** Work that writes only Argus's own records (``.argus/…``): its failure is
 * Argus's housekeeping, not the person's work, and never undoes a result that
 * was delivered and reviewed (ux-principles rules 6 and 7). */
export function internalRecordsOnly(item: unknown): boolean {
  const owned = (item as { owns_paths?: unknown } | undefined)?.owns_paths;
  if (!Array.isArray(owned) || owned.length === 0) return false;
  return owned.every((path) => {
    const flat = String(path).replace(/\\/g, '/').replace(/^\.\//, '');
    return flat === '.argus' || flat.startsWith('.argus/');
  });
}

export function eventTaskId(event: EventMsg): string {
  return String(event.item_id || event.mission_id || '').split(':attempt:')[0];
}

/** The recorded start boundary for this task's current attempt and daemon run. */
export function currentWorkStartedAt(snapshot: Snapshot | undefined, view: MissionView | null | undefined, taskId: string): number {
  const task = snapshot?.backlog?.find(item => item.id === taskId);
  const boot = Date.parse(snapshot?.daemon.started_at_iso || '') / 1000;
  return Math.max(view?.mission.id === taskId ? view.mission.started_at || 0 : 0,
    task?.started_ts || 0, Number.isFinite(boot) ? boot : 0);
}

/** A completion belongs to its call or task, never to every concurrent task. */
export function activeProviderRequest(events: EventMsg[], notBefore = 0): EventMsg | null {
  const calls = new Map<string, EventMsg>();
  for (const event of events) {
    if (Number(event.ts || 0) < notBefore) continue;
    const type = String(event.type || ''), id = String(event.call_id || '');
    if (['provider.request.started', 'agent.io.start'].includes(type) && id) {
      if (!String(event.run_label || '').startsWith('map-summary')) calls.set(id, event);
    } else if (['provider.request.completed', 'provider.request.denied', 'provider.request.failed', 'agent.io.complete'].includes(type) && id) {
      calls.delete(id);
    } else if (['life.mission.completed', 'mission.completed', 'life.mission.failed'].includes(type)) {
      const task = eventTaskId(event);
      if (task) for (const [key, call] of calls) if (eventTaskId(call) === task) calls.delete(key);
    }
  }
  return [...calls.values()].at(-1) ?? null;
}

/** State and wording come from one server projection; the browser only dates its activity. */
export function currentWorkStatus(snapshot: Snapshot | undefined, nowSeconds = Date.now() / 1000): WorkStatus {
  const status = snapshot?.work_status;
  if (!status) return { state: 'unknown', reason: '', role: '', roles: [], taskId: '', title: '', activityAt: null, activityAgeSeconds: null, foreground: false, label: { 'zh-CN': '', en: '' } };
  return { ...status, activityAgeSeconds: status.activityAt === null ? null : Math.max(0, nowSeconds - status.activityAt) };
}

export function workStatusLabel(status: WorkStatus, locale: Locale): string {
  return status.label[locale === 'zh-CN' ? 'zh-CN' : 'en'];
}
