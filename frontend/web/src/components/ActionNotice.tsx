import { useEffect, useState } from 'react';
import { useI18n } from '../i18n';
import { ErrorDetails } from './ErrorMessage';

export type NoticeTone = 'success' | 'error' | 'info';
export interface NoticeAction { label: string; onClick: () => Promise<void> }

export interface UiNotice {
  id: number;
  tone: NoticeTone;
  message: string;
  details?: unknown;
  action?: NoticeAction;
}

/** Short, accessible feedback for actions that otherwise change state silently; below the
 * header, never over the composer and its buttons. */
export function ActionNotice({
  notice,
  onClose,
}: {
  notice: UiNotice | null;
  onClose: () => void;
}) {
  const { locale } = useI18n();
  const [pending, setPending] = useState(false);
  useEffect(() => {
    setPending(false);
    if (!notice) return;
    if (notice.tone === 'error' || notice.action) return;
    const timeout = window.setTimeout(onClose, 5_000);
    return () => window.clearTimeout(timeout);
  }, [notice, onClose]);

  if (!notice) return null;
  const colors = notice.tone === 'error'
    ? 'border-err/60 text-err'
    : notice.tone === 'success'
    ? 'border-ok/60 text-ok'
    : 'border-blue-deep/60 text-blue-sky';
  return (
    <div
      role={notice.tone === 'error' ? 'alert' : 'status'}
      aria-live={notice.tone === 'error' ? 'assertive' : 'polite'}
      className={`fixed left-4 right-4 top-16 z-[70] flex items-start gap-2 rounded-md border bg-panel px-3 py-2.5 shadow-glow sm:left-auto sm:max-w-md lg:top-[108px] ${colors}`}
    >
      <span aria-hidden="true" className="mt-px shrink-0">
        {notice.tone === 'error' ? '!' : notice.tone === 'success' ? '✓' : 'i'}
      </span>
      <div className="min-w-0 flex-1 break-words text-xs leading-relaxed text-ink-dim">
        {notice.message}
        {notice.details ? <ErrorDetails error={notice.details} /> : null}
      </div>
      {notice.action && <button type="button" disabled={pending} className="shrink-0 text-xs underline disabled:opacity-50"
        onClick={() => { setPending(true); void notice.action!.onClick(); }}>{notice.action.label}</button>}
      <button
        type="button"
        aria-label={locale === 'zh-CN' ? '关闭提示' : 'Dismiss notification'}
        onClick={onClose}
        className="shrink-0 rounded px-1 text-base leading-none opacity-70 hover:bg-white/5 hover:opacity-100"
      >
        ×
      </button>
    </div>
  );
}
