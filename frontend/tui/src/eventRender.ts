import { theme } from './theme.js';
import type { EventMsg } from './api.js';
import { isReasoning, isStructuredAgentPayload, mergeFragment } from '../../core/src/events.js';

export { isReasoning, mergeFragment };

/**
 * Clean, whitelisted event rendering for the terminal — the twin of the web's
 * lib/eventRender.ts and a faithful port of the Python cockpit
 * (cli/event_format.py + apps/cli/_follow.py). The daemon's raw events.jsonl is
 * noisy: raw CLI framing (``agent.io.*``), telemetry, empty progress. The REPL
 * shows a WHITELIST — each meaningful event → role, glyph, one clean line;
 * everything else is HIDDEN. No more ``agent.io.stream`` flooding the feed.
 */

export type Tone = 'bright' | 'dim' | 'accent' | 'ok' | 'warn' | 'err' | 'info';

export interface Rendered {
  role: string;
  label: string;
  glyph: string;
  text: string;
  tone: Tone;
  rule?: boolean; // a round/mission boundary — draw a divider
}

const ROLE_LABEL: Record<string, string> = {
  manager: 'Manager',
  planner: 'Planner',
  engineer: 'Engineer',
  reviewer: 'Reviewer',
  critic: 'Critic',
  system: 'Argus',
};

/** tone → an Ink-accepted colour (role hue for the label, tone for the body). */
export function toneColor(tone: Tone): string {
  switch (tone) {
    case 'bright': return 'white';
    case 'dim': return 'gray';
    case 'accent': return theme.accent;
    case 'ok': return theme.success;
    case 'warn': return theme.warning;
    case 'err': return theme.error;
    case 'info': return theme.info;
  }
}

export function roleColor(role: string): string {
  return theme.role[role] ?? 'gray';
}

function trunc(s: string, n: number): string {
  const t = (s || '').replace(/```[a-z]*\n?/gi, '').replace(/\[([^\]]+)\]\([^)]+\)/g, '[$1]').trim();
  return t.length <= n ? t : t.slice(0, n - 1).trimEnd() + '…';
}
const S = (ev: EventMsg, k: string) => String((ev as Record<string, unknown>)[k] ?? '');

/** Accept both lifecycle event schemas.  The supervised Engineer historically
 * emitted `round`, while other producers emitted `round_index`. */
const roundNo = (ev: EventMsg): string | number => {
  const row = ev as Record<string, unknown>;
  const value = row.round_index ?? row.round;
  return typeof value === 'string' || typeof value === 'number' ? value : '?';
};

/** Render one event to a clean line, or null to HIDE (default for anything not
 *  whitelisted — including agent.io.* raw framing). */
export function renderEvent(ev: EventMsg): Rendered | null {
  const t = S(ev, 'type');

  if (t === 'engineer.progress') {
    const kind = S(ev, 'kind');
    const layer = S(ev, 'agent_layer') || 'engineer';
    const label = ROLE_LABEL[layer] || 'Engineer';
    // Observable action details live in Ctrl+O. Keep the default transcript
    // focused on user-facing agent messages and settled milestones; never put
    // chain-of-thought, commands, or tool protocol into terminal scrollback.
    if (kind === 'reasoning') return null;
    if (kind === 'assistant_message' || kind === 'agent_message' || kind === 'message') {
      if (isStructuredAgentPayload(ev)) return null;
      const body = trunc(S(ev, 'text'), 280);
      return body ? { role: layer, label, glyph: '▌', text: body, tone: 'bright' } : null;
    }
    return null;
  }

  if (t === 'role.activity') {
    const status = S(ev, 'status');
    if (status === 'running') return null;
    const role = S(ev, 'role') || 'engineer';
    const milestone = (ev as Record<string, unknown>).milestone === true;
    if (status !== 'error' && !milestone) return null;
    return {
      role,
      label: ROLE_LABEL[role] || role,
      glyph: status === 'error' ? '✕' : '✓',
      text: trunc(S(ev, 'label') || 'activity completed', 180),
      tone: status === 'error' ? 'err' : 'ok',
    };
  }

  if (t === 'life.manager.intent.started') return { role: 'manager', label: 'Manager', glyph: '🧭', text: '判断任务归属…', tone: 'info' };
  if (t === 'life.manager.intent.completed') return { role: 'manager', label: 'Manager', glyph: '🧭', text: `→ ${S(ev, 'vertical') || S(ev, 'kind') || 'resolved'}`, tone: 'info' };
  if (t === 'life.manager.intent.failed') return { role: 'manager', label: 'Manager', glyph: '⚠', text: `分流失败 ${trunc(S(ev, 'error'), 160)}`, tone: 'err' };
  if (t === 'life.manager.stage_decision') {
    const target = S(ev, 'target_stage') || S(ev, 'stage') || S(ev, 'current_stage');
    return { role: 'manager', label: 'Manager', glyph: '🧭', text: `${S(ev, 'action')}${target ? ` → ${target}` : ''} ${trunc(S(ev, 'reason'), 140)}`, tone: 'info' };
  }

  if (t === 'life.planner.start') return { role: 'planner', label: 'Planner', glyph: '📋', text: `planning ${trunc(S(ev, 'objective'), 160)}`, tone: 'accent' };
  if (t === 'life.planner.verdict') {
    const done = S(ev, 'status') === 'done' || (ev as Record<string, unknown>).project_done === true;
    return done
      ? { role: 'planner', label: 'Planner', glyph: '🏁', text: 'project done', tone: 'ok' }
      : { role: 'planner', label: 'Planner', glyph: '📋', text: `queued ${S(ev, 'queued') || S(ev, 'n') || 'next'} task(s)`, tone: 'accent' };
  }
  if (t === 'life.planner.task_added') return { role: 'planner', label: 'Planner', glyph: '＋', text: `added ${trunc(S(ev, 'title') || S(ev, 'objective'), 160)}`, tone: 'accent' };
  if (t === 'life.planner.task_skipped') return { role: 'planner', label: 'Planner', glyph: '⏭', text: `skipped duplicate ${trunc(S(ev, 'title'), 140)}`, tone: 'dim' };
  if (t === 'life.planner.error') return { role: 'planner', label: 'Planner', glyph: '⚠', text: `planner error ${trunc(S(ev, 'error') || S(ev, 'text'), 160)}`, tone: 'err' };

  if (t === 'life.mission.started' || t === 'mission.started')
    return { role: 'engineer', label: 'Engineer', glyph: '🚀', text: trunc(S(ev, 'title') || S(ev, 'objective') || S(ev, 'text') || 'mission started', 180), tone: 'info', rule: true };
  if (t === 'round.started' || t === 'round.start')
    return { role: 'engineer', label: 'Engineer', glyph: '──', text: `round ${roundNo(ev)}`, tone: 'dim', rule: true };
  if (t === 'life.phase.started') {
    const phase = S(ev, 'label') || S(ev, 'phase');
    if (!phase) return null;
    const role = S(ev, 'agent_layer') || 'engineer';
    return { role, label: ROLE_LABEL[role] || role, glyph: '🔄', text: `进入 ${phase}`, tone: 'info' };
  }
  if (t === 'round.review.started') return { role: 'reviewer', label: 'Reviewer', glyph: '🔄', text: `review round ${roundNo(ev)}`, tone: 'info' };
  if (t === 'round.review.deferred') return { role: 'engineer', label: 'Engineer', glyph: '↪', text: `continues before review · ${trunc(S(ev, 'next_step'), 180)}`, tone: 'info' };
  if (t === 'round.main.completed') return { role: 'engineer', label: 'Engineer', glyph: '✅', text: `round ${roundNo(ev)} completed`, tone: 'info' };
  if (t === 'round.review.completed') {
    const st = S(ev, 'status');
    const tone: Tone = st === 'done' ? 'ok' : st === 'blocked' || st === 'no_progress' ? 'err' : 'warn';
    const glyph = st === 'done' ? '✅' : st === 'blocked' || st === 'no_progress' ? '⛔' : '↻';
    return { role: 'reviewer', label: 'Reviewer', glyph, text: `${st || '?'} · ${trunc(S(ev, 'reason'), 200)}`, tone };
  }
  if (t === 'life.iteration.critic') return { role: 'critic', label: 'Critic', glyph: '👔', text: `${S(ev, 'decision') || ''} ${trunc(S(ev, 'reason'), 160)}`, tone: 'info' };
  if (t === 'life.iteration.continued') return { role: 'critic', label: 'Critic', glyph: '🔁', text: 'queued next iteration', tone: 'dim' };
  if (t === 'life.mission.completed' || t === 'mission.completed' || t === 'loop.completed') {
    const ok = (ev as Record<string, unknown>).success !== false;
    return { role: 'engineer', label: 'Engineer', glyph: ok ? '🎉' : '💥', text: `mission ${ok ? S(ev, 'status') || 'done' : 'failed'}`, tone: ok ? 'ok' : 'err', rule: true };
  }
  if (t === 'life.mission.failed' || t === 'mission.error')
    return { role: 'engineer', label: 'Engineer', glyph: '❌', text: `mission failed ${trunc(S(ev, 'reason') || S(ev, 'error'), 160)}`, tone: 'err', rule: true };
  if (t === 'loop.start') return { role: 'engineer', label: 'Engineer', glyph: '▶', text: trunc(S(ev, 'text') || S(ev, 'objective'), 180), tone: 'info' };
  if (t === 'loop.done') return { role: 'engineer', label: 'Engineer', glyph: '🏁', text: `loop done ${trunc(S(ev, 'text'), 140)}`, tone: 'dim' };

  if (t === 'life.inbox.queued') return { role: 'system', label: 'You', glyph: '📥', text: `nudge · ${trunc(S(ev, 'text'), 180)}`, tone: 'accent' };
  if (t === 'final.report.ready' || t === 'pptx.report.ready') return { role: 'system', label: 'Argus', glyph: '📄', text: 'report ready', tone: 'accent' };
  if (t === 'plan.completed') return { role: 'planner', label: 'Planner', glyph: '📋', text: 'plan completed', tone: 'accent' };
  if (t === 'daemon.stopping') return { role: 'system', label: 'Daemon', glyph: '🛑', text: 'stopping', tone: 'err' };
  if (t === 'daemon.parked') {
    return {
      role: 'system',
      label: 'Argus',
      glyph: 'Ⅱ',
      text: `session parked · state saved${S(ev, 'replaced_by') ? ` · replaced by ${S(ev, 'replaced_by')}` : ''}`,
      tone: 'warn',
      rule: true,
    };
  }
  if (t === 'provider.request.denied') {
    return {
      role: 'system',
      label: 'Quota',
      glyph: '⏸',
      text: `${S(ev, 'provider') || 'provider'} request blocked · ${trunc(S(ev, 'reason'), 160)}`,
      tone: 'warn',
      rule: true,
    };
  }

  // ── Guardian (监视守护) — Argus Panoptes keeping watch: the signals that fire
  // when a mission stalls, blocks, escalates, or a role backend fails. These are
  // the events that ACTUALLY persist to events.jsonl (the round.watchdog.* idle
  // "waits" are dropped as noise), so THIS is where the operator sees the guardian
  // at work. The hundred-eyed watcher voice; anything flagged operator_alert is
  // surfaced loud regardless of type.
  if (t === 'round.reviewer_backend_failure')
    return { role: 'system', label: 'Watch', glyph: '👁', text: `reviewer backend down — holding, won't continue blind · ${trunc(S(ev, 'text'), 150)}`, tone: 'err', rule: true };
  if (t === 'round.stall')
    return { role: 'system', label: 'Watch', glyph: '👁', text: trunc(S(ev, 'text') || 'no forward progress — watching closely', 170), tone: 'warn' };
  if (t === 'round.escalated')
    return { role: 'system', label: 'Watch', glyph: '👁', text: trunc(S(ev, 'text') || 'soft round limit — escalating external blockers', 170), tone: 'warn' };
  if (t === 'life.planner.stall_escalation')
    return { role: 'system', label: 'Watch', glyph: '👁', text: `planner stalled — ${trunc(S(ev, 'reason') || S(ev, 'text'), 150)}`, tone: 'warn' };
  if (t === 'life.budget.pause')
    return { role: 'system', label: 'Watch', glyph: '⏸', text: `budget cap reached — paused · ${trunc(S(ev, 'text') || S(ev, 'reason'), 140)}`, tone: 'warn' };
  if (t === 'life.lifecycle.block')
    return { role: 'system', label: 'Watch', glyph: '⛔', text: `blocked — needs you · ${trunc(S(ev, 'text') || S(ev, 'reason'), 150)}`, tone: 'err', rule: true };
  if (t === 'life.daemon.idle_timeout')
    return { role: 'system', label: 'Watch', glyph: '🟦', text: trunc(S(ev, 'text') || 'idle timeout — standing by', 150), tone: 'dim' };
  // round.watchdog.* only reach the feed in "full" verbosity — still render them.
  if (t === 'round.watchdog.restart_requested')
    return { role: 'system', label: 'Watch', glyph: '🔄', text: `stall caught — restarting the round · ${trunc(S(ev, 'reason'), 170)}`, tone: 'warn' };
  if (t === 'engineer.failure_nudge')
    return { role: 'engineer', label: 'Engineer', glyph: '⚠', text: `repeated tool failure — ${trunc(S(ev, 'text') || S(ev, 'reason'), 170)}`, tone: 'warn' };
  if (t === 'mission.idle')
    return { role: 'system', label: 'Argus', glyph: '🟦', text: trunc(S(ev, 'text') || 'idle — awaiting the next mission', 160), tone: 'dim' };
  // Catch-all: any event the daemon flagged for the operator's eyes, surfaced
  // loud even if its type has no bespoke renderer above (harness marks it, the
  // cockpit shows it — the guardian never swallows an alert).
  if ((ev as Record<string, unknown>).operator_alert === true) {
    const body = trunc(S(ev, 'text') || S(ev, 'reason') || t, 170);
    if (body) return { role: 'system', label: 'Watch', glyph: '👁', text: body, tone: 'err', rule: true };
  }

  // Operator ↔ Manager conversation, injected locally so it flows inline with
  // the mission feed (the Manager reply lives in transcript, not events).
  if (t === 'ui.operator') return { role: 'system', label: 'You', glyph: '›', text: S(ev, 'text'), tone: 'accent', rule: true };
  if (t === 'ui.argus') {
    const body = S(ev, 'text');
    return body ? { role: 'manager', label: 'Argus', glyph: '▌', text: body, tone: 'bright' } : null;
  }

  // Everything else (agent.io.*, life.telemetry, internal bookkeeping) → hidden.
  return null;
}

/** message_id for streaming coalescing (empty when the event is not a stream). */
export function messageId(ev: EventMsg): string {
  const rec = ev as Record<string, unknown>;
  const kind = String(rec.kind ?? '');
  if (String(rec.type) === 'engineer.progress' && ['assistant_message', 'agent_message', 'message'].includes(kind)) {
    return String(rec.message_id ?? '');
  }
  // The locally-injected Manager reply carries a message_id too, so its blocks
  // coalesce into ONE growing row that stays live (out of <Static>) while it
  // streams — otherwise a multi-block reply would freeze after the first block.
  if (String(rec.type) === 'ui.argus') {
    return String(rec.message_id ?? '');
  }
  return '';
}

/**
 * Merge a new fragment into the accumulated text of a streaming message. The
 * daemon delivers a message under one message_id as several fragments — usually
 * SEPARATE blocks (paragraphs), occasionally a cumulative resend. Keeping only
 * the longest DROPS blocks (the message looked truncated / "frozen"); we instead
 * grow the message: cumulative resend replaces, a duplicate is skipped, a new
 * block is appended. Result: the full reply streams in, nothing lost.
 */
