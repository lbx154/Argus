import { translate, type Locale } from '../i18n';

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
  const units = ['B', 'KB', 'MB', 'GB'];
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

/** local wall-clock HH:MM:SS for a stream event, tolerant of ts/time shapes. */
export function clockOf(ev: Record<string, unknown>): string {
  const raw = ev.ts ?? ev.time;
  let ms: number | null = null;
  if (typeof raw === 'number') ms = raw > 1e12 ? raw : raw * 1000;
  else if (typeof raw === 'string') {
    const p = Date.parse(raw);
    if (!isNaN(p)) ms = p;
  }
  if (ms == null) return '';
  const d = new Date(ms);
  const p = (x: number) => String(x).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** Best-effort human-readable message for a thrown value of unknown shape. */
export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error || 'Unknown error');
}

export function managerStreamFailureMessage(error: unknown, gotDelta: boolean): string {
  const detail = errorText(error);
  return gotDelta
    ? `Reply interrupted after a partial response: ${detail}`
    : `Message failed before a response was received: ${detail}`;
}
