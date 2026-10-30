import { translate, type Locale } from '../i18n';
import { ApiError } from '../../../core/src/http';

/** Small formatting helpers shared across the web views. */

/** seconds of uptime → "1d 3h" / "4h 12m" / "9m", or "1 天 3 小时" / "4 小时 12 分钟" / "9 分钟". */
export function uptime(seconds: number | null | undefined, locale: Locale = 'en'): string {
  if (seconds == null || seconds < 0) return '—';
  const [day, hour, minute, second] = locale === 'zh-CN' ? [' 天', ' 小时', ' 分钟', ' 秒'] : ['d', 'h', 'm', 's'];
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d}${day} ${h}${hour}`;
  if (h) return `${h}${hour} ${m}${minute}`;
  if (m) return `${m}${minute}`;
  return `${Math.floor(seconds)}${second}`;
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

/** When something happened, as a clock: today the time, yesterday and this week with the day, earlier
 * with the date. It reads true however long the page stays open, as "5 minutes ago" would not. */
export function formatCalendarTime(ts: string | number | Date, locale: Locale = 'en'): string {
  const date = ts instanceof Date
    ? ts
    : new Date(typeof ts === 'number' && ts < 1e12 ? ts * 1000 : ts);
  if (Number.isNaN(date.getTime())) return '—';
  const now = new Date();
  const time = new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
  const calendarDays = Math.round((
    Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())
    - Date.UTC(date.getFullYear(), date.getMonth(), date.getDate())
  ) / 86_400_000);
  if (calendarDays <= 0) return time;
  if (calendarDays === 1) return translate('time.yesterdayAt', { time }, locale);
  if (calendarDays < 7) {
    return new Intl.DateTimeFormat(locale, { weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
  }
  return new Intl.DateTimeFormat(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
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

/** The UI owns error copy. Service/model prose belongs only in raw details;
 * unknown codes still get a sentence in the selected language. */
export function errorText(error: unknown, locale?: Locale, code = error instanceof ApiError ? error.code : ''): string {
  if (code) {
    const key = `error.${code}`;
    const text = translate(key, {}, locale);
    if (text !== key) return text;
  }
  let key = 'unknown';
  // The request never reached the server (api.ts's LocalArgusUnavailableError): the network, not the work.
  if (error instanceof Error && error.name === 'LocalArgusUnavailableError') key = 'network';
  else if (error instanceof ApiError) {
    const status = error.status;
    key = !status ? 'network' : status === 401 ? 'unauthorized' : status === 403 ? 'forbidden'
      : status === 404 ? 'notFound' : status === 413 ? 'tooLarge' : status === 429 || status === 503 ? 'busy'
      : status >= 500 ? 'server' : 'rejected';
  }
  return translate(`error.${key}`, {}, locale);
}

export function rawErrorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error ?? '');
}

/** A Ctrl/⌘ shortcut as this platform writes it: "⌘K" on Apple devices, "Ctrl+K" elsewhere. */
export const shortcut = (key: string): string => /Mac|iPhone|iPad/.test(navigator.platform) ? `⌘${key}` : `Ctrl+${key}`;
