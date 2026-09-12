import { theme } from './theme.js';
import type { EventMsg } from './api.js';
import {
  isReasoning,
  isStructuredAgentPayload,
  mergeFragment,
  visibleAgentText,
} from '../../core/src/events.js';
import { formatMissionRouting } from '../../core/src/missionView.js';
import { missionOutcomePresentation } from '../../core/src/missionOutcome.js';
import {
  renderLine,
  type RenderContext,
  type RenderedLine,
} from '../../core/src/eventRender/index.js';

export { isReasoning, mergeFragment };

/**
 * Clean, whitelisted event rendering for the terminal — a port of the Python
 * cockpit (cli/event_format.py + apps/cli/_follow.py). The daemon's raw
 * events.jsonl is noisy: raw CLI framing (``agent.io.*``), telemetry, empty
 * progress. The REPL shows a WHITELIST — each meaningful event → role, glyph,
 * one clean line; everything the whitelist does not name goes through the
 * shared renderer (frontend/core/src/eventRender), which the web feed reads
 * for every event; what neither knows is HIDDEN.
 */

export type Tone = 'bright' | 'dim' | 'accent' | 'ok' | 'warn' | 'err' | 'info';

export interface Rendered {
  role: string;
  label: string;
  glyph: string;
  text: string;
  tone: Tone;
  rule?: boolean; // a round/mission boundary — draw a divider
  reasoning?: boolean; // provider reasoning summary; rendered faint/italic
  expand?: boolean; // terminal delivery: preserve the complete wrapped body
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

function managerFailureText(ev: EventMsg): string {
  const phase = S(ev, 'phase');
  const cause = S(ev, 'cause') || S(ev, 'backend_error');
  const raw = S(ev, 'error');
  if (!phase || !cause) return `没能判断这个请求该归谁 ${trunc(raw, 160)}`;
  const phaseLabel: Record<string, string> = {
    backend: '模型服务',
    parse: '读取回答',
    contract: '回答格式：',
    timeout: '超时',
  };
  const attempts = Number((ev as Record<string, unknown>).attempts || 0);
  const attempt = attempts > 1 ? ` (第${attempts}次尝试)` : '';
  const summary = `没能判断这个请求该归谁 · ${phaseLabel[phase] || phase} ${cause}${attempt}`;
  return raw ? `${summary} · 原始错误: ${raw}` : summary;
}

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
    // Match pi: show provider-supplied reasoning summaries as quiet context.
    // Raw protocol/encrypted reasoning never reaches this event type.
    if (kind === 'reasoning') {
      const body = trunc(S(ev, 'text'), 280);
      return body
        ? { role: layer, label, glyph: '∴', text: body, tone: 'dim', reasoning: true }
        : null;
    }
    if (kind === 'assistant_message' || kind === 'agent_message' || kind === 'message') {
      if (isStructuredAgentPayload(ev)) return null;
      const body = visibleAgentText(S(ev, 'text'));
      return body ? {
        role: layer,
        label,
        glyph: '▌',
        text: body,
        tone: 'bright',
        expand: true,
      } : null;
    }
    if (kind === 'command_execution') {
      const body = S(ev, 'text') || S(ev, 'command') || S(ev, 'action_summary');
      return body ? {
        role: layer,
        label,
        glyph: '▸ $',
        text: body,
        tone: S(ev, 'status') === 'failed' ? 'err' : 'dim',
        expand: true,
      } : null;
    }
    if (kind === 'tool_use' || kind === 'file_change') {
      const body = S(ev, 'text') || S(ev, 'action_summary');
      return body ? {
        role: layer,
        label,
        glyph: kind === 'file_change' ? '✎' : '⚙',
        text: body,
        tone: S(ev, 'status') === 'failed' ? 'err' : 'dim',
        expand: true,
      } : null;
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

  if (t === 'life.manager.intent.started') return { role: 'manager', label: 'Manager', glyph: '🧭', text: '判断这是什么样的请求…', tone: 'info' };
  if (t === 'life.manager.intent.completed') {
    const routing = formatMissionRouting({
      route: S(ev, 'route') || 'team',
      vertical: S(ev, 'vertical'),
      workflow_mode: S(ev, 'workflow_mode'),
      lifetime: S(ev, 'lifetime'),
      continuous: (ev as Record<string, unknown>).continuous === true,
      open_ended: (ev as Record<string, unknown>).open_ended === true,
    });
    return { role: 'manager', label: 'Manager', glyph: '🧭', text: `→ ${routing || S(ev, 'kind') || 'resolved'}`, tone: 'info' };
  }
  if (t === 'life.manager.intent.failed') return { role: 'manager', label: 'Manager', glyph: '⚠', text: managerFailureText(ev), tone: 'err', expand: true };
  if (t === 'life.manager.stage_decision') {
    const target = S(ev, 'target_stage') || S(ev, 'stage') || S(ev, 'current_stage');
    return { role: 'manager', label: 'Manager', glyph: '🧭', text: `${S(ev, 'action')}${target ? ` → ${target}` : ''} ${trunc(S(ev, 'reason'), 140)}`, tone: 'info' };
  }
  if (t === 'life.research.second_reading') {
    const layer = S(ev, 'agent_layer') || 'manager';
    const supported = trunc(S(ev, 'supported'), 160);
    const base = 'reread the evidence and reworked the plan';
    return { role: layer, label: ROLE_LABEL[layer] || layer, glyph: '📖', text: supported ? `${base} · ${supported}` : base, tone: 'info' };
  }
  if (t === 'life.letter.written') {
    const layer = S(ev, 'agent_layer') || 'manager';
    return { role: layer, label: ROLE_LABEL[layer] || layer, glyph: '✉', text: 'wrote you a letter', tone: 'accent' };
  }

  if (t === 'life.planner.start') return { role: 'planner', label: 'Planner', glyph: '📋', text: `planning ${trunc(S(ev, 'objective'), 160)}`, tone: 'accent' };
  if (t === 'life.planner.verdict') {
    const done = S(ev, 'status') === 'done' || (ev as Record<string, unknown>).project_done === true;
    return done
      ? { role: 'planner', label: 'Planner', glyph: '🏁', text: 'the project is finished', tone: 'ok' }
      : { role: 'planner', label: 'Planner', glyph: '📋', text: `lined up ${S(ev, 'queued') || S(ev, 'n') || 'next'} task(s) to do next`, tone: 'accent' };
  }
  if (t === 'life.planner.task_added') return { role: 'planner', label: 'Planner', glyph: '＋', text: `added a task · ${trunc(S(ev, 'title') || S(ev, 'objective'), 160)}`, tone: 'accent' };
  if (t === 'life.planner.task_skipped') return { role: 'planner', label: 'Planner', glyph: '⏭', text: `skipped a task already planned ${trunc(S(ev, 'title'), 140)}`, tone: 'dim' };
  if (t === 'life.planner.error') return { role: 'planner', label: 'Planner', glyph: '⚠', text: `the Planner hit an error ${trunc(S(ev, 'error') || S(ev, 'text'), 160)}`, tone: 'err' };

  if (t === 'life.mission.started' || t === 'mission.started')
    return { role: 'engineer', label: 'Engineer', glyph: '🚀', text: trunc(S(ev, 'title') || S(ev, 'objective') || S(ev, 'text') || 'started work on this task', 180), tone: 'info', rule: true };
  if (t === 'round.started' || t === 'round.start')
    return { role: 'engineer', label: 'Engineer', glyph: '──', text: `round ${roundNo(ev)}`, tone: 'dim', rule: true };
  if (t === 'life.phase.started') {
    const phase = S(ev, 'label') || S(ev, 'phase');
    if (!phase) return null;
    const role = S(ev, 'agent_layer') || 'engineer';
    return { role, label: ROLE_LABEL[role] || role, glyph: '🔄', text: `进入 ${phase}`, tone: 'info' };
  }
  if (t === 'round.review.started') return { role: 'reviewer', label: 'Reviewer', glyph: '🔄', text: `the Reviewer checks round ${roundNo(ev)}`, tone: 'info' };
  if (t === 'round.review.deferred') return { role: 'engineer', label: 'Engineer', glyph: '↪', text: `carries on into the next round without a review · ${trunc(S(ev, 'next_step'), 180)}`, tone: 'info' };
  if (t === 'round.main.completed') return { role: 'engineer', label: 'Engineer', glyph: '✅', text: `finished round ${roundNo(ev)} of work`, tone: 'info' };
  if (t === 'round.review.completed') {
    if (ev.review_skipped === true)
      return { role: 'reviewer', label: 'Reviewer', glyph: '↪', text: `no review this round · ${trunc(S(ev, 'reason'), 200)}`, tone: 'info' };
    const st = S(ev, 'status');
    const tone: Tone = st === 'done' ? 'ok' : st === 'blocked' || st === 'no_progress' ? 'err' : 'warn';
    const glyph = st === 'done' ? '✅' : st === 'blocked' || st === 'no_progress' ? '⛔' : '↻';
    return { role: 'reviewer', label: 'Reviewer', glyph, text: `${reviewVerdict(st)} · ${trunc(S(ev, 'reason'), 200)}`, tone };
  }
  if (t === 'life.mission.completed' || t === 'mission.completed' || t === 'loop.completed') {
    const presentation = missionOutcomePresentation(ev);
    const summary = trunc(S(ev, 'summary'), 240);
    return {
      role: 'engineer',
      label: 'Engineer',
      glyph: presentation.glyph,
      text: summary ? `${presentation.label} · ${summary}` : presentation.label,
      tone: presentation.tone,
      rule: true,
    };
  }
  if (t === 'life.mission.failed' || t === 'mission.error')
    return { role: 'engineer', label: 'Engineer', glyph: '❌', text: `this task failed ${trunc(S(ev, 'reason') || S(ev, 'error'), 160)}`, tone: 'err', rule: true };
  if (t === 'loop.start') return { role: 'engineer', label: 'Engineer', glyph: '▶', text: trunc(S(ev, 'text') || S(ev, 'objective'), 16_000), tone: 'info', expand: true };
  if (t === 'loop.done') return { role: 'engineer', label: 'Engineer', glyph: '🏁', text: `the run finished ${trunc(S(ev, 'text'), 140)}`, tone: 'dim' };

  if (t === 'life.inbox.queued') return { role: 'system', label: 'You', glyph: '📥', text: `you added guidance · ${trunc(S(ev, 'text'), 180)}`, tone: 'accent' };
  if (t === 'daemon.parked') {
    return {
      role: 'system',
      label: 'Argus',
      glyph: 'Ⅱ',
      text: `Argus set this project aside with its state saved${S(ev, 'replaced_by') ? ` · continued as ${S(ev, 'replaced_by')}` : ''}`,
      tone: 'warn',
      rule: true,
    };
  }
  if (t === 'provider.request.denied') {
    return {
      role: 'system',
      label: 'Quota',
      glyph: '⏸',
      text: `a request to ${S(ev, 'provider') || 'the model service'} was held back · ${trunc(S(ev, 'reason'), 160)}`,
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
    return { role: 'system', label: 'Watch', glyph: '👁', text: `the Reviewer's model service is unreachable — waiting rather than going on unchecked · ${trunc(S(ev, 'text'), 150)}`, tone: 'err', rule: true };
  if (t === 'round.stall')
    return { role: 'system', label: 'Watch', glyph: '👁', text: trunc(S(ev, 'text') || 'no progress this round — keeping a close eye on it', 170), tone: 'warn' };
  if (t === 'round.escalated')
    return { role: 'system', label: 'Watch', glyph: '👁', text: trunc(S(ev, 'text') || 'many rounds without a finish — raising what is blocking the work', 170), tone: 'warn' };
  if (t === 'life.planner.stall_escalation')
    return { role: 'system', label: 'Watch', glyph: '👁', text: `the Planner is stuck — ${trunc(S(ev, 'reason') || S(ev, 'text'), 150)}`, tone: 'warn' };
  if (t === 'life.budget.pause')
    return { role: 'system', label: 'Watch', glyph: '⏸', text: `budget cap reached — paused · ${trunc(S(ev, 'text') || S(ev, 'reason'), 140)}`, tone: 'warn' };
  if (t === 'budget.reservation.denied')
    return { role: 'system', label: 'Budget', glyph: '$', text: `not enough budget for this step — ${trunc(S(ev, 'reason') || S(ev, 'text'), 160)}`, tone: 'err', rule: true };
  if (t === 'budget.unpriced.blocked')
    return { role: 'system', label: 'Budget', glyph: '$', text: `held until the cost of this step is known — ${trunc(S(ev, 'reason') || S(ev, 'text'), 160)}`, tone: 'err', rule: true };
  if (t === 'life.lifecycle.block')
    return { role: 'system', label: 'Watch', glyph: '⛔', text: `blocked — needs you · ${trunc(S(ev, 'text') || S(ev, 'reason'), 150)}`, tone: 'err', rule: true };
  if (t === 'life.daemon.idle_timeout')
    return { role: 'system', label: 'Watch', glyph: '🟦', text: trunc(S(ev, 'text') || 'nothing to do for a while — standing by', 150), tone: 'dim' };
  // Operator ↔ Manager conversation, injected locally so it flows inline with
  // the mission feed (the Manager reply lives in transcript, not events).
  if (t === 'ui.operator') return { role: 'system', label: 'You', glyph: '›', text: S(ev, 'text'), tone: 'accent', rule: true };
  if (t === 'ui.argus') {
    const body = S(ev, 'text');
    return body ? { role: 'manager', label: 'Argus', glyph: '▌', text: body, tone: 'bright' } : null;
  }
  // The step trail of a finished Manager turn, folded into the scrollback so the
  // operator can still read WHAT Argus did after the live status line is gone.
  if (t === 'ui.activity') {
    const body = S(ev, 'text');
    return body ? { role: 'manager', label: 'Steps', glyph: '⋮', text: body, tone: 'dim' } : null;
  }

  // Catalog events this whitelist does not enumerate render through the
  // shared semantic renderer, so the terminal shows the same line as every
  // other frontend instead of dropping the event.
  const shared = renderLine(ev, SHARED_RENDER_CONTEXT);
  if (shared) return fromSharedLine(shared);

  // Catch-all: any event the daemon flagged for the operator's eyes, surfaced
  // loud even if its type has no bespoke renderer above (harness marks it, the
  // cockpit shows it — the guardian never swallows an alert).
  if ((ev as Record<string, unknown>).operator_alert === true) {
    const body = trunc(S(ev, 'text') || S(ev, 'reason') || t, 170);
    if (body) return { role: 'system', label: 'Watch', glyph: '👁', text: body, tone: 'err', rule: true };
  }

  // Everything else (agent.io.*, internal bookkeeping) → hidden.
  return null;
}

const SHARED_RENDER_CONTEXT: RenderContext = {
  locale: 'en',
  showReasoning: true,
  unknownEventPolicy: 'hide',
  density: 'full',
};

/** A shared-renderer line in this file's Rendered shape. The terminal speaks
 *  as the watcher — every notice is labelled Watch, as the whitelist above does. */
function fromSharedLine(line: RenderedLine): Rendered {
  return {
    role: line.role,
    label: line.labelKey === 'event.notice' ? 'Watch' : line.label,
    glyph: line.glyph,
    text: line.text,
    tone: line.tone,
    ...(line.rule ? { rule: true } : {}),
    ...(line.reasoning ? { reasoning: true } : {}),
    ...(line.expand ? { expand: true } : {}),
  };
}

/** message_id for streaming coalescing (empty when the event is not a stream). */
export function messageId(ev: EventMsg): string {
  const rec = ev as Record<string, unknown>;
  const kind = String(rec.kind ?? '');
  if (
    String(rec.type) === 'engineer.progress'
    && ['assistant_message', 'agent_message', 'message', 'reasoning'].includes(kind)
  ) {
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

/** The Reviewer's verdict in words a reader outside the project understands. */
function reviewVerdict(status: string): string {
  const verdicts: Record<string, string> = {
    done: 'the Reviewer was satisfied',
    blocked: 'the Reviewer found the work stuck',
    no_progress: 'the Reviewer saw no progress',
  };
  if (!status) return 'the Reviewer gave no verdict';
  return verdicts[status] ?? 'the Reviewer asked for another pass';
}
