import { theme } from './theme.js';
import type { EventMsg } from './api.js';

/**
 * event.type → colour. Transcribed from the Python renderer
 * (argus_skill/cli/render.py:43-159) so the TUI colours events the same way
 * the Rich cockpit does. Kept as a small pure table — no backend import.
 */
export function eventColor(ev: EventMsg): string {
  const t = String(ev.type ?? '');
  if (t === 'round.review.completed') {
    const s = String(ev.status ?? '');
    if (s === 'done') return theme.success;
    if (s === 'blocked' || s === 'no_progress') return theme.error;
    return theme.warning; // continue
  }
  if (t === 'mission.completed' || t === 'loop.completed') {
    return ev.success === false ? theme.error : theme.success;
  }
  if (t === 'mission.error' || t === 'command.error' || t === 'daemon.stopping') return theme.error;
  if (t === 'round.main.completed') return theme.info;
  if (t === 'round.checks.completed') return theme.warning;
  if (t === 'plan.completed' || t.startsWith('distill.')) return theme.accent;
  if (t === 'final.report.ready' || t === 'pptx.report.ready') return theme.accent;
  if (t === 'life.inbox.queued' || t === 'life.inbox.drained') return theme.accent;
  if (
    t === 'mission.started' ||
    t === 'loop.started' ||
    t === 'life.mission.started' ||
    t === 'round.started' ||
    t === 'round.start'
  ) {
    return theme.info;
  }
  if (t.startsWith('engineer') || t === 'life.mission.completed') return theme.role.engineer;
  if (t.startsWith('life.planner')) return theme.role.planner;
  return 'white';
}

/** A compact one-line summary of an event for the scrolling log. */
export function eventLine(ev: EventMsg): string {
  const t = String(ev.type ?? 'event');
  if (t === 'engineer.progress') {
    const kind = String(ev.kind ?? '');
    const text = String(ev.text ?? ev.action_summary ?? '').split('\n')[0]?.trim() ?? '';
    const glyph =
      kind === 'assistant_message' || kind === 'agent_message' || kind === 'message'
        ? '▌'
        : '▸';
    return `${glyph} ${trunc(text, 140)}`;
  }
  if (t === 'round.review.completed') {
    return `⟳ review · ${String(ev.status ?? '?')} · ${trunc(String(ev.reason ?? ''), 100)}`;
  }
  if (t === 'round.started' || t === 'round.start') {
    return `── round ${ev.round_index ?? ev.round ?? '?'}`;
  }
  if (t === 'mission.started' || t === 'life.mission.started') {
    return `▶ ${trunc(String(ev.text ?? ev.title ?? ev.objective ?? 'mission started'), 120)}`;
  }
  if (t === 'mission.completed' || t === 'life.mission.completed') {
    return `■ mission ${ev.success === false ? 'failed' : String(ev.status ?? 'done')}`;
  }
  if (t === 'life.inbox.queued') return `📥 nudge · ${trunc(String(ev.text ?? ''), 120)}`;
  const text = String(ev.text ?? '').split('\n')[0]?.trim() ?? '';
  return text ? `${t} · ${trunc(text, 120)}` : t;
}

function trunc(s: string, n: number): string {
  return s.length <= n ? s : s.slice(0, n - 1) + '…';
}

/** Identify reasoning protocol events so presentation layers can exclude them. */
export function isReasoning(ev: EventMsg): boolean {
  return String(ev.type ?? '') === 'engineer.progress' && String(ev.kind ?? '') === 'reasoning';
}

/** The role that drove an event (for colouring the reasoning pane). */
export function eventRole(ev: EventMsg): string {
  const layer = String(ev.agent_layer ?? '');
  if (layer) return layer;
  const t = String(ev.type ?? '');
  if (t.startsWith('life.planner')) return 'planner';
  if (t.startsWith('round.review') || t.startsWith('reviewer')) return 'reviewer';
  if (t.startsWith('life.manager') || t.startsWith('manager')) return 'manager';
  return 'engineer';
}
