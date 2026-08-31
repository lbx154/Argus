import { useEffect, useState, type FormEvent } from 'react';
import { Modal } from './Modal';
import { useI18n } from '../i18n';

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
}) {
  const { t } = useI18n();
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
      <div className="border-b border-line px-5 py-4">
        <h2 className="text-base font-semibold text-ink">{t('topbar.manageSession')}</h2>
        <p className="mt-0.5 font-mono text-[10px] text-ink-faint">{sid}</p>
      </div>

      <form onSubmit={(event) => void save(event)} className="border-b border-line p-5">
        <label className="block">
          <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-ink-faint">{t('manage.displayName')}</span>
          <div className="flex gap-2">
            <input
              value={draftName}
              onChange={(event) => setDraftName(event.target.value)}
              maxLength={80}
              disabled={busy}
              className="h-9 min-w-0 flex-1 rounded border border-line bg-bg/50 px-3 text-sm text-ink outline-none focus:border-blue-deep disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={busy || draftName.trim() === name}
              className="rounded border border-line px-3 text-xs text-ink-dim hover:bg-surface disabled:opacity-40"
            >
              {t('common.save')}
            </button>
          </div>
        </label>
      </form>

      <div className="border-b border-line p-5">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-faint">{t('manage.executor')}</div>
        <div className="mt-2 flex items-center justify-between rounded border border-line bg-bg/30 p-3">
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
              effectiveAlive ? 'border-warn/50 text-warn hover:bg-warn/10' : 'border-blue-deep bg-blue-deep text-white hover:bg-blue-deep/80'
            }`}
          >
            {busy ? t('manage.working') : !controlAvailable ? t('common.external') : effectiveAlive ? t('manage.stopNow') : t('manage.resume')}
          </button>
        </div>
      </div>

      <div className="p-5">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-err">{t('manage.deleteSession')}</div>
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
