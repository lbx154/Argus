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
