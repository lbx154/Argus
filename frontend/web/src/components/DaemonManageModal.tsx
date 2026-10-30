import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { Modal } from './Modal';
import { useI18n } from '../i18n';
import type { Snapshot } from '../api';
import { SPEND_ESTIMATE_HINT, spendEstimateLabel } from '../lib/projectOverview';

export function DaemonManageModal({
  open,
  sid,
  name,
  alive,
  busy,
  onClose,
  onRename,
  onDelete,
  snapshot,
  feedback,
}: {
  open: boolean;
  sid: string;
  name: string;
  alive: boolean;
  busy: boolean;
  onClose: () => void;
  onRename: (name: string) => Promise<boolean>;
  onDelete: () => Promise<boolean>;
  /** The project's current cost and call count. */
  snapshot?: Snapshot | null;
  /** Action feedback stays readable and reachable while the dialog is open. */
  feedback?: ReactNode;
}) {
  const { t, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const spend = snapshot ? spendEstimateLabel({ spendUsd: typeof snapshot.spend_usd === 'number' ? snapshot.spend_usd : null, spendStatus: snapshot.spend_status }, zh) : '';
  const [draftName, setDraftName] = useState(name);

  useEffect(() => {
    if (open) {
      setDraftName(name);
    }
  }, [open, name, sid]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (await onRename(draftName.trim())) onClose();
  };
  return (
    <Modal open={open} onClose={() => !busy && onClose()} label={t('manage.daemon')} width="max-w-lg">
      <div className="modal-header">
        <h2>{t('topbar.manageSession')}</h2>
      </div>
      {feedback}
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

      <div className="px-6 py-5">
        <div className="text-xs font-medium text-err">{t('manage.deleteSession')}</div>
        <p className="mt-1 text-[11px] leading-relaxed text-ink-faint">
          {t('manage.deleteHint')}
        </p>
        <button
          type="button"
          disabled={busy || alive}
          onClick={() => void onDelete()}
          className="mt-3 rounded border border-err/40 px-3 py-1.5 text-xs text-err hover:bg-err/10 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {t('manage.delete')}
        </button>
      </div>
    </Modal>
  );
}
