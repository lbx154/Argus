import { translate, type Locale } from '../i18n';
import { ApiError } from '../../../core/src/http';

/** Small formatting helpers shared across the web views. */

/** epoch-seconds → "3m ago" / "2h ago" / "just now". */
export function ago(ts: number | null | undefined): string {
  if (!ts) return '—';
  const now = Date.now() / 1000;
  const d = Math.max(0, now - ts);
  if (d < 5) return 'just now';
  if (d < 60) return `${Math.floor(d)}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

/** seconds of uptime → "1d 3h" / "4h 12m" / "9m". */
export function uptime(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return '—';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${Math.floor(seconds)}s`;
}

export function money(n: number | null | undefined, digits = 2): string {
  if (n == null || !isFinite(n)) return '$0.00';
  return `$${n.toFixed(digits)}`;
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** index;
  return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}

/** Human-readable local time relative to now, with calendar context for older events. */
export function formatRelativeTime(ts: string | number | Date, locale: Locale = 'en'): string {
  const date = ts instanceof Date
    ? ts
    : new Date(typeof ts === 'number' && ts < 1e12 ? ts * 1000 : ts);
  if (Number.isNaN(date.getTime())) return '—';

  const now = new Date();
  const elapsed = Math.max(0, Math.floor((now.getTime() - date.getTime()) / 1000));
  if (elapsed < 60) return translate('time.justNow', {}, locale);
  if (elapsed < 3600) {
    return translate('time.minutesAgo', { count: Math.floor(elapsed / 60) }, locale);
  }

  const time = new Intl.DateTimeFormat(locale, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
  const calendarDays = Math.round((
    Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())
    - Date.UTC(date.getFullYear(), date.getMonth(), date.getDate())
  ) / 86_400_000);
  if (calendarDays === 1) return translate('time.yesterdayAt', { time }, locale);
  if (elapsed < 86_400) {
    return translate('time.hoursAgo', { count: Math.floor(elapsed / 3600) }, locale);
  }
  if (elapsed < 7 * 86_400) {
    return new Intl.DateTimeFormat(locale, {
      weekday: 'short',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(date);
  }
  return new Intl.DateTimeFormat(locale, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
}

/** Local date for a stream event, tolerant of ts/time shapes. */
export function dateOf(ev: Record<string, unknown>): Date | null {
  const raw = ev.ts ?? ev.time;
  let ms: number | null = null;
  if (typeof raw === 'number') ms = raw > 1e12 ? raw : raw * 1000;
  else if (typeof raw === 'string') {
    const p = Date.parse(raw);
    if (!isNaN(p)) ms = p;
  }
  if (ms == null || !Number.isFinite(ms)) return null;
  const d = new Date(ms);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** local wall-clock HH:MM:SS for a stream event, tolerant of ts/time shapes. */
export function clockOf(ev: Record<string, unknown>): string {
  const d = dateOf(ev);
  if (!d) return '';
  const p = (x: number) => String(x).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** Best-effort human-readable message for a thrown value of unknown shape.
 * HTTP failures read as what happened, not as a request line and status code. */
export function errorText(error: unknown): string {
  if (error instanceof ApiError) return apiErrorText(error);
  const message = error instanceof Error ? error.message : String(error || 'Unknown error');
  const full = /workspace storage is full \(([\d.]+) GB of ([\d.]+) GB\)/.exec(message);
  return full ? translate('storagePause.notice', { used: full[1], limit: full[2] }) : message;
}

function apiErrorText(error: ApiError): string {
  const detail = error.detail.trim();
  const status = error.status;
  const limit = /active daemon limit (\d+) reached/.exec(detail);
  if (limit) return translate('error.daemonLimit', { limit: limit[1] });
  if (!status) return translate('error.network');
  if (status === 401) return translate('error.unauthorized');
  if (status === 403) return detail || translate('error.forbidden');
  if (status === 404) return translate('error.notFound');
  if (status === 413) return translate('error.tooLarge');
  if (status === 429 || status === 503) return detail ? `${translate('error.busy')}（${detail}）` : translate('error.busy');
  if (status >= 500) return detail ? `${translate('error.server')}（${detail}）` : translate('error.server');
  return detail || translate('error.rejected');
}

export function managerStreamFailureMessage(error: unknown, gotDelta: boolean): string {
  const detail = errorText(error);
  return gotDelta
    ? `Reply interrupted after a partial response: ${detail}`
    : `Message failed before a response was received: ${detail}`;
}
