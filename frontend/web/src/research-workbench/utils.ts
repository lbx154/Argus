import type { Locale } from '../i18n';
import { plainEventName, plainStatus } from '../lib/plainStatus';
import type { EventMsg } from './types';

export function cx(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ');
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

export type Tone = 'neutral' | 'info' | 'success' | 'warn' | 'danger' | 'live';

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

export function eventTitle(event: EventMsg, locale?: Locale): string {
  const action = String(event.action_summary ?? '').trim();
  if (action) return locale ? plainStatus(action, locale) : action;
  const title = String(event.title ?? '').trim();
  if (title) return locale ? plainStatus(title, locale) : title;
  const named = locale ? plainEventName(String(event.type ?? ''), locale) : null;
  if (named) return named;
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
