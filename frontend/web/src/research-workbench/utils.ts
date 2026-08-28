import type { ArtifactInfo, EventMsg } from './types';

export function cx(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ');
}

export function formatDuration(value: number | null | undefined): string {
  const total = Math.max(0, Math.floor(Number(value ?? 0)));
  if (total < 60) return `${total}s`;
  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3_600);
  const minutes = Math.floor((total % 3_600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

export function formatClock(ts: number | null | undefined, locale: 'en' | 'zh-CN'): string {
  if (!ts) return '—';
  return new Date(ts * 1_000).toLocaleTimeString(locale, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

export function formatDate(ts: number | null | undefined, locale: 'en' | 'zh-CN'): string {
  if (!ts) return '—';
  return new Date(ts * 1_000).toLocaleString(locale, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

export function formatBytes(bytes: number): string {
  if (bytes < 1_024) return `${bytes} B`;
  if (bytes < 1_048_576) return `${(bytes / 1_024).toFixed(1)} KB`;
  return `${(bytes / 1_048_576).toFixed(1)} MB`;
}

export type Tone = 'neutral' | 'info' | 'success' | 'warn' | 'danger' | 'live';

export function statusTone(status: string | null | undefined): Tone {
  const value = String(status ?? '').toLowerCase();
  if (/failed|error|blocked|rejected|stalled|dead|abort/.test(value)) return 'danger';
  if (/warn|waiting|paused|hold|queued|pending|continue/.test(value)) return 'warn';
  if (/done|complete|completed|accepted|healthy|success|passed/.test(value)) return 'success';
  if (/run|active|work|claimed|progress|live/.test(value)) return 'live';
  if (/research|plan|info|ready/.test(value)) return 'info';
  return 'neutral';
}

export function eventRole(event: EventMsg): string {
  const role = String(event.agent_layer ?? event.actor ?? '').toLowerCase();
  if (role === 'main' || role.startsWith('engineer')) return 'engineer';
  if (role.startsWith('review')) return 'reviewer';
  if (role.startsWith('plan')) return 'planner';
  if (role.startsWith('manager')) return 'manager';
  const type = String(event.type ?? '');
  if (/review/.test(type)) return 'reviewer';
  if (/planner/.test(type)) return 'planner';
  if (/manager/.test(type)) return 'manager';
  if (/engineer|round/.test(type)) return 'engineer';
  return 'system';
}

export function eventTitle(event: EventMsg): string {
  const action = String(event.action_summary ?? '').trim();
  if (action) return action;
  const title = String(event.title ?? '').trim();
  if (title) return title;
  const kind = String(event.kind ?? '').trim();
  if (kind) return kind.replaceAll('_', ' ');
  const type = String(event.type ?? 'event');
  return type.split('.').slice(-2).join(' · ').replaceAll('_', ' ');
}

export function eventDetail(event: EventMsg, limit = 400): string {
  const text = String(event.text ?? event.reason ?? event.summary ?? event.detail ?? '').trim();
  if (!text) return '';
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

export function mergeStreamText(current: string, next: string, mode: string): string {
  if (!next) return current;
  if (mode === 'snapshot') return next;
  if (mode === 'append') return `${current}${next}`;
  if (next.startsWith(current)) return next;
  if (current.endsWith(next)) return current;
  return current ? `${current}\n${next}` : next;
}

export function artifactIsLiterature(item: ArtifactInfo): boolean {
  return /literature|paper|reference|citation|bibliograph|bibtex|arxiv|related.?work|survey|source/i
    .test(`${item.path} ${item.name} ${item.why}`)
    || item.name.toLowerCase().endsWith('.bib');
}

export function artifactIsPaper(item: ArtifactInfo): boolean {
  return /paper|manuscript|latex|draft|technical.?report|camera.?ready|submission|figure|table|reference/i
    .test(`${item.path} ${item.name} ${item.why}`)
    || /\.(tex|bib|pdf)$/.test(item.name.toLowerCase());
}

export function artifactIsCode(item: ArtifactInfo): boolean {
  return /\.(py|ts|tsx|js|jsx|sh|toml|ya?ml|json|ipynb|csv|md|txt)$/i.test(item.name);
}

export function extractUrls(value: string): string[] {
  const matches = value.match(/https?:\/\/[^\s<>"')\]}]+/g) ?? [];
  return [...new Set(matches.map((url) => url.replace(/[.,;:]$/, '')))];
}

export function compactPath(value: string, max = 54): string {
  if (value.length <= max) return value;
  const parts = value.split('/');
  if (parts.length < 3) return `…${value.slice(-(max - 1))}`;
  const tail = parts.slice(-2).join('/');
  return `…/${tail.length > max - 2 ? tail.slice(-(max - 2)) : tail}`;
}
