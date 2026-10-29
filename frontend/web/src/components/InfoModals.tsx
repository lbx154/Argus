import { useQueryClient } from '@tanstack/react-query';
import { ModelPicker } from './ModelPicker';
import { useConfig } from '../hooks';
import { Modal, ModalHeader } from './Modal';
import { Spinner, EmptyHint } from './primitives';
import { useEffect, useState } from 'react';
import { api } from '../api';
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
            <section className="settings-section">
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
            </section>


          </div>
        )}
      </div>
    </Modal>
    <ModelPicker sid={sid} open={open && pickerOpen} onClose={() => setPickerOpen(false)} />
    </>
  );
}
