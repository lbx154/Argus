import type { Snapshot } from '../api';
import { useI18n } from '../i18n';

const gb = (bytes: number) => (bytes / 1024 ** 3).toFixed(1);

/**
 * While the workspace is over its storage limit, work is paused until files
 * are removed. Without this the page showed nothing and the work simply
 * stopped moving (2026-10-29 trial).
 */
export function StoragePauseBanner({ pause }: { pause: Snapshot['storage_pause'] }) {
  const { t } = useI18n();
  if (!pause) return null;
  return (
    <div
      data-storage-pause
      role="alert"
      className="mx-3 mt-3 rounded-lg border border-err/50 bg-err/10 px-3.5 py-2 text-[13px] leading-5 text-err"
    >
      {t('storagePause.notice', { used: gb(pause.used_bytes), limit: gb(pause.limit_bytes) })}
    </div>
  );
}
