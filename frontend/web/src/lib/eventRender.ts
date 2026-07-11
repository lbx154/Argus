import type { EventMsg } from '../api';
import { theme } from './theme';
import {
  eventKey as sharedEventKey,
  isReasoning,
  isStructuredAgentPayload,
  mergeFragment,
} from '../../../core/src/events';

export { isReasoning, mergeFragment };

/**
 * Faithful web port of the terminal's clean feed (argus_skill/apps/cli/_follow.py
 * _format_follow_event_body + cli/render.py). The daemon's raw events.jsonl is
 * noisy — raw CLI framing (agent_io.*), internal bookkeeping, empty progress.
 * The REPL shows a WHITELIST: each meaningful event maps to a role, glyph, and a
 * human line; everything else is hidden. This mirrors that exactly so the web
 * feed reads like the terminal, not a debug dump.
 */

export type Tone = 'bright' | 'dim' | 'accent' | 'ok' | 'warn' | 'err' | 'info';

export interface Rendered {
  role: string; // manager | planner | engineer | reviewer | critic | system
  label: string; // human role label e.g. "Engineer"
  glyph: string;
  text: string;
  tone: Tone;
  rule?: boolean; // render as a section divider (round/mission boundary)
  reasoning?: boolean; // inner-monologue — hidden unless the reasoning toggle is on
}

function trunc(s: string, n: number): string {
  const t = (s || '').replace(/```[a-z]*\n?/gi, '').replace(/\[([^\]]+)\]\([^)]+\)/g, '[$1]').trim();
  return t.length <= n ? t : t.slice(0, n - 1).trimEnd() + '…';
}
const firstLine = (s: unknown) => String(s ?? '').split('\n')[0]?.trim() ?? '';
const S = (ev: EventMsg, k: string) => String((ev as Record<string, unknown>)[k] ?? '');

/** Accept both lifecycle event schemas (`round_index` and legacy `round`). */
const roundNo = (ev: EventMsg): string | number => {
  const row = ev as Record<string, unknown>;
  const value = row.round_index ?? row.round;
  return typeof value === 'string' || typeof value === 'number' ? value : '?';
};

const ROLE_LABEL: Record<string, string> = {
  manager: 'Manager',
  planner: 'Planner',
  engineer: 'Engineer',
  reviewer: 'Reviewer',
  critic: 'Critic',
  system: 'Argus',
};

export const toneColor = (tone: Tone): string =>
  ({
    bright: theme.ink,
    dim: theme.inkDim,
    accent: theme.accent,
    ok: theme.success,
    warn: theme.warning,
    err: theme.error,
    info: theme.info,
  }[tone]);

/** Is this an inner-monologue reasoning event? (hidden by default, ⌘O shows it) */
/**
 * Render one event to a feed line, or return null to HIDE it (the default for
 * any type not in the whitelist — including agent_io.* raw framing).
 */
export function renderEvent(ev: EventMsg): Rendered | null {
  const t = S(ev, 'type');

  if (t === 'ui.operator') {
    const body = S(ev, 'text');
    return body ? { role: 'operator', label: 'You', glyph: '›', text: body, tone: 'bright', rule: true } : null;
  }
  if (t === 'ui.argus') {
    const body = S(ev, 'text');
    return body ? { role: 'manager', label: 'Argus', glyph: '◆', text: body, tone: 'bright', rule: true } : null;
  }

  // ── engineer.progress: split by kind (model speech vs operations vs reasoning)
  if (t === 'engineer.progress') {
    const kind = S(ev, 'kind');
    const layer = S(ev, 'agent_layer') || 'engineer';
    const text = firstLine((ev as Record<string, unknown>).text ?? (ev as Record<string, unknown>).action_summary);
    if (kind === 'reasoning') {
      const body = trunc(S(ev, 'text'), 180);
      if (!body) return null;
      return { role: layer, label: ROLE_LABEL[layer] || layer, glyph: '🧠', text: body, tone: 'dim', reasoning: true };
    }
    if (kind === 'assistant_message' || kind === 'agent_message' || kind === 'message') {
      if (isStructuredAgentPayload(ev)) return null;
      const body = trunc(S(ev, 'text'), 280);
      if (!body) return null;
      return { role: layer, label: ROLE_LABEL[layer] || layer, glyph: '▌', text: body, tone: 'bright' };
    }
    if (kind === 'command_execution') {
      const cmd = trunc(S(ev, 'action_summary') || S(ev, 'command') || text, 160);
      if (!cmd) return null;
      return { role: layer, label: ROLE_LABEL[layer] || layer, glyph: '▸ $', text: cmd, tone: 'dim' };
    }
    if (kind === 'file_change') {
      const f = trunc(text, 160);
      return { role: layer, label: ROLE_LABEL[layer] || layer, glyph: '✎', text: f || '(file change)', tone: 'dim' };
    }
    if (kind === 'tool_use') {
      const tu = trunc(text, 160);
      return { role: layer, label: ROLE_LABEL[layer] || layer, glyph: '⚙', text: tu || '(tool)', tone: 'dim' };
    }
    if (!text) return null;
    return { role: layer, label: ROLE_LABEL[layer] || layer, glyph: '▸', text: trunc(text, 160), tone: 'dim' };
  }

  // ── Manager triage
  if (t === 'life.manager.intent.started')
    return { role: 'manager', label: 'Manager', glyph: '🧭', text: '判断任务归属…', tone: 'info' };
  if (t === 'life.manager.intent.completed')
    return { role: 'manager', label: 'Manager', glyph: '🧭', text: `→ ${S(ev, 'vertical') || S(ev, 'kind') || 'resolved'}`, tone: 'info' };
  if (t === 'life.manager.intent.failed')
    return { role: 'manager', label: 'Manager', glyph: '⚠', text: `分流失败 ${trunc(S(ev, 'error'), 140)}`, tone: 'err' };
  if (t === 'life.manager.stage_decision') {
    const target = S(ev, 'target_stage') || S(ev, 'stage') || S(ev, 'current_stage');
    return { role: 'manager', label: 'Manager', glyph: '🧭', text: `${S(ev, 'action')}${target ? ` → ${target}` : ''} ${trunc(S(ev, 'reason'), 120)}`, tone: 'info' };
  }

  // ── Planner
  if (t === 'life.planner.start')
    return { role: 'planner', label: 'Planner', glyph: '📋', text: `planning ${trunc(S(ev, 'objective'), 140)}`, tone: 'accent' };
  if (t === 'life.planner.verdict') {
    const done = S(ev, 'status') === 'done' || (ev as Record<string, unknown>).project_done === true;
    return done
      ? { role: 'planner', label: 'Planner', glyph: '🏁', text: 'project done', tone: 'ok' }
      : { role: 'planner', label: 'Planner', glyph: '📋', text: `queued ${S(ev, 'queued') || S(ev, 'n') || 'next'} task(s)`, tone: 'accent' };
  }
  if (t === 'life.planner.task_added')
    return { role: 'planner', label: 'Planner', glyph: '＋', text: `added ${trunc(S(ev, 'title') || S(ev, 'objective'), 140)}`, tone: 'accent' };
  if (t === 'life.planner.task_skipped')
    return { role: 'planner', label: 'Planner', glyph: '⏭', text: `skipped duplicate ${trunc(S(ev, 'title'), 120)}`, tone: 'dim' };
  if (t === 'life.planner.error')
    return { role: 'planner', label: 'Planner', glyph: '⚠', text: `planner error ${trunc(S(ev, 'error') || S(ev, 'text'), 140)}`, tone: 'err' };

  // ── Mission / round lifecycle
  if (t === 'life.mission.started' || t === 'mission.started')
    return { role: 'engineer', label: 'Engineer', glyph: '🚀', text: trunc(S(ev, 'title') || S(ev, 'objective') || S(ev, 'text') || 'mission started', 160), tone: 'info', rule: true };
  if (t === 'round.started' || t === 'round.start')
    return { role: 'engineer', label: 'Engineer', glyph: '──', text: `round ${roundNo(ev)}`, tone: 'dim', rule: true };
  if (t === 'life.phase.started') {
    const phase = S(ev, 'label') || S(ev, 'phase');
    if (!phase) return null;
    const role = S(ev, 'agent_layer') || 'engineer';
    return { role, label: ROLE_LABEL[role] || role, glyph: '🔄', text: `进入 ${phase}`, tone: 'info' };
  }
  if (t === 'round.review.started')
    return { role: 'reviewer', label: 'Reviewer', glyph: '🔄', text: `review round ${roundNo(ev)}`, tone: 'info' };
  if (t === 'round.main.completed')
    return { role: 'engineer', label: 'Engineer', glyph: '✅', text: `round ${roundNo(ev)} completed`, tone: 'info' };
  if (t === 'round.review.completed') {
    const st = S(ev, 'status');
    const tone: Tone = st === 'done' ? 'ok' : st === 'blocked' || st === 'no_progress' ? 'err' : 'warn';
    const glyph = st === 'done' ? '✅' : st === 'blocked' || st === 'no_progress' ? '⛔' : '↻';
    return { role: 'reviewer', label: 'Reviewer', glyph, text: `${st || '?'} · ${trunc(S(ev, 'reason'), 160)}`, tone };
  }
  if (t === 'life.iteration.critic')
    return { role: 'critic', label: 'Critic', glyph: '👔', text: `${S(ev, 'decision') || ''} ${trunc(S(ev, 'reason'), 140)}`, tone: 'info' };
  if (t === 'life.iteration.continued')
    return { role: 'critic', label: 'Critic', glyph: '🔁', text: 'queued next iteration', tone: 'dim' };
  if (t === 'life.mission.completed' || t === 'mission.completed' || t === 'loop.completed') {
    const ok = (ev as Record<string, unknown>).success !== false;
    return { role: 'engineer', label: 'Engineer', glyph: ok ? '🎉' : '💥', text: `mission ${ok ? S(ev, 'status') || 'done' : 'failed'}`, tone: ok ? 'ok' : 'err', rule: true };
  }
  if (t === 'life.mission.failed' || t === 'mission.error')
    return { role: 'engineer', label: 'Engineer', glyph: '❌', text: `mission failed ${trunc(S(ev, 'reason') || S(ev, 'error'), 140)}`, tone: 'err', rule: true };
  if (t === 'loop.start')
    return { role: 'engineer', label: 'Engineer', glyph: '▶', text: trunc(S(ev, 'text') || S(ev, 'objective'), 160), tone: 'info' };
  if (t === 'loop.done')
    return { role: 'engineer', label: 'Engineer', glyph: '🏁', text: `loop done ${trunc(S(ev, 'text'), 120)}`, tone: 'dim' };

  // ── inbox / reports (accent)
  if (t === 'life.inbox.queued')
    return { role: 'system', label: 'You', glyph: '📥', text: `nudge · ${trunc(S(ev, 'text'), 160)}`, tone: 'accent' };
  if (t === 'final.report.ready' || t === 'pptx.report.ready')
    return { role: 'system', label: 'Argus', glyph: '📄', text: 'report ready', tone: 'accent' };
  if (t === 'plan.completed')
    return { role: 'planner', label: 'Planner', glyph: '📋', text: 'plan completed', tone: 'accent' };
  if (t === 'daemon.stopping')
    return { role: 'system', label: 'Daemon', glyph: '🛑', text: 'stopping', tone: 'err' };

  // ── Guardian (监视守护) — Argus Panoptes keeping watch: the signals that fire
  // when a mission stalls, blocks, escalates, or a role backend fails. These are
  // the events that ACTUALLY persist to events.jsonl (the round.watchdog.* idle
  // "waits" are dropped as noise), so THIS is where the operator sees the guardian
  // at work. Anything flagged operator_alert is surfaced loud regardless of type.
  if (t === 'round.reviewer_backend_failure')
    return { role: 'system', label: 'Notice', glyph: '!', text: `reviewer backend down — holding · ${trunc(S(ev, 'text'), 150)}`, tone: 'err', rule: true };
  if (t === 'round.stall')
    return { role: 'system', label: 'Notice', glyph: '!', text: trunc(S(ev, 'text') || 'no forward progress', 170), tone: 'warn' };
  if (t === 'round.escalated')
    return { role: 'system', label: 'Notice', glyph: '!', text: trunc(S(ev, 'text') || 'soft round limit — escalating external blockers', 170), tone: 'warn' };
  if (t === 'life.planner.stall_escalation')
    return { role: 'system', label: 'Notice', glyph: '!', text: `planner stalled — ${trunc(S(ev, 'reason') || S(ev, 'text'), 150)}`, tone: 'warn' };
  if (t === 'life.budget.pause')
    return { role: 'system', label: 'Watch', glyph: '⏸', text: `budget cap reached — paused · ${trunc(S(ev, 'text') || S(ev, 'reason'), 140)}`, tone: 'warn' };
  if (t === 'life.lifecycle.block') return null;
  if (t === 'life.daemon.idle_timeout')
    return { role: 'system', label: 'Watch', glyph: '🟦', text: trunc(S(ev, 'text') || 'idle timeout — standing by', 150), tone: 'dim' };
  // round.watchdog.* only reach the feed in "full" verbosity — still render them.
  if (t === 'round.watchdog.restart_requested')
    return { role: 'system', label: 'Watch', glyph: '🔄', text: `stall caught — restarting the round · ${trunc(S(ev, 'reason'), 160)}`, tone: 'warn' };
  if (t === 'engineer.failure_nudge')
    return { role: 'engineer', label: 'Engineer', glyph: '⚠', text: `repeated tool failure — ${trunc(S(ev, 'text') || S(ev, 'reason'), 160)}`, tone: 'warn' };
  if (t === 'mission.idle')
    return { role: 'system', label: 'Argus', glyph: '🟦', text: trunc(S(ev, 'text') || 'idle — awaiting the next mission', 160), tone: 'dim' };
  // Catch-all: any event the daemon flagged for the operator's eyes, surfaced
  // even if its type has no bespoke renderer (harness marks it, cockpit shows it).
  if ((ev as Record<string, unknown>).operator_alert === true) {
    const body = trunc(S(ev, 'text') || S(ev, 'reason') || t, 170);
    if (body) return { role: 'system', label: 'Notice', glyph: '!', text: body, tone: 'err', rule: true };
  }

  // Everything else (agent_io.*, life.telemetry, internal bookkeeping) → hidden.
  return null;
}

/** Stable key for a stream event (dedup + React list key). */
export function eventKey(ev: EventMsg, i: number): string {
  void i;
  return sharedEventKey(ev);
}
