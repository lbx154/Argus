import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  ChevronDown, ChevronUp, Download, Layers, Loader2, Power, PowerOff, RefreshCw, Search, Store, Trash2, TriangleAlert, X,
} from 'lucide-react';
import { api, VERTICAL_STORE_CAPABILITY } from '../api';
import type { VerticalAction, VerticalRow, VerticalsPayload } from '../../../core/src/types';
import { useI18n } from '../i18n';
import {
  EMPTY_FILTER, KIND_FILTERS, VERTICAL_ACTIONS, actionLabelKey, anyRunning, deriveTags, failureDetail, filterLabelKey,
  filterRows, formatSize, hasStoreCapability, hostedTrialBuild, isRunning, isStoreConflict, isStoreMissing, kindLabelKey,
  knownNames, operationLabelKey, pollInterval, progressPercent, purposeText, visibleActions, type StoreFilter,
} from '../lib/verticalStore';

/**
 * The vertical store: every research vertical this Argus knows about, on one
 * card each, with exactly the lifecycle buttons the server allows right now.
 * Same modal shell and polling rhythm as the plugin center.
 */

export type CardFailure = { text: string; offerForce: boolean };
type Act = (row: VerticalRow, action: VerticalAction, options?: { force?: boolean }) => Promise<boolean>;
/** A page-level failure: reading the list (cleared by the next good read) or refreshing the catalog (stays until the next refresh). */
type StoreFailure = { text: string; source: 'load' | 'refresh' };

const control = 'inline-flex items-center justify-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm transition-colors hover:bg-bg disabled:cursor-not-allowed disabled:opacity-45';
const chip = (active: boolean) => `rounded-full border px-2.5 py-0.5 text-xs transition-colors ${active ? 'border-blue/60 bg-blue/10 text-blue' : 'border-line text-ink-dim hover:border-blue/40 hover:text-ink'}`;
const badge = (tone: 'neutral' | 'ok' | 'warn' | 'blue') => `rounded px-1.5 py-0.5 text-[10px] font-semibold ${
  tone === 'ok' ? 'bg-ok/10 text-ok' : tone === 'warn' ? 'bg-warn/10 text-warn' : tone === 'blue' ? 'bg-blue/10 text-blue' : 'bg-line text-ink-dim'}`;
const actionIcon: Record<VerticalAction, typeof Download> = { install: Download, update: RefreshCw, enable: Power, disable: PowerOff, uninstall: Trash2 };

function whenText(iso: string, locale: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  try {
    return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
  } catch {
    return date.toISOString();
  }
}

// The sidebar entry, next to the plugin center. It only opens the store; the
// store itself is rendered once by the application shell.
export function VerticalStoreEntry({ compact = false, onOpen }: { compact?: boolean; onOpen: () => void }) {
  const { t } = useI18n();
  return <button type="button" onClick={onOpen} title={t('verticals.entry')} aria-label={t('verticals.entry')} data-testid="vertical-store-entry"
    className={`mx-2 my-1 flex h-9 shrink-0 items-center rounded-md text-sm text-ink-dim transition-colors hover:bg-bg hover:text-ink ${compact ? 'justify-center' : 'gap-2 px-3'}`}>
    <Store size={17} strokeWidth={1.5} />{!compact && <span>{t('verticals.entry')}</span>}
  </button>;
}

// One vertical. The buttons come from row.actions and nowhere else; a hosted
// workspace additionally loses install, update and uninstall.
export function VerticalCard({ row, locale, hosted, busy, failure, known, onAct, onJump, onTag }: {
  row: VerticalRow; locale: string; hosted: boolean; busy: boolean; failure?: CardFailure; known: ReadonlySet<string>;
  onAct: Act; onJump?: (name: string) => void; onTag?: (tag: string) => void;
}) {
  const { t } = useI18n();
  const [confirming, setConfirming] = useState(false);
  const [showProjects, setShowProjects] = useState(false);
  const running = isRunning(row);
  const disabled = busy || running;
  const actions = visibleActions(row, hosted);
  const operation = row.operation;
  const percent = progressPercent(operation);
  const operationName = (action: string) => VERTICAL_ACTIONS.includes(action as VerticalAction) ? t(actionLabelKey(action)) : action;
  const runningText = operation && VERTICAL_ACTIONS.includes(operation.action as VerticalAction)
    ? t(operationLabelKey(operation.action)) : t('verticals.operationRunning');
  const kindTone = row.kind === 'installed' ? 'ok' : row.kind === 'available' ? 'neutral' : 'blue';
  const uninstall = async (force = false) => {
    setConfirming(false);
    await onAct(row, 'uninstall', force ? { force: true } : undefined);
  };
  return <article id={`vertical-${row.name}`} data-testid={`vertical-${row.name}`} data-kind={row.kind} tabIndex={-1}
    className="flex flex-col rounded-xl border border-line/70 p-4 outline-none focus-visible:border-blue/60">
    <div className="flex items-start gap-3">
      <Layers size={22} strokeWidth={1.25} className="mt-0.5 shrink-0 text-blue" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="font-medium">{row.name}</h3>
          <span className={badge(kindTone)} data-testid="vertical-kind">{t(kindLabelKey(row.kind))}</span>
          {row.kind !== 'available' && <span className={badge(row.enabled ? 'ok' : 'neutral')}>{row.enabled ? t('verticals.enabled') : t('verticals.disabledState')}</span>}
          {row.update_available && <span className={badge('warn')} data-testid="vertical-update">{t('verticals.updateAvailable')}</span>}
        </div>
        <p className="mt-1 text-sm leading-relaxed text-ink-faint">{purposeText(row, locale)}</p>
      </div>
    </div>
    <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-faint">
      {row.installed_version && <span>{t('verticals.installedVersion', { version: row.installed_version })}</span>}
      {row.version && row.version !== row.installed_version && <span>{t('verticals.catalogVersion', { version: row.version })}</span>}
      {row.size_bytes != null && <span>{t('verticals.size')} {formatSize(row.size_bytes)}</span>}
    </div>
    {row.requires.length > 0 && <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs">
      <span className="text-ink-faint">{t('verticals.requires')}</span>
      {row.requires.map((name) => known.has(name) && onJump
        ? <button type="button" key={name} onClick={() => onJump(name)} aria-label={t('verticals.jumpTo', { name })} title={t('verticals.jumpTo', { name })}
            className="rounded-full border border-line px-2 py-0.5 font-mono text-ink-dim transition-colors hover:border-blue/60 hover:text-blue">{name}</button>
        : <span key={name} className="rounded-full border border-line/50 px-2 py-0.5 font-mono text-ink-faint">{name}</span>)}
    </div>}
    {row.shared.length > 0 && <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
      <span className="text-ink-faint">{t('verticals.shared')}</span>
      {row.shared.map((name) => <span key={name} className="rounded-full border border-line/50 px-2 py-0.5 font-mono text-ink-faint">{name}</span>)}
    </div>}
    {row.tags.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">
      {row.tags.map((tag) => onTag
        ? <button type="button" key={tag} onClick={() => onTag(tag)} className="rounded-full bg-bg px-2 py-0.5 text-[11px] text-ink-faint transition-colors hover:text-blue">#{tag}</button>
        : <span key={tag} className="rounded-full bg-bg px-2 py-0.5 text-[11px] text-ink-faint">#{tag}</span>)}
    </div>}
    {row.missing_python.length > 0 && <p role="status" className="mt-3 flex items-start gap-2 rounded-lg bg-warn/10 p-2.5 text-xs leading-relaxed text-warn" data-testid="vertical-missing-python">
      <TriangleAlert size={14} className="mt-0.5 shrink-0" />
      <span>{t('verticals.missingPython', { packages: row.missing_python.join(', ') })}</span>
    </p>}
    {row.used_by.length > 0 && <div className="mt-3 text-xs text-ink-faint">
      <button type="button" aria-expanded={showProjects} onClick={() => setShowProjects((value) => !value)} className="inline-flex items-center gap-1 transition-colors hover:text-ink" data-testid="vertical-used-by">
        {row.used_by.length === 1 ? t('verticals.usedByOne') : t('verticals.usedBy', { count: row.used_by.length })}
        {showProjects ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
      </button>
      {showProjects && <ul className="mt-1.5 flex flex-wrap gap-1 font-mono">
        {row.used_by.map((sid) => <li key={sid} className="rounded bg-bg px-1.5 py-0.5">{sid}</li>)}
      </ul>}
    </div>}
    {running && operation && <div className="mt-3" data-testid="vertical-progress">
      <p role="status" className="flex items-center gap-2 text-sm text-ink-dim">
        <Loader2 size={14} className="animate-spin" />
        <span>{runningText}</span>
        {percent != null && <span className="font-mono text-xs tabular-nums text-ink-faint">{percent}%</span>}
      </p>
      <div role="progressbar" aria-label={runningText} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent ?? undefined}
        className="mt-1.5 h-1 overflow-hidden rounded bg-line">
        {percent != null
          ? <div className="h-full rounded bg-blue transition-[width] duration-500" style={{ width: `${percent}%` }} />
          : <div className="h-full w-1/2 animate-pulse rounded bg-blue" />}
      </div>
      {operation.message && <p className="mt-1 break-words text-xs text-ink-faint">{operation.message}</p>}
    </div>}
    {operation?.status === 'failed' && <p role="status" className="mt-3 break-words text-sm text-err" data-testid="vertical-operation-failed">
      {t('verticals.operationFailed', { action: operationName(operation.action) })}{operation.message ? ` — ${operation.message}` : ''}
    </p>}
    {failure && <div role="alert" className="mt-3 rounded-lg border border-err/30 bg-err/5 p-2.5 text-sm text-ink-dim" data-testid="vertical-failure">
      <p className="break-words">{failure.text}</p>
      {failure.offerForce && <button type="button" className={`${control} mt-2 border-err/50 text-err hover:bg-err/10`} disabled={disabled} onClick={() => void uninstall(true)}>
        <Trash2 size={14} />{t('verticals.removeAnyway')}
      </button>}
    </div>}
    {actions.length > 0 && <div className="mt-4 flex flex-wrap items-center gap-2">
      {actions.map((action) => {
        const Icon = actionIcon[action];
        if (action === 'uninstall') {
          return confirming
            ? <span key={action} className="flex flex-wrap items-center gap-2">
                <button type="button" className={`${control} border-err/50 text-err hover:bg-err/10`} disabled={disabled} onClick={() => void uninstall()} data-testid="vertical-confirm-uninstall">
                  <Trash2 size={14} />{t('verticals.confirmUninstall')}
                </button>
                <button type="button" className="text-xs text-ink-faint hover:text-ink" onClick={() => setConfirming(false)}>{t('common.cancel')}</button>
                <span className="text-xs text-ink-faint">{t('verticals.confirmUninstallHint', { name: row.name })}</span>
              </span>
            : <button key={action} type="button" className={control} disabled={disabled} onClick={() => setConfirming(true)} data-action={action}>
                <Icon size={14} />{t(actionLabelKey(action))}
              </button>;
        }
        const label = action === 'update' && row.version ? t('verticals.updateTo', { version: row.version }) : t(actionLabelKey(action));
        return <button key={action} type="button" className={control} disabled={disabled} onClick={() => void onAct(row, action)} data-action={action}>
          <Icon size={14} />{label}
        </button>;
      })}
    </div>}
  </article>;
}

// The page inside the modal: catalog line, filters and the card grid. Pure
// with respect to data so the tests can render it without a network.
export function VerticalStoreView({ payload, filter, onFilter, hosted, supported, loading, refreshing, error, pending, failures, onAct, onRefresh, onJump }: {
  payload: VerticalsPayload | null; filter: StoreFilter; onFilter: (next: StoreFilter) => void; hosted: boolean;
  supported: boolean | null; loading: boolean; refreshing: boolean; error: string; pending: string | null;
  failures: Record<string, CardFailure>; onAct: Act; onRefresh: () => void; onJump: (name: string) => void;
}) {
  const { t, locale } = useI18n();
  const rows = payload?.verticals ?? [];
  const tags = useMemo(() => deriveTags(rows), [rows]);
  const known = useMemo(() => knownNames(rows), [rows]);
  const shown = useMemo(() => filterRows(rows, filter), [rows, filter]);
  const filtered = filter.query.trim() !== '' || filter.kind !== 'all' || filter.tag !== null;
  const catalog = payload?.catalog;
  return <>
    <p className="mt-2 text-sm text-ink-faint">{hosted ? t('verticals.hostedNote') : t('verticals.intro')}</p>
    {supported === false && <p role="alert" className="mt-4 rounded-lg border border-line bg-bg/70 p-3 text-sm text-ink-dim" data-testid="vertical-store-too-old">{t('verticals.tooOld')}</p>}
    {supported !== false && <>
      {catalog && <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-faint" data-testid="vertical-catalog">
        <span className="min-w-0 truncate" title={catalog.source}>{t('verticals.catalogSource', { source: catalog.source })}</span>
        {catalog.release_tag && <span className="rounded bg-bg px-1.5 py-0.5 font-mono">{t('verticals.catalogRelease', { tag: catalog.release_tag })}</span>}
        <span>{catalog.fetched_at ? t('verticals.catalogFetched', { time: whenText(catalog.fetched_at, locale) }) : t('verticals.catalogNever')}</span>
        {!hosted && <button type="button" className={`${control} ml-auto`} disabled={refreshing || loading} onClick={onRefresh} data-testid="vertical-refresh-catalog">
          <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />{refreshing ? t('verticals.refreshing') : t('verticals.refreshCatalog')}
        </button>}
      </div>}
      {catalog?.error && <p role="alert" className="mt-3 rounded-lg border border-warn/40 bg-warn/10 p-2.5 text-sm text-warn" data-testid="vertical-catalog-error">{t('verticals.catalogError', { error: catalog.error })}</p>}
      {error && <p role="alert" className="mt-3 text-sm text-err" data-testid="vertical-store-error">{error}</p>}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <label className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg border border-line bg-bg px-2.5 focus-within:border-blue/60">
          <Search size={14} className="shrink-0 text-ink-faint" />
          <span className="sr-only">{t('verticals.search')}</span>
          <input type="search" value={filter.query} onChange={(event) => onFilter({ ...filter, query: event.target.value })}
            placeholder={t('verticals.searchPlaceholder')} className="min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-faint" />
        </label>
        <div role="group" aria-label={t('verticals.kindFilter')} className="flex flex-wrap gap-1">
          {KIND_FILTERS.map((kind) => <button key={kind} type="button" aria-pressed={filter.kind === kind} onClick={() => onFilter({ ...filter, kind })}
            className={`h-8 rounded-md px-2.5 text-xs font-medium transition-colors ${filter.kind === kind ? 'bg-blue/10 text-blue' : 'text-ink-faint hover:bg-bg hover:text-ink'}`}>{t(filterLabelKey(kind))}</button>)}
        </div>
      </div>
      {tags.length > 0 && <div role="group" aria-label={t('verticals.tagFilter')} className="mt-3 flex flex-wrap items-center gap-1.5">
        {tags.map((tag) => <button key={tag} type="button" aria-pressed={filter.tag === tag} className={chip(filter.tag === tag)}
          onClick={() => onFilter({ ...filter, tag: filter.tag === tag ? null : tag })}>#{tag}</button>)}
        {filtered && <button type="button" className="ml-1 text-xs text-ink-faint hover:text-ink" onClick={() => onFilter(EMPTY_FILTER)}>{t('verticals.clearFilters')}</button>}
      </div>}
      {rows.length > 0 && <p className="mt-3 text-xs text-ink-faint">{t('verticals.count', { shown: shown.length, total: rows.length })}</p>}
      {loading && !payload && <p className="mt-4 text-sm text-ink-faint">{t('verticals.loading')}</p>}
      {payload && rows.length === 0 && <p className="mt-4 text-sm text-ink-faint">{t('verticals.none')}</p>}
      {rows.length > 0 && shown.length === 0 && <p className="mt-4 text-sm text-ink-faint">{t('verticals.noMatches')}</p>}
      {shown.length > 0 && <div className="mt-3 grid gap-3 md:grid-cols-2" data-testid="vertical-grid">
        {shown.map((row) => <VerticalCard key={row.name} row={row} locale={locale} hosted={hosted} busy={pending === row.name} failure={failures[row.name]}
          known={known} onAct={onAct} onJump={onJump} onTag={(tag) => onFilter({ ...filter, tag })} />)}
      </div>}
    </>}
  </>;
}

export function VerticalStore({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  const [payload, setPayload] = useState<VerticalsPayload | null>(null);
  const [supported, setSupported] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<StoreFailure | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [failures, setFailures] = useState<Record<string, CardFailure>>({});
  const [filter, setFilter] = useState<StoreFilter>(EMPTY_FILTER);
  const close = useRef<HTMLButtonElement>(null);
  // Effects read the latest callbacks through refs so a re-render never restarts the polling loop.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const tRef = useRef(t);
  tRef.current = t;
  const running = anyRunning(payload?.verticals ?? []);
  const hosted = Boolean(payload?.host.managed_by_host) || hostedTrialBuild();

  // An older backend has no store: say so instead of showing a failed request.
  useEffect(() => {
    if (!open || supported !== null) return;
    let cancelled = false;
    api.meta().then(
      (meta) => { if (!cancelled) setSupported(hasStoreCapability(meta.capabilities, VERTICAL_STORE_CAPABILITY)); },
      () => { if (!cancelled) setSupported(true); },
    );
    return () => { cancelled = true; };
  }, [open, supported]);

  // Read the list when the store opens, then follow it: closely while a job
  // runs, more slowly when idle, and still slowly after closing until the
  // job ends so reopening shows the result.
  useEffect(() => {
    if (supported !== true || (!open && !running)) return;
    const controller = new AbortController();
    const load = () => api.verticals(controller.signal).then((next) => {
      if (controller.signal.aborted) return;
      setPayload(next);
      setError((current) => current?.source === 'load' ? null : current);
    }, (failure: unknown) => {
      if (controller.signal.aborted) return;
      if (isStoreMissing(failure)) setSupported(false);
      else if (open) setError({ text: tRef.current('verticals.loadFailed'), source: 'load' });
    });
    if (open) setLoading(true);
    void load().finally(() => { if (!controller.signal.aborted) setLoading(false); });
    const interval = pollInterval(open, running);
    const timer = interval ? window.setInterval(load, interval) : 0;
    return () => { controller.abort(); if (timer) window.clearInterval(timer); };
  }, [open, running, supported]);

  useEffect(() => {
    if (!open) { setFailures({}); setError((current) => current?.source === 'refresh' ? null : current); return; }
    close.current?.focus();
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') closeRef.current(); };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [open]);

  const reload = useCallback(async () => {
    try {
      setPayload(await api.verticals());
      setError((current) => current?.source === 'load' ? null : current);
    } catch (failure) {
      if (isStoreMissing(failure)) setSupported(false);
      else setError({ text: tRef.current('verticals.loadFailed'), source: 'load' });
    }
  }, []);

  const act = useCallback<Act>(async (row, action, options = {}) => {
    setPending(row.name);
    setFailures((current) => { const { [row.name]: _dropped, ...rest } = current; return rest; });
    try {
      await api.manageVertical(row.name, action, options);
      await reload();
      return true;
    } catch (failure) {
      const text = failureDetail(failure) || tRef.current('verticals.actionFailed');
      const offerForce = action === 'uninstall' && isStoreConflict(failure) && !options.force;
      setFailures((current) => ({ ...current, [row.name]: { text, offerForce } }));
      return false;
    } finally {
      setPending(null);
    }
  }, [reload]);

  const refreshCatalog = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      setPayload(await api.refreshVerticalCatalog());
    } catch (failure) {
      setError({ text: failureDetail(failure) || tRef.current('verticals.loadFailed'), source: 'refresh' });
    } finally {
      setRefreshing(false);
    }
  }, []);

  // A "requires" chip clears the filters so the target card is on the page, then scrolls to it.
  const jump = useCallback((name: string) => {
    setFilter(EMPTY_FILTER);
    if (typeof document === 'undefined') return;
    window.setTimeout(() => {
      const target = document.getElementById(`vertical-${name}`);
      target?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
      target?.focus?.();
    }, 0);
  }, []);

  if (!open) return null;
  return createPortal(<div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/20 p-5 backdrop-blur-sm" onClick={onClose}>
    <section role="dialog" aria-modal="true" aria-labelledby="vertical-store-title" onClick={(event) => event.stopPropagation()}
      className="flex max-h-[85vh] w-full max-w-4xl flex-col overflow-y-auto rounded-2xl border border-line bg-panel p-6 text-ink shadow-xl scroll-thin">
      <div className="flex items-center justify-between">
        <h2 id="vertical-store-title" className="flex items-center gap-2 text-lg font-semibold"><Store size={20} strokeWidth={1.5} className="text-blue" />{t('verticals.title')}</h2>
        <button ref={close} type="button" aria-label={t('verticals.close')} className="icon-control p-1.5" onClick={onClose}><X size={18} /></button>
      </div>
      <VerticalStoreView payload={payload} filter={filter} onFilter={setFilter} hosted={hosted} supported={supported} loading={loading} refreshing={refreshing}
        error={error?.text ?? ''} pending={pending} failures={failures} onAct={act} onRefresh={() => void refreshCatalog()} onJump={jump} />
    </section>
  </div>, document.body);
}
