import { useQueryClient } from '@tanstack/react-query';
import { ModelPicker } from './ModelPicker';
import { useDoctor, useConfig, useIdentity, useTranscript } from '../hooks';
import { Modal, ModalHeader } from './Modal';
import { Spinner, EmptyHint, RawDisclosure } from './primitives';
import { lastMeaningfulLine } from '../lib/rawSummary';
import { ago } from '../lib/format';
import { useEffect, useState } from 'react';
import { api } from '../api';
import { canOpenDesktopSettings, openDesktopTrialSettings } from '../lib/desktopBridge';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faFloppyDisk } from '@fortawesome/free-solid-svg-icons';
import { useI18n } from '../i18n';
import { requestFailureText } from '../lib/requestFailure';
import { AppearanceControls } from './AppearanceControls';
import type { ThemeMode } from './TopBar';
import { backendLabel, configuredBackend } from '../lib/backend';

type Translate = (key: string, variables?: Record<string, string | number>) => string;

function LoadFailure({ message, retrying, onRetry, t }: {
  message: string;
  retrying: boolean;
  onRetry: () => void;
  t: Translate;
}) {
  return (
    <div role="alert" className="flex flex-col items-center gap-3 px-4 py-8 text-center">
      <p className="text-sm text-err">{message}</p>
      <button type="button" onClick={onRetry} disabled={retrying} className="rounded-md border border-err/40 px-3 py-1.5 text-xs font-medium text-err hover:bg-err/10 disabled:opacity-40">
        {retrying ? t('common.loading') : t('common.retry')}
      </button>
    </div>
  );
}

/** Doctor: health checks with the single recommended fix pinned + gold, plus the
 *  daemon.log tail. Read-only diagnostics. */
export function DoctorModal({ sid, open, onClose }: { sid: string; open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  const { data, isLoading, isError, isFetching, refetch } = useDoctor(sid, open);
  const hasData = Boolean(data && (data.recommended || data.checks.length || data.log_tail.trim()));
  return (
    <Modal open={open} onClose={onClose} label={t('doctor.title')} width="max-w-3xl">
      <ModalHeader title={t('doctor.title')} sub={t('doctor.subtitle')} />
      <div className="p-4">
        {isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
        {!isLoading && isError && <LoadFailure message={t('doctor.loadError')} retrying={isFetching} onRetry={() => void refetch()} t={t} />}
        {!isLoading && !isError && !hasData && <EmptyHint>{t('doctor.empty')}</EmptyHint>}
        {!isLoading && !isError && data?.recommended && (
          <div className="mb-4 rounded-lg border border-gold/40 bg-gold/5 p-3">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-gold">{t('doctor.recommended')}</div>
            <div className="mt-1 text-sm text-ink">{data.recommended.name}</div>
            <div className="mt-0.5 text-xs text-ink-dim">{data.recommended.detail}</div>
            {data.recommended.fix && (
              <pre className="mt-2 whitespace-pre-wrap break-words rounded bg-bg p-2 font-mono text-xs text-blue-sky">{data.recommended.fix}</pre>
            )}
          </div>
        )}
        {!isLoading && !isError && <div className="space-y-1.5">
          {(data?.checks ?? []).map((c, i) => (
            <div key={i} className="flex items-start gap-2 rounded-md border border-line/60 px-3 py-2">
              <span className={c.ok ? 'text-ok' : 'text-err'}>{c.ok ? '✓' : '✗'}</span>
              <div className="min-w-0">
                <div className="text-xs font-medium text-ink">{c.name}</div>
                {c.detail && <div className="mt-0.5 text-[11px] text-ink-dim">{c.detail}</div>}
                {!c.ok && c.fix && (
                  <pre className="mt-1 whitespace-pre-wrap break-words rounded bg-bg p-1.5 font-mono text-xs text-ink-dim">{c.fix}</pre>
                )}
              </div>
            </div>
          ))}
        </div>}
        {!isLoading && !isError && data?.log_tail?.trim() && (
          <div className="mt-4">
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t('doctor.daemonLog')}</div>
            <p className="text-xs leading-5 text-ink-dim">
              <span className="text-ink-faint">{t('doctor.lastEntry')} · </span>
              {lastMeaningfulLine(data.log_tail)}
            </p>
            <RawDisclosure>
              <pre className="mt-1 max-h-48 overflow-x-hidden overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-bg p-3 font-mono text-xs leading-relaxed text-ink-dim scroll-thin">{data.log_tail}</pre>
            </RawDisclosure>
          </div>
        )}
      </div>
    </Modal>
  );
}

export function ConfigModal({
  sid,
  open,
  onClose,
  themeMode,
  onCycleTheme,
}: {
  sid: string;
  open: boolean;
  onClose: () => void;
  themeMode?: ThemeMode;
  onCycleTheme?: () => void;
}) {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const { data, isLoading, isError, isFetching, refetch } = useConfig(sid, open);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pendingBackend, setPendingBackend] = useState('');
  const [quickConfigBusy, setQuickConfigBusy] = useState(false);
  const [quickConfigMsg, setQuickConfigMsg] = useState('');
  const [quickConfigError, setQuickConfigError] = useState(false);
  useEffect(() => {
    if (!open) setPickerOpen(false);
  }, [open]);
  useEffect(() => {
    if (open && data) setPendingBackend('');
  }, [data, open]);
  const refreshSettings = async () => {
    await refetch();
    await queryClient.invalidateQueries({ queryKey: ['map-copy'] });
  };
  const currentBackend = configuredBackend(data);
  const backendTarget = pendingBackend || currentBackend;
  const setBackend = async (backend: string) => {
    if (quickConfigBusy || backend === currentBackend) return;
    setQuickConfigBusy(true);
    setQuickConfigMsg('');
    setQuickConfigError(false);
    try {
      await api.setConfig(sid, 'ARGUS_SKILL_RUNNER_BACKEND', backend);
      await refreshSettings();
      setQuickConfigMsg(t('settings.backendSwitched', { backend: backendLabel(backend, data) }));
    } catch (error) {
      setQuickConfigError(true);
      setQuickConfigMsg(requestFailureText(error, t).text);
    } finally {
      setQuickConfigBusy(false);
    }
  };
  const hasData = Boolean(data && (data.roles.length || data.operator_knobs.length));
  return (
    <>
    <Modal open={open} onClose={onClose} label={t('common.settings')} width="max-w-4xl">
      <ModalHeader title={t('common.settings')} sub={t('settings.subtitle')} />
      <div className="settings-body">
        {themeMode && onCycleTheme && <section className="settings-section mb-4 flex items-center gap-3">
          <div className="min-w-0 flex-1"><h3 className="text-sm font-medium">{locale === 'zh-CN' ? '此浏览器的偏好' : 'Preferences for this browser'}</h3>
            <p className="mt-1 text-xs text-ink-faint">{locale === 'zh-CN' ? '语言与明暗外观。' : 'Language and light/dark appearance.'}</p></div>
          <AppearanceControls themeMode={themeMode} onCycleTheme={onCycleTheme} />
        </section>}
        {isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
        {!isLoading && isError && <LoadFailure message={t('settings.loadError')} retrying={isFetching} onRetry={() => void refetch()} t={t} />}
        {!isLoading && !isError && !hasData && <EmptyHint>{t('settings.empty')}</EmptyHint>}
        {!isLoading && !isError && hasData && data && (
          <div className="settings-sections">
            {!data.trial_mode && <section className="settings-section">
              <h3 className="mb-3 text-sm font-semibold text-ink">{t('settings.quickConfig')}</h3>
              <details className="mb-4">
                <summary className="cursor-pointer text-xs text-ink-faint">{locale === 'zh-CN' ? '服务连接设置 · 由维护者配置' : 'Service connection · Maintainer settings'}</summary>
                <p className="my-2 text-xs leading-5 text-ink-faint">{locale === 'zh-CN' ? '普通使用无需修改。更换连接前，请确认服务已配置好相应账号与环境。' : 'Not required for ordinary use. Confirm the account and environment before changing this connection.'}</p>
              <label className="flex flex-wrap items-center gap-2">
                <span className="w-16 shrink-0 text-xs text-ink-dim">{t('settings.backend')}</span>
                <select
                  value={backendTarget}
                  disabled={quickConfigBusy}
                  onChange={(event) => setPendingBackend(event.target.value)}
                  aria-label={t('settings.backend')}
                  className="h-8 min-w-44 rounded border border-line bg-bg px-2 text-xs text-ink outline-none focus:border-blue disabled:opacity-40"
                >
                  {Object.entries(data.backends ?? {}).map(([backend, label]) => <option key={backend} value={backend}>{label}</option>)}
                </select>
                {/* A backend switch changes which models exist, so it only
                    takes effect on Apply, never on a stray dropdown change. */}
                <button
                  type="button"
                  data-apply-backend
                  onClick={() => void setBackend(backendTarget)}
                  disabled={quickConfigBusy || backendTarget === currentBackend}
                  className="h-8 shrink-0 rounded border border-line/70 px-2.5 text-xs font-medium text-ink-dim hover:border-blue/50 disabled:opacity-40"
                >
                  {t('settings.applyModel')}
                </button>
              </label>
              </details>
              <div className="mt-2 flex items-center gap-2">
                <span className="w-16 shrink-0 text-xs text-ink-dim">{t('settings.model')}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink" data-current-model>{data.roles[0]?.model || t('models.none')}</span>
                <button type="button" data-open-model-picker onClick={() => setPickerOpen(true)} className="h-8 shrink-0 rounded border border-blue/35 bg-blue/8 px-2.5 text-xs font-medium text-blue hover:border-blue-deep hover:bg-blue-deep hover:text-on-accent">
                  {t('models.title')}
                </button>
              </div>
              {quickConfigMsg && (
                <div
                  role={quickConfigError ? 'alert' : 'status'}
                  className={`mt-1.5 text-[10px] ${quickConfigError ? 'text-err' : 'text-ink-dim'}`}
                >
                  {quickConfigMsg}
                </div>
              )}
            </section>}

            {data.trial_mode && <section className="settings-section" aria-label={t('settings.trialAccount')}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-ink">{t('settings.trialAccount')}</h3>
                  <p className="mt-1 text-xs text-ink-dim">GPT-5.5 · high</p>
                  <p className="mt-1 text-xs text-ink-faint">{t('settings.trialKeyHint')}</p>
                </div>
                {canOpenDesktopSettings() && <button type="button" className="compact-control px-3 py-2" onClick={() => { onClose(); openDesktopTrialSettings(); }}>{t('settings.changeTrialKey')}</button>}
              </div>
            </section>}
          </div>
        )}
      </div>
    </Modal>
    {!data?.trial_mode && <ModelPicker sid={sid} open={open && pickerOpen} onClose={() => setPickerOpen(false)} />}
    </>
  );
}

/** Identity: the operator identity text on a wordmarked panel. */
export function IdentityModal({ sid, open, onClose }: { sid: string; open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  const { data, isLoading, refetch } = useIdentity(sid, open);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState('');
  useEffect(() => {
    if (open && data != null) setDraft(data);
  }, [data, open]);
  const save = async () => {
    if (busy) return;
    setBusy(true);
    setResult('');
    try {
      await api.setIdentity(sid, draft);
      await refetch();
      setResult(t('identity.saved'));
    } catch (error) {
      setResult(requestFailureText(error, t).text);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} onClose={onClose} label={t('identity.title')} width="max-w-2xl">
      <ModalHeader title={t('identity.title')} sub={t('identity.subtitle')} />
      <div className="max-h-[64vh] overflow-y-auto scroll-thin p-5">
        {isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
        {!isLoading ? (
          <>
            <textarea value={draft} onChange={(event) => setDraft(event.target.value)} rows={12} className="w-full resize-y rounded-lg border border-line bg-bg p-3 font-sans text-sm leading-relaxed text-ink outline-none focus:border-blue" placeholder={t('identity.placeholder')} />
            <div className="mt-3 flex items-center justify-between">
              <span className="text-xs text-ink-faint">{result}</span>
              <button type="button" onClick={() => void save()} disabled={busy || draft === (data ?? '')} title={t('identity.save')} aria-label={t('identity.save')} className="flex h-9 w-9 items-center justify-center rounded border border-blue/35 bg-blue/8 text-xs font-medium text-blue hover:border-blue-deep hover:bg-blue-deep hover:text-on-accent disabled:opacity-40">{busy ? '…' : <FontAwesomeIcon icon={faFloppyDisk} />}</button>
            </div>
          </>
        ) : null}
      </div>
    </Modal>
  );
}

/** Transcript: recent operator↔argus turns for replay/resume. Reply via the
 *  composer (nudge/note) — this pane is read-only history. */
export function TranscriptModal({ sid, open, onClose }: { sid: string; open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  const { data, isLoading } = useTranscript(sid, open);
  const turns = data ?? [];
  return (
    <Modal open={open} onClose={onClose} label={t('transcript.title')} width="max-w-2xl">
      <ModalHeader title={t('transcript.title')} sub={t('transcript.subtitle')} />
      <div className="max-h-[64vh] overflow-y-auto scroll-thin p-4">
        {isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
        {!isLoading && turns.length === 0 && <EmptyHint>{t('transcript.empty')}</EmptyHint>}
        {turns.map((turn, i) => {
          const me = turn.role === 'operator';
          return (
            <div key={i} className="grid grid-cols-[72px_minmax(0,1fr)] border-b border-line/50 py-2.5 last:border-b-0">
              <div>
                <div className={`font-mono text-[10px] font-semibold uppercase tracking-wide ${me ? 'text-ink-faint' : 'text-blue-sky'}`}>
                  {me ? t('transcript.operator') : 'argus'}
                </div>
                <div className="mt-0.5 text-[9px] text-ink-faint">{ago(turn.ts)}</div>
              </div>
              <div className="whitespace-pre-wrap text-sm leading-relaxed text-ink-dim">{turn.text}</div>
            </div>
          );
        })}
      </div>
    </Modal>
  );
}
