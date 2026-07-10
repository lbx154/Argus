import { useEffect } from 'react';

export type NoticeTone = 'success' | 'error' | 'info';

export interface UiNotice {
  id: number;
  tone: NoticeTone;
  message: string;
}

/** Short, accessible feedback for actions that otherwise change state silently. */
export function ActionNotice({
  notice,
  onClose,
}: {
  notice: UiNotice | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(onClose, notice.tone === 'error' ? 8_000 : 4_000);
    return () => window.clearTimeout(timeout);
  }, [notice, onClose]);

  if (!notice) return null;
  const colors = notice.tone === 'error'
    ? 'border-err/60 bg-err/10 text-err'
    : notice.tone === 'success'
    ? 'border-ok/60 bg-ok/10 text-ok'
    : 'border-blue-deep/60 bg-panel text-blue-sky';
  return (
    <div
      role={notice.tone === 'error' ? 'alert' : 'status'}
      aria-live={notice.tone === 'error' ? 'assertive' : 'polite'}
      className={`fixed bottom-4 left-4 right-4 z-[70] flex items-start gap-2 rounded-md border px-3 py-2.5 shadow-glow sm:left-auto sm:max-w-md ${colors}`}
    >
      <span aria-hidden="true" className="mt-px shrink-0">
        {notice.tone === 'error' ? '!' : notice.tone === 'success' ? '✓' : 'i'}
      </span>
      <span className="min-w-0 flex-1 break-words text-xs leading-relaxed text-ink-dim">
        {notice.message}
      </span>
      <button
        type="button"
        aria-label="dismiss notification"
        onClick={onClose}
        className="shrink-0 rounded px-1 text-base leading-none opacity-70 hover:bg-white/5 hover:opacity-100"
      >
        ×
      </button>
    </div>
  );
}
