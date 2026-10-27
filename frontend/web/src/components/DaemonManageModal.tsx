import { useEffect, useState, type FormEvent } from 'react';
import { Modal } from './Modal';
import { useI18n } from '../i18n';
import type { Snapshot } from '../api';
import { SPEND_ESTIMATE_HINT, spendEstimateLabel } from '../lib/projectOverview';

export function DaemonManageModal({
  open,
  sid,
  name,
  alive,
  controlAvailable = true,
  busy,
  onClose,
  onRename,
  onStart,
  onStop,
  onDelete,
  snapshot,
}: {
  open: boolean;
  sid: string;
  name: string;
  alive: boolean;
  controlAvailable?: boolean;
  busy: boolean;
  onClose: () => void;
  onRename: (name: string) => Promise<boolean>;
  onStart: () => Promise<boolean>;
  onStop: () => Promise<boolean>;
  onDelete: () => Promise<boolean>;
  /** Run details (cost, calls) live here, out of the top bar. */
  snapshot?: Snapshot | null;
}) {
  const { t, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const spend = snapshot ? spendEstimateLabel({ spendUsd: typeof snapshot.spend_usd === 'number' ? snapshot.spend_usd : null, spendStatus: snapshot.spend_status }, zh) : '';
  const [draftName, setDraftName] = useState(name);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [stoppedLocally, setStoppedLocally] = useState(false);

  useEffect(() => {
    if (open) {
      setDraftName(name);
      setConfirmDelete(false);
      setStoppedLocally(false);
    }
  }, [open, name, sid]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    await onRename(draftName.trim());
  };
  const effectiveAlive = alive && !stoppedLocally;
  const toggleExecutor = async () => {
    if (effectiveAlive) {
      if (await onStop()) setStoppedLocally(true);
      return;
    }
    if (await onStart()) setStoppedLocally(false);
  };

  return (
    <Modal open={open} onClose={() => !busy && onClose()} label={t('manage.daemon')} width="max-w-lg">
      <div className="modal-header">
        <h2>{t('topbar.manageSession')}</h2>
        <p className="font-mono text-[11px] text-ink-faint">{sid}</p>
      </div>
      {snapshot ? <div className="border-t border-line px-6 py-4" data-testid="manage-run-info">
        <div className="text-xs font-medium text-ink-faint">{zh ? '运行信息' : 'Run details'}</div>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-ink-dim">
          <span title={spend ? (zh ? SPEND_ESTIMATE_HINT.zh : SPEND_ESTIMATE_HINT.en) : undefined}>{spend || (zh ? '还没有花费' : 'No spend yet')}</span>
          {snapshot.usage_summary?.call_count ? <span>{zh ? `模型调用 ${snapshot.usage_summary.call_count} 次` : `${snapshot.usage_summary.call_count} model calls`}</span> : null}
        </div>
      </div> : null}

      <form onSubmit={(event) => void save(event)} className="border-y border-line px-6 py-5">
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-faint">{t('manage.displayName')}</span>
          <div className="flex gap-2">
            <input
              value={draftName}
              onChange={(event) => setDraftName(event.target.value)}
              maxLength={80}
              disabled={busy}
              className="h-9 min-w-0 flex-1 rounded-md border border-field-line/70 bg-surface px-3 text-sm text-ink outline-none focus:border-accent disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={busy || draftName.trim() === name}
              className="brand-button brand-button-ghost disabled:opacity-40"
            >
              {t('common.save')}
            </button>
          </div>
        </label>
      </form>

      <div className="border-b border-line px-6 py-5">
        <div className="text-xs font-medium text-ink-faint">{t('manage.executor')}</div>
        <div className="mt-2 flex items-center justify-between gap-4">
          <div>
            <div className="text-sm text-ink">{effectiveAlive ? (controlAvailable ? t('manage.running') : t('manage.runningExternally')) : t('manage.paused')}</div>
            <p className="mt-0.5 text-[11px] text-ink-faint">
              {effectiveAlive
                ? controlAvailable
                  ? t('manage.stopNowHint')
                  : t('manage.externalHint')
                : t('manage.resumeHint')}
            </p>
          </div>
          <button
            type="button"
            disabled={busy || !controlAvailable}
            onClick={() => void toggleExecutor()}
            className={`rounded border px-3 py-1.5 text-xs disabled:cursor-wait disabled:opacity-50 ${
              effectiveAlive ? 'border-line text-ink hover:bg-sunken' : 'border-ink bg-ink text-surface hover:bg-ink/85'
            }`}
          >
            {busy ? t('manage.working') : !controlAvailable ? t('common.external') : effectiveAlive ? t('manage.stopNow') : t('manage.resume')}
          </button>
        </div>
      </div>

      <div className="px-6 py-5">
        <div className="text-xs font-medium text-err">{t('manage.deleteSession')}</div>
        <p className="mt-1 text-[11px] leading-relaxed text-ink-faint">
          {t('manage.deleteHint')}
        </p>
        {!confirmDelete ? (
          <button
            type="button"
            disabled={busy || effectiveAlive}
            onClick={() => setConfirmDelete(true)}
            className="mt-3 rounded border border-err/40 px-3 py-1.5 text-xs text-err hover:bg-err/10 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {t('manage.delete')}
          </button>
        ) : (
          <div className="mt-3 flex items-center justify-between gap-3 rounded border border-err/40 bg-err/5 p-3">
            <span className="text-xs text-ink-dim">{t('manage.confirmQuestion')}</span>
            <div className="flex gap-2">
              <button type="button" onClick={() => setConfirmDelete(false)} className="rounded px-2 py-1 text-xs text-ink-faint hover:bg-surface">{t('common.cancel')}</button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void onDelete()}
                className="rounded bg-err px-3 py-1 text-xs font-medium text-bg disabled:opacity-50"
              >
                {t('manage.confirmDelete')}
              </button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}
