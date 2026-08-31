import { useEffect, useMemo, useRef, useState } from 'react';
import type { ArtifactInfo, EventMsg } from '../api';
import type { DeliveryReceipt, MissionView } from '../../../core/src/types';
import { api } from '../api';
import { useArtifact } from '../hooks';
import { formatBytes } from '../lib/format';
import { Spinner } from './primitives';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faAnglesRight } from '@fortawesome/free-solid-svg-icons';
import { useGsapMotion } from '../lib/motion';
import { HtmlPreview } from './HtmlPreview';
import { JsonPreview, TablePreview } from './DataPreview';
import { MarkdownContent } from './MarkdownContent';
import { displayObjective, formatMissionElapsed } from '../../../core/src/missionView';
import { useI18n } from '../i18n';
import { PdfPreview } from './PdfPreview';

export const LIVE_PROGRESS_PATH = '__argus_live_progress__';

const ACTIVE_MISSION_STATUSES = new Set([
  'framed', 'grounding', 'queued', 'running', 'in_progress', 'working',
]);
const COMPLETE_MISSION_STATUSES = new Set(['complete', 'completed', 'done', 'success']);

export function missionIsComplete(view?: MissionView | null): boolean {
  return COMPLETE_MISSION_STATUSES.has(String(view?.mission.status || '').toLowerCase());
}

export function selectLiveArtifacts(artifacts?: ArtifactInfo[]): ArtifactInfo[] {
  return (artifacts ?? []).filter((item) => item.source === 'manager_live');
}

export function selectPreferredLiveArtifact(artifacts?: ArtifactInfo[]): ArtifactInfo | null {
  const live = selectLiveArtifacts(artifacts).filter((item) => item.exists);
  return live[0] ?? null;
}

export function selectPreviewArtifacts(artifacts?: ArtifactInfo[]): ArtifactInfo[] {
  const all = artifacts ?? [];
  const kindPriority: Record<string, number> = {
    markdown: 0, pdf: 1, html: 2, text: 3, table: 4, json: 5,
    image: 6, video: 7, audio: 8, binary: 9,
  };
  const existingDelivery = all.filter((item) => item.exists && item.source === 'delivery');
  const existingLive = all.filter((item) => item.exists && item.source === 'manager_live');
  const existing = all.filter(
    (item) => item.exists && item.source !== 'manager_live' && item.source !== 'delivery',
  );
  if (existing.length || existingDelivery.length) {
    return [
      ...existingDelivery,
      ...[...existing].sort(
        (left, right) => (kindPriority[left.kind] ?? 99) - (kindPriority[right.kind] ?? 99),
      ),
      ...existingLive,
    ];
  }
  return existingLive;
}

export function selectPreferredPreviewArtifact(artifacts?: ArtifactInfo[]): ArtifactInfo | null {
  return selectPreviewArtifacts(artifacts).find((item) => item.exists) ?? null;
}

export function selectCompletionArtifact(artifacts?: ArtifactInfo[]): ArtifactInfo | null {
  const visible = selectPreviewArtifacts(artifacts);
  return visible.find((item) => item.source === 'delivery')
    ?? visible.find((item) => item.source !== 'manager_live')
    ?? visible[0]
    ?? null;
}

function artifactLabel(item: ArtifactInfo): string {
  const parts = item.path.split('/');
  return parts[parts.length - 1] || item.path;
}

export function defaultPreviewPath(
  view?: MissionView | null,
  artifacts?: ArtifactInfo[],
): string {
  if (view) {
    const status = String(view.mission.status || '').toLowerCase();
    if (ACTIVE_MISSION_STATUSES.has(status)) return LIVE_PROGRESS_PATH;
    const delivered = view.delivery?.primary_target?.path;
    if (delivered) return delivered;
    if (missionIsComplete(view)) {
      return selectCompletionArtifact(artifacts)?.path ?? LIVE_PROGRESS_PATH;
    }
    const managerSelected = selectPreferredLiveArtifact(artifacts);
    if (managerSelected) return managerSelected.path;
    return LIVE_PROGRESS_PATH;
  }
  return selectPreferredPreviewArtifact(artifacts)?.path ?? '';
}

const ROLE_LABELS: Record<string, string> = {
  manager: 'Manager',
  planner: 'Planner',
  engineer: 'Engineer',
  reviewer: 'Reviewer',
};

export interface LiveMissionStatus {
  role: string;
  roleLabel: string;
  label: string;
  detail: string;
}

function eventRole(event: EventMsg): string {
  const explicit = String(event.agent_layer ?? event.actor ?? '');
  if (explicit === 'main') return 'engineer';
  if (explicit) return explicit;
  const type = String(event.type ?? '');
  if (type.startsWith('round.review') || type.startsWith('reviewer')) return 'reviewer';
  if (type.startsWith('life.planner')) return 'planner';
  if (type.startsWith('life.manager') || type.startsWith('manager')) return 'manager';
  return type.startsWith('engineer') || type.startsWith('round.') ? 'engineer' : '';
}

export function selectLiveMissionStatus(
  view?: MissionView | null,
  events: EventMsg[] = [],
): LiveMissionStatus | null {
  if (missionIsComplete(view)) return null;
  const role = String(view?.active_role ?? '');
  if (!role) return null;
  const roleView = view?.roles.find((candidate) => candidate.role === role);
  let detail = '';
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (eventRole(event) !== role) continue;
    const kind = String(event.kind ?? '');
    if (kind === 'reasoning') continue;
    const raw = String(event.text ?? event.action_summary ?? '').trim();
    if (!raw || raw.startsWith('{')) continue;
    detail = raw.split('\n')[0].slice(0, 240);
    break;
  }
  return {
    role,
    roleLabel: ROLE_LABELS[role] ?? role,
    label: roleView?.label || 'Working',
    detail,
  };
}

export function liveProgressSummary(view: MissionView): {
  title: string;
  dagProgress: string;
} {
  const activeNode = view.dag.find((node) => ['running', 'in_progress', 'claimed'].includes(node.status));
  const completed = view.dag.filter((node) => ['done', 'completed'].includes(node.status)).length;
  const total = view.dag.length;
  let fallbackTitle = 'Awaiting Planner';
  if (view.mission.status === 'idle') fallbackTitle = 'Ready for a new mission';
  else if (view.mission.status === 'complete') fallbackTitle = 'Mission complete';
  return {
    title: displayObjective(activeNode?.title || view.mission.title || fallbackTitle),
    dagProgress: total > 0 ? `${completed} / ${total} complete` : 'Not planned',
  };
}

function LiveProgressPreview({
  view,
  liveStatus,
  artifacts = [],
  onOpenArtifact,
}: {
  view: MissionView;
  liveStatus: LiveMissionStatus | null;
  artifacts?: ArtifactInfo[];
  onOpenArtifact: (path: string) => void;
}) {
  const { t } = useI18n();
  const summary = liveProgressSummary(view);
  const reviewedArtifacts = [...artifacts]
    .filter((item) => item.exists && item.source !== 'manager_live')
    .sort((left, right) => Number(right.mtime ?? 0) - Number(left.mtime ?? 0))
    .slice(0, 4);
  const recent = view.timeline.slice(-6).reverse();
  const delivery = view.delivery;
  const statusTone = (status: string) => (
    status === 'done' ? 'text-ok'
    : ['running', 'in_progress', 'claimed'].includes(status) ? 'text-blue-sky'
    : ['failed', 'blocked', 'rejected'].includes(status) ? 'text-err'
    : 'text-ink-faint'
  );

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-5 text-sm text-ink-dim scroll-thin">
      {delivery ? (
        <section className="mb-4 rounded-lg border border-ok/35 bg-ok/10 p-4">
          <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ok">
            {delivery.kind === 'submission_certified' ? '交付已认证' : '已完成'}
          </div>
          <h3 className="mt-2 text-base font-semibold leading-snug text-ink">{delivery.title}</h3>
          {delivery.summary ? <p className="mt-2 text-xs leading-5 text-ink-dim">{delivery.summary}</p> : null}
          {delivery.primary_target ? (
            <button
              type="button"
              onClick={() => onOpenArtifact(delivery.primary_target!.path)}
              title={artifacts.find((item) => item.path === delivery.primary_target!.path)?.storage_path || delivery.primary_target.path}
              className="mt-3 rounded border border-ok/40 bg-panel px-2.5 py-1.5 font-mono text-[10px] text-ok hover:border-ok"
            >
              打开成果 · {delivery.primary_target.label || delivery.primary_target.path}
            </button>
          ) : null}
        </section>
      ) : null}
      <section className="rounded-lg border border-blue-deep/30 bg-blue-deep/10 p-4">
        <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-blue-sky">{t('research.currentWork')}</div>
        <h3 className="mt-2 text-base font-semibold leading-snug text-ink">
          {summary.title}
        </h3>
        {liveStatus?.detail ? <p className="mt-2 leading-6 text-ink-dim">{liveStatus.detail}</p> : null}
        <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
          <div><span className="text-ink-faint">{t('mission.stage')}</span><div className="mt-1 font-medium capitalize text-blue-sky">{view.stage.label || view.stage.id || '—'}</div></div>
          <div><span className="text-ink-faint">{t('mission.campaign')}</span><div className="mt-1 font-mono text-ink">{formatMissionElapsed(view.mission.campaign_elapsed_seconds)}</div></div>
          <div><span className="text-ink-faint">{t('mission.round')}</span><div className="mt-1 font-mono text-ink">{view.round.current || '—'}{view.round.max ? ` / ${view.round.max}` : ''}</div></div>
          <div><span className="text-ink-faint">{t('research.dagProgress')}</span><div className="mt-1 font-mono text-ink">{summary.dagProgress}</div></div>
        </div>
      </section>

      <section className="mt-5">
        <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">{t('mission.researchDag')}</div>
        <div className="mt-2 space-y-2">
          {view.dag.map((node) => (
            <div key={node.id} className="rounded-md border border-line/60 bg-panel px-3 py-2.5">
              <div className="flex items-start gap-2">
                <span className={`mt-0.5 shrink-0 font-mono text-xs ${statusTone(node.status)}`}>
                  {node.status === 'done' ? '✓' : ['running', 'in_progress', 'claimed'].includes(node.status) ? '●' : '○'}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-medium leading-5 text-ink">{node.title}</div>
                  <div className={`mt-0.5 font-mono text-[10px] ${statusTone(node.status)}`}>{node.status}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {reviewedArtifacts.length ? (
        <section className="mt-5">
          <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">{t('research.verifiedOutputs')}</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {reviewedArtifacts.map((item) => (
              <button
                key={item.path}
                type="button"
                onClick={() => onOpenArtifact(item.path)}
                title={item.storage_path || item.path}
                className="rounded border border-line/70 bg-panel px-2.5 py-1.5 font-mono text-[10px] text-blue-sky hover:border-blue/60"
              >
                {artifactLabel(item)} ↗
              </button>
            ))}
          </div>
        </section>
      ) : null}

      {recent.length ? (
        <section className="mt-5">
          <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">{t('research.recentMilestones')}</div>
          <div className="mt-2 space-y-2 border-l border-line/70 pl-3">
            {recent.map((item) => (
              <div key={item.id}>
                <div className="text-xs font-medium text-ink">{item.title}</div>
                {item.detail ? <div className="mt-0.5 line-clamp-2 text-xs leading-5 text-ink-faint">{item.detail}</div> : null}
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}

export function ResearchCanvas({
  sid,
  artifacts,
  error = false,
  onExpand,
  className = '',
  embedded = false,
  onCollapse,
  missionView,
  activityEvents = [],
  requestedPath,
  requestedPathToken,
}: {
  sid: string | null;
  artifacts?: ArtifactInfo[];
  error?: boolean;
  onExpand: (path: string) => void;
  className?: string;
  embedded?: boolean;
  onCollapse?: () => void;
  missionView?: MissionView | null;
  activityEvents?: EventMsg[];
  requestedPath?: string | null;
  requestedPathToken?: number;
}) {
  const { t, locale } = useI18n();
  const previewArtifacts = useMemo(
    () => selectPreviewArtifacts(artifacts),
    [artifacts],
  );
  const preferred = useMemo(() => selectPreferredPreviewArtifact(artifacts), [artifacts]);
  const completed = missionIsComplete(missionView);
  const completionArtifact = useMemo(() => {
    if (!completed) return null;
    const receiptPath = missionView?.delivery?.primary_target?.path;
    return previewArtifacts.find((item) => item.path === receiptPath && item.exists)
      ?? selectCompletionArtifact(artifacts);
  }, [artifacts, completed, missionView?.delivery?.primary_target?.path, previewArtifacts]);
  const [manualPath, setManualPath] = useState<string | null>(null);

  useEffect(() => {
    // New/running work always returns to the truthful event-sourced progress
    // view. Once that same mission becomes terminal, select its best real file.
    setManualPath(completed && completionArtifact ? completionArtifact.path : null);
  }, [completed, completionArtifact?.path, missionView?.mission.id, sid]);
  useEffect(() => {
    if (!requestedPath) return;
    const target = previewArtifacts.find(
      (item) => item.path === requestedPath && item.exists,
    );
    if (target) setManualPath(target.path);
  }, [previewArtifacts, requestedPath, requestedPathToken]);

  const effectivePath = manualPath ?? defaultPreviewPath(missionView, artifacts);
  const showLiveProgress = effectivePath === LIVE_PROGRESS_PATH;
  const selected = showLiveProgress
    ? null
    : previewArtifacts.find((item) => item.path === effectivePath)
      ?? (completed ? completionArtifact : preferred);
  const artifactQ = useArtifact(
    sid,
    selected?.exists ? selected.path : null,
    selected?.mtime ?? null,
  );
  const info = artifactQ.data;
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState('');
  const [downloading, setDownloading] = useState(false);
  const previewRef = useRef<HTMLDivElement>(null);
  const [downloadError, setDownloadError] = useState('');
  const liveStatus = useMemo(
    () => selectLiveMissionStatus(missionView, activityEvents),
    [activityEvents, missionView],
  );

  useEffect(() => {
    setPreviewUrl(null);
    setPreviewError('');
    if (!sid || !selected || !info || !['image', 'pdf', 'audio', 'video'].includes(info.kind)) return;
    let alive = true;
    let objectUrl = '';
    const controller = new AbortController();
    api.artifactBlob(sid, selected.path, false, controller.signal).then(
      (blob) => {
        if (!alive) return;
        objectUrl = URL.createObjectURL(blob);
        setPreviewUrl(objectUrl);
      },
      (loadError: Error) => alive && setPreviewError(loadError.message),
    );
    return () => {
      alive = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [sid, selected?.path, info?.kind, info?.mtime]);

  const delivery: DeliveryReceipt | null = missionView?.delivery ?? null;
  const deliveryTargetPath = delivery?.primary_target?.path ?? '';
  const showingDelivery = Boolean(
    completed
    && !showLiveProgress
    && selected
    && (selected.source === 'delivery' || selected.path === deliveryTargetPath),
  );
  const deliveryLabel = locale === 'zh-CN'
    ? (delivery?.kind === 'submission_certified' ? '交付已认证' : '已完成')
    : (delivery?.kind === 'submission_certified' ? 'Certified delivery' : 'Delivered result');
  const title = showingDelivery
    ? deliveryLabel
    : showLiveProgress
      ? t('research.liveProgress')
      : previewArtifacts[0]?.group_title || t('research.artifact');
  const download = async () => {
    if (!sid || !selected) return;
    setDownloading(true);
    setDownloadError('');
    try {
      const blob = await api.artifactBlob(sid, selected.path, true);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = selected.name;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (downloadError) {
      setDownloadError((downloadError as Error).message);
    } finally {
      setDownloading(false);
    }
  };
  useGsapMotion(previewRef, (gsap, reduceMotion) => {
    if (!previewRef.current) return;
    if (reduceMotion) return;
    gsap.fromTo(
      previewRef.current,
      { autoAlpha: 0, y: 6, scale: 0.995 },
      {
        autoAlpha: 1,
        y: 0,
        scale: 1,
        duration: 0.3,
        ease: 'power2.out',
        clearProps: 'transform,opacity,visibility',
      },
    );
  }, [showLiveProgress, selected?.path, info?.kind]);

  return (
    <section className={`glass-panel glass-panel--side flex min-h-0 flex-col overflow-hidden ${embedded ? '' : 'rounded-lg border'} ${className}`} aria-label={t('research.canvas')}>
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line/50 bg-panel px-4">
        <div className="flex min-w-0 shrink-0 items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${showingDelivery ? 'bg-ok' : 'animate-pulse bg-blue'}`} />
          <h2 className="max-w-24 truncate text-sm font-semibold text-ink sm:max-w-48">{title}</h2>
        </div>
        {missionView || previewArtifacts.length > 0 ? (
          <label className="min-w-0 flex-1">
            <span className="sr-only">{t('research.previewArtifact')}</span>
            <select
              value={showLiveProgress ? LIVE_PROGRESS_PATH : selected?.path ?? ''}
              onChange={(event) => setManualPath(event.target.value)}
              title={showLiveProgress ? t('research.liveProgress') : selected?.storage_path || selected?.path}
              className="h-8 w-full min-w-0 max-w-64 truncate rounded-md border border-line/50 bg-bg px-2 font-mono text-xs text-ink-dim outline-none focus:border-blue/60"
            >
              {missionView ? <option value={LIVE_PROGRESS_PATH}>{t('research.liveProgress')}</option> : null}
              {previewArtifacts.map((item) => (
                <option key={item.path} value={item.path} disabled={!item.exists} title={item.storage_path || item.path}>
                  {item.source === 'delivery' ? '交付 · ' : item.source === 'manager_live' ? 'Checkpoint · ' : ''}{artifactLabel(item)}{item.exists ? '' : ' · pending'}
                </option>
              ))}
            </select>
          </label>
        ) : <div className="flex-1" />}
        <div className="shrink-0">
          {selected ? (
            <div className="flex shrink-0 items-center gap-1">
              <button
                type="button"
                onClick={() => void download()}
                disabled={downloading || !selected.exists}
                title={t('artifact.download')}
                aria-label={t('artifact.download')}
                className="flex h-7 w-7 items-center justify-center rounded-md text-ink-faint hover:bg-surface hover:text-ink disabled:opacity-40"
              >
                <svg viewBox="0 0 16 16" aria-hidden="true" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.25">
                  <path d="M8 2.25v7.5M5.25 7.5 8 10.25 10.75 7.5M3 13.25h10" />
                </svg>
              </button>
              <button
                type="button"
                onClick={() => onExpand(selected.path)}
                title={t('research.openLarge')}
                aria-label={t('research.openLarge')}
                className="flex h-7 w-7 items-center justify-center rounded-md text-ink-faint hover:bg-surface hover:text-ink"
              >
                <svg viewBox="0 0 16 16" aria-hidden="true" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.25">
                  <path d="M6 3H3v3M10 3h3v3M6 13H3v-3M10 13h3v-3" />
                </svg>
              </button>
            </div>
          ) : null}
        </div>
        {onCollapse ? (
          <button type="button" onClick={onCollapse} aria-label={t('research.collapse')} title={t('research.collapse')} className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line/50 bg-bg/40 text-ink-faint hover:border-blue/50 hover:text-ink lg:flex">
            <FontAwesomeIcon icon={faAnglesRight} className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </header>

      {liveStatus ? (
        <div className="shrink-0 border-b border-line/50 bg-blue-deep/10 px-4 py-3">
          <div className="flex items-center gap-2 text-xs">
            <span className="h-2 w-2 animate-pulse rounded-full bg-blue" />
            <span className="font-semibold text-ink">{liveStatus.roleLabel}</span>
            <span className="text-blue-sky">{t('mission.active')}</span>
            <span className="truncate text-ink-faint">· {liveStatus.label}</span>
          </div>
          {liveStatus.detail ? (
            <p className="mt-1 line-clamp-2 text-xs leading-5 text-ink-dim">{liveStatus.detail}</p>
          ) : null}
        </div>
      ) : null}

      <div ref={previewRef} key={showLiveProgress ? LIVE_PROGRESS_PATH : selected?.path ?? 'empty'} className="relative flex min-h-0 flex-1 flex-col bg-bg">
        {showLiveProgress && missionView ? (
          <LiveProgressPreview view={missionView} liveStatus={liveStatus} artifacts={artifacts} onOpenArtifact={setManualPath} />
        ) : null}
        {!showLiveProgress && error ? (
          <div className="m-auto max-w-sm px-6 text-center text-sm text-warn">
            {t('research.unavailable')}
          </div>
        ) : null}
        {!showLiveProgress && !error && previewArtifacts.length === 0 ? (
          <div className="m-auto max-w-sm px-8 text-center">
            <div className="text-3xl text-ink-faint">◇</div>
            <h3 className="mt-3 text-xs text-ink-faint">{t('research.noPreview')}</h3>
          </div>
        ) : null}
        {!showLiveProgress && !error && previewArtifacts.length > 0 && !selected ? (
          <div className="m-auto max-w-sm px-8 text-center">
            <Spinner />
            <p className="mt-3 text-xs text-ink-faint">{t('research.waiting')}</p>
          </div>
        ) : null}
        {selected && !selected.exists ? (
          <div className="m-auto max-w-sm px-8 text-center">
            <Spinner />
            <p className="mt-3 text-xs text-ink-faint">{t('research.updating')}</p>
          </div>
        ) : null}
        {selected?.exists && artifactQ.isLoading ? <div className="m-auto"><Spinner /></div> : null}
        {selected?.exists && artifactQ.isError ? (
          <div className="m-auto px-6 text-center text-sm text-err">
            {t('artifact.unavailable')} · {(artifactQ.error as Error).message}
          </div>
        ) : null}
        {info?.kind === 'text' ? (
          <pre className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto whitespace-pre-wrap break-words p-5 font-mono text-xs leading-6 text-ink-dim scroll-thin">
            {info.preview || '(empty file)'}
            {info.truncated ? '\n\n… live preview truncated · expand to inspect the complete file' : ''}
          </pre>
        ) : null}
        {info?.kind === 'markdown' ? (
          <div className="min-h-0 flex-1 overflow-auto p-5 text-sm text-ink-dim scroll-thin">
            <MarkdownContent artifacts={artifacts} onOpenArtifact={setManualPath}>{info.preview || '(empty file)'}</MarkdownContent>
          </div>
        ) : null}
        {info?.kind === 'json' ? <JsonPreview value={info.preview || ''} /> : null}
        {info?.kind === 'table' ? (
          <TablePreview value={info.preview || ''} delimiter={info.name.endsWith('.tsv') ? '\t' : ','} />
        ) : null}
        {info?.kind === 'html' && !info.truncated ? (
          <HtmlPreview html={info.preview || ''} title={`Live HTML preview: ${info.name}`} />
        ) : null}
        {info?.kind === 'html' && info.truncated ? (
          <div className="m-auto max-w-sm px-8 text-center text-sm text-warn">
            {t('artifact.htmlTooLarge')}
          </div>
        ) : null}
        {info?.kind === 'image' && previewUrl ? (
          <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden p-4">
            <img src={previewUrl} alt={info.why || info.name} className="max-h-full max-w-full object-contain" />
          </div>
        ) : null}
        {info?.kind === 'pdf' && previewUrl ? (
          <PdfPreview src={previewUrl} name={info.name} />
        ) : null}
        {info?.kind === 'audio' && previewUrl ? (
          <div className="m-auto w-full max-w-xl px-6">
            <audio controls preload="metadata" src={previewUrl} className="w-full" />
          </div>
        ) : null}
        {info?.kind === 'video' && previewUrl ? (
          <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden bg-black p-2">
            <video controls playsInline preload="metadata" src={previewUrl} className="max-h-full max-w-full" />
          </div>
        ) : null}
        {info?.kind === 'binary' ? (
          <div className="m-auto max-w-sm px-8 text-center text-sm text-ink-dim">
            {t('research.fileUnavailable')}
          </div>
        ) : null}
        {info && ['image', 'pdf', 'audio', 'video'].includes(info.kind) && !previewUrl && !previewError ? (
          <div className="m-auto"><Spinner /></div>
        ) : null}
        {previewError ? <div className="m-auto px-6 text-center text-sm text-err">{previewError}</div> : null}
      </div>

      {showLiveProgress ? (
        <footer className="flex h-9 items-center gap-2 border-t border-line px-4 font-mono text-xs text-ink-faint">
          <span className="min-w-0 flex-1 truncate">{t('research.eventSourced')}</span>
          <span className="shrink-0 text-ok">{showingDelivery ? deliveryLabel : t('common.live')}</span>
        </footer>
      ) : info ? (
        <footer className="flex h-9 items-center gap-2 border-t border-line px-4 font-mono text-xs text-ink-faint">
          <span className="min-w-0 flex-1 truncate" title={info.storage_path || info.path}>{info.storage_path || info.path}</span>
          {downloadError ? <span className="ml-auto truncate text-err" title={downloadError}>{t('research.downloadFailed')}</span> : null}
          <span className="shrink-0">{info.kind} · {formatBytes(info.size)}</span>
          <span className="shrink-0 text-ok">{showingDelivery ? deliveryLabel : t('common.live')}</span>
        </footer>
      ) : null}
    </section>
  );
}
