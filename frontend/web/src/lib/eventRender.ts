import type { EventMsg } from '../api';
import { theme } from './theme';
import { missionOutcomePresentation } from '../../../core/src';
import { formatMissionRouting } from '../../../core/src/missionView';
import {
  eventKey as sharedEventKey,
  isReasoning,
  isStructuredAgentPayload,
  mergeFragment,
  visibleAgentText,
} from '../../../core/src/events';
import type { Locale } from '../i18n';

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

function managerFailureText(ev: EventMsg, locale: Locale): string {
  const l = (en: string, zh: string) => locale === 'zh-CN' ? zh : en;
  const phase = S(ev, 'phase');
  const cause = S(ev, 'cause') || S(ev, 'backend_error');
  const raw = S(ev, 'error');
  if (!phase || !cause) return `${l('could not work out where this request belongs', '没能判断这个请求该归谁')} ${trunc(raw, 140)}`;
  const phaseLabel: Record<string, string> = {
    backend: l('model service', '模型服务'),
    parse: l('reading the answer', '读取回答'),
    contract: l('answer shape:', '回答格式：'),
    timeout: l('timed out', '超时'),
  };
  const attempts = Number((ev as Record<string, unknown>).attempts || 0);
  const attempt = attempts > 1
    ? l(` (attempt ${attempts})`, ` (第${attempts}次尝试)`)
    : '';
  const summary = `${l('could not work out where this request belongs', '没能判断这个请求该归谁')} · ${phaseLabel[phase] || phase} ${cause}${attempt}`;
  return raw ? `${summary} · ${l('error text', '原始错误')}: ${raw}` : summary;
}

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
const ROLE_LABEL_ZH: Record<string, string> = {
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

/** Reasoning summaries are shown softly by default; ⌘/Ctrl+T can hide them. */
/**
 * Render one event to a feed line, or return null to HIDE it (the default for
 * any type not in the whitelist — including agent_io.* raw framing).
 */
export function renderEvent(ev: EventMsg, locale: Locale = 'en'): Rendered | null {
  const t = S(ev, 'type');
  const l = (english: string, chinese: string) => locale === 'zh-CN' ? chinese : english;
  const roleLabel = (role: string) => (locale === 'zh-CN' ? ROLE_LABEL_ZH : ROLE_LABEL)[role] || role;

  if (t === 'ui.operator') {
    const body = visibleAgentText(S(ev, 'text'));
    return body ? { role: 'operator', label: l('You', '你'), glyph: '›', text: body, tone: 'bright', rule: true } : null;
  }
  if (t === 'ui.argus') {
    const body = S(ev, 'text');
    // A reply that is still being worked on has steps before it has words.
    const hasSteps = Array.isArray(ev.steps) && ev.steps.length > 0;
    return body || hasSteps ? { role: 'manager', label: 'Argus', glyph: '◆', text: body, tone: 'bright', rule: true } : null;
  }

  // ── engineer.progress: split by kind (model speech vs operations vs reasoning)
  if (t === 'engineer.progress') {
    const kind = S(ev, 'kind');
    const layer = S(ev, 'agent_layer') || 'engineer';
    const text = firstLine((ev as Record<string, unknown>).text ?? (ev as Record<string, unknown>).action_summary);
    if (kind === 'reasoning') {
      const body = trunc(S(ev, 'text'), 280);
      if (!body) return null;
      return { role: layer, label: roleLabel(layer), glyph: '∴', text: body, tone: 'dim', reasoning: true };
    }
    if (kind === 'assistant_message' || kind === 'agent_message' || kind === 'message') {
      if (isStructuredAgentPayload(ev)) return null;
      const body = visibleAgentText(S(ev, 'text'));
      if (!body) return null;
      return { role: layer, label: roleLabel(layer), glyph: '▌', text: body, tone: 'bright' };
    }
    if (kind === 'command_execution') {
      const cmd = S(ev, 'text') || S(ev, 'command') || S(ev, 'action_summary');
      if (!cmd) return null;
      return { role: layer, label: roleLabel(layer), glyph: '▸ $', text: cmd, tone: 'dim' };
    }
    if (kind === 'file_change') {
      const f = S(ev, 'text') || S(ev, 'action_summary');
      return { role: layer, label: roleLabel(layer), glyph: '✎', text: f || l('(file change)', '（文件变更）'), tone: 'dim' };
    }
    if (kind === 'tool_use') {
      const tu = S(ev, 'text') || S(ev, 'action_summary');
      return { role: layer, label: roleLabel(layer), glyph: '⚙', text: tu || l('(tool)', '（工具）'), tone: 'dim' };
    }
    if (!text) return null;
    return { role: layer, label: roleLabel(layer), glyph: '▸', text: trunc(text, 160), tone: 'dim' };
  }

  // ── Manager triage
  if (t === 'life.manager.intent.started')
    return { role: 'manager', label: 'Manager', glyph: '🧭', text: l('working out what kind of request this is…', '判断这是什么样的请求…'), tone: 'info' };
  if (t === 'life.manager.intent.completed') {
    const routing = formatMissionRouting({
      route: S(ev, 'route') || 'team',
      vertical: S(ev, 'vertical'),
      workflow_mode: S(ev, 'workflow_mode'),
      lifetime: S(ev, 'lifetime'),
      continuous: (ev as Record<string, unknown>).continuous === true,
      open_ended: (ev as Record<string, unknown>).open_ended === true,
    });
    return { role: 'manager', label: 'Manager', glyph: '🧭', text: `→ ${routing || S(ev, 'kind') || l('resolved', '已确定')}`, tone: 'info' };
  }
  if (t === 'life.manager.intent.failed')
    return { role: 'manager', label: 'Manager', glyph: '⚠', text: managerFailureText(ev, locale), tone: 'err' };
  if (t === 'life.manager.stage_decision') {
    const target = S(ev, 'target_stage') || S(ev, 'stage') || S(ev, 'current_stage');
    return { role: 'manager', label: 'Manager', glyph: '🧭', text: `${S(ev, 'action')}${target ? ` → ${target}` : ''} ${trunc(S(ev, 'reason'), 120)}`, tone: 'info' };
  }
  if (t === 'life.research.second_reading') {
    const layer = S(ev, 'agent_layer') || 'manager';
    const supported = trunc(S(ev, 'supported'), 160);
    const base = l('reread the evidence and reworked the plan', '重读了证据并重排了计划');
    return { role: layer, label: roleLabel(layer), glyph: '📖', text: supported ? `${base} · ${supported}` : base, tone: 'info' };
  }
  if (t === 'life.letter.written') {
    const layer = S(ev, 'agent_layer') || 'manager';
    return { role: layer, label: roleLabel(layer), glyph: '✉', text: l('wrote you a letter', '给你写了一封信'), tone: 'accent' };
  }

  // ── Planner
  if (t === 'life.planner.start')
    return { role: 'planner', label: 'Planner', glyph: '📋', text: `${l('planning', '正在规划')} ${trunc(S(ev, 'objective'), 140)}`, tone: 'accent' };
  if (t === 'life.planner.verdict') {
    const done = S(ev, 'status') === 'done' || (ev as Record<string, unknown>).project_done === true;
    return done
      ? { role: 'planner', label: 'Planner', glyph: '🏁', text: l('the project is finished', '项目已完成'), tone: 'ok' }
      : { role: 'planner', label: 'Planner', glyph: '📋', text: l(`lined up ${S(ev, 'queued') || S(ev, 'n') || 'next'} task(s) to do next`, `已排好 ${S(ev, 'queued') || S(ev, 'n') || '下一'} 个接下来要做的任务`), tone: 'accent' };
  }
  if (t === 'life.planner.task_added')
    return { role: 'planner', label: 'Planner', glyph: '＋', text: `${l('added a task', '新增任务')} · ${trunc(S(ev, 'title') || S(ev, 'objective'), 140)}`, tone: 'accent' };
  if (t === 'life.planner.task_skipped')
    return { role: 'planner', label: 'Planner', glyph: '⏭', text: `${l('skipped a task already planned', '跳过了已在计划中的任务')} ${trunc(S(ev, 'title'), 120)}`, tone: 'dim' };
  if (t === 'life.planner.error')
    return { role: 'planner', label: 'Planner', glyph: '⚠', text: `${l('the Planner hit an error', '规划者出错')} ${trunc(S(ev, 'error') || S(ev, 'text'), 140)}`, tone: 'err' };

  // ── Mission / round lifecycle
  if (t === 'life.mission.started' || t === 'mission.started')
    return { role: 'engineer', label: 'Engineer', glyph: '🚀', text: trunc(S(ev, 'title') || S(ev, 'objective') || S(ev, 'text') || l('started work on this task', '开始做这项任务'), 160), tone: 'info', rule: true };
  if (t === 'round.started' || t === 'round.start')
    return { role: 'engineer', label: 'Engineer', glyph: '──', text: l(`round ${roundNo(ev)}`, `第 ${roundNo(ev)} 轮`), tone: 'dim', rule: true };
  if (t === 'life.phase.started') {
    const phase = S(ev, 'label') || S(ev, 'phase');
    if (!phase) return null;
    const role = S(ev, 'agent_layer') || 'engineer';
    return { role, label: roleLabel(role), glyph: '🔄', text: l(`entering ${phase}`, `进入 ${phase}`), tone: 'info' };
  }
  if (t === 'round.review.started')
    return { role: 'reviewer', label: 'Reviewer', glyph: '🔄', text: l(`the Reviewer checks round ${roundNo(ev)}`, `审阅者检查第 ${roundNo(ev)} 轮`), tone: 'info' };
  if (t === 'round.review.deferred')
    return { role: 'engineer', label: 'Engineer', glyph: '↪', text: l(`carries on into the next round without a review · ${trunc(S(ev, 'next_step'), 160)}`, `不经审阅直接进入下一轮 · ${trunc(S(ev, 'next_step'), 160)}`), tone: 'info' };
  if (t === 'round.main.completed')
    return { role: 'engineer', label: 'Engineer', glyph: '✅', text: l(`finished round ${roundNo(ev)} of work`, `第 ${roundNo(ev)} 轮工作已完成`), tone: 'info' };
  if (t === 'round.review.completed') {
    if (ev.review_skipped === true)
      return { role: 'reviewer', label: 'Reviewer', glyph: '↪', text: `${l('no review this round', '这一轮没有审阅')} · ${trunc(S(ev, 'reason'), 160)}`, tone: 'info' };
    const st = S(ev, 'status');
    const tone: Tone = st === 'done' ? 'ok' : st === 'blocked' || st === 'no_progress' ? 'err' : 'warn';
    const glyph = st === 'done' ? '✅' : st === 'blocked' || st === 'no_progress' ? '⛔' : '↻';
    return { role: 'reviewer', label: 'Reviewer', glyph, text: `${reviewVerdict(st, l)} · ${trunc(S(ev, 'reason'), 160)}`, tone };
  }
  if (t === 'life.iteration.critic')
    return { role: 'critic', label: 'Critic', glyph: '👔', text: `${S(ev, 'decision') || ''} ${trunc(S(ev, 'reason'), 140)}`, tone: 'info' };
  if (t === 'life.iteration.continued')
    return { role: 'critic', label: 'Critic', glyph: '🔁', text: l('lined up the next iteration', '排好了下一轮迭代'), tone: 'dim' };
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
    return { role: 'engineer', label: 'Engineer', glyph: '❌', text: `${l('this task failed', '这项任务失败了')} ${trunc(S(ev, 'reason') || S(ev, 'error'), 140)}`, tone: 'err', rule: true };
  if (t === 'loop.start')
    return { role: 'engineer', label: 'Engineer', glyph: '▶', text: trunc(S(ev, 'text') || S(ev, 'objective'), 160), tone: 'info' };
  if (t === 'loop.done')
    return { role: 'engineer', label: 'Engineer', glyph: '🏁', text: `${l('the run finished', '这次运行结束')} ${trunc(S(ev, 'text'), 120)}`, tone: 'dim' };

  // ── inbox / reports (accent)
  if (t === 'life.inbox.queued')
    return { role: 'system', label: l('You', '你'), glyph: '📥', text: `${l('you added guidance', '你追加了指导')} · ${trunc(S(ev, 'text'), 160)}`, tone: 'accent' };
  if (t === 'final.report.ready' || t === 'pptx.report.ready')
    return { role: 'system', label: 'Argus', glyph: '📄', text: l('report ready', '报告已就绪'), tone: 'accent' };
  if (t === 'plan.completed')
    return { role: 'planner', label: 'Planner', glyph: '📋', text: l('plan completed', '计划已完成'), tone: 'accent' };
  if (t === 'daemon.stopping')
    return { role: 'system', label: 'Argus', glyph: '🛑', text: l('Argus is stopping', '正在停止'), tone: 'err' };

  // ── Guardian (监视守护) — Argus Panoptes keeping watch: the signals that fire
  // when a mission stalls, blocks, escalates, or a role backend fails. These are
  // the events that ACTUALLY persist to events.jsonl (the round.watchdog.* idle
  // "waits" are dropped as noise), so THIS is where the operator sees the guardian
  // at work. Anything flagged operator_alert is surfaced loud regardless of type.
  if (t === 'round.reviewer_backend_failure')
    return { role: 'system', label: l('Notice', '通知'), glyph: '!', text: l(`the Reviewer's model service is unreachable — waiting · ${trunc(S(ev, 'text'), 150)}`, `审阅者的模型服务连不上 — 先等待 · ${trunc(S(ev, 'text'), 150)}`), tone: 'err', rule: true };
  if (t === 'round.stall')
    return { role: 'system', label: l('Notice', '通知'), glyph: '!', text: trunc(S(ev, 'text') || l('no progress this round', '这一轮没有进展'), 170), tone: 'warn' };
  if (t === 'round.escalated')
    return { role: 'system', label: l('Notice', '通知'), glyph: '!', text: trunc(S(ev, 'text') || l('many rounds without a finish — raising what is blocking the work', '轮次已经很多还没做完 — 把外部阻碍提出来'), 170), tone: 'warn' };
  if (t === 'life.planner.stall_escalation')
    return { role: 'system', label: l('Notice', '通知'), glyph: '!', text: `${l('the Planner is stuck', '规划者卡住了')} — ${trunc(S(ev, 'reason') || S(ev, 'text'), 150)}`, tone: 'warn' };
  if (t === 'life.budget.pause')
    return { role: 'system', label: l('Watch', '监控'), glyph: '⏸', text: l(`budget cap reached — paused · ${trunc(S(ev, 'text') || S(ev, 'reason'), 140)}`, `已达到预算上限 — 已暂停 · ${trunc(S(ev, 'text') || S(ev, 'reason'), 140)}`), tone: 'warn' };
  if (t === 'budget.reservation.denied')
    return { role: 'system', label: l('Budget', '预算'), glyph: '$', text: `${l('not enough budget for this step', '这一步的预算不够')} — ${trunc(S(ev, 'reason') || S(ev, 'text'), 150)}`, tone: 'err', rule: true };
  if (t === 'budget.unpriced.blocked')
    return { role: 'system', label: l('Budget', '预算'), glyph: '$', text: `${l('held until the cost of this step is known', '这一步的成本还不清楚，先不做')} — ${trunc(S(ev, 'reason') || S(ev, 'text'), 150)}`, tone: 'err', rule: true };
  if (t === 'life.lifecycle.block') return null;
  if (t === 'life.daemon.idle_timeout')
    return { role: 'system', label: l('Watch', '监控'), glyph: '🟦', text: trunc(S(ev, 'text') || l('nothing to do for a while — standing by', '一段时间没有事做 — 待命中'), 150), tone: 'dim' };
  // round.watchdog.* only reach the feed in "full" verbosity — still render them.
  if (t === 'round.watchdog.restart_requested')
    return { role: 'system', label: l('Watch', '监控'), glyph: '🔄', text: l(`the round got stuck — starting it again · ${trunc(S(ev, 'reason'), 160)}`, `这一轮卡住了 — 重新开始这一轮 · ${trunc(S(ev, 'reason'), 160)}`), tone: 'warn' };
  if (t === 'engineer.failure_nudge')
    return { role: 'engineer', label: 'Engineer', glyph: '⚠', text: `${l('the same tool keeps failing', '同一个工具反复失败')} — ${trunc(S(ev, 'text') || S(ev, 'reason'), 160)}`, tone: 'warn' };
  if (t === 'mission.idle')
    return { role: 'system', label: 'Argus', glyph: '🟦', text: trunc(S(ev, 'text') || l('idle — waiting for the next task', '空闲 — 正在等待下一个任务'), 160), tone: 'dim' };
  // Catch-all: any event the daemon flagged for the operator's eyes, surfaced
  // even if its type has no bespoke renderer (harness marks it, cockpit shows it).
  if ((ev as Record<string, unknown>).operator_alert === true) {
    const body = trunc(S(ev, 'text') || S(ev, 'reason') || t, 170);
    if (body) return { role: 'system', label: l('Notice', '通知'), glyph: '!', text: body, tone: 'err', rule: true };
  }

  // Everything else (agent_io.*, internal bookkeeping) → hidden.
  return null;
}

/** Stable key for a stream event (dedup + React list key). */
export function eventKey(ev: EventMsg, i: number): string {
  void i;
  return sharedEventKey(ev);
}

/** The Reviewer's verdict in words a reader outside the project understands. */
function reviewVerdict(status: string, l: (english: string, chinese: string) => string): string {
  const verdicts: Record<string, [string, string]> = {
    done: ['the Reviewer was satisfied', '审阅者认可了这一轮'],
    blocked: ['the Reviewer found the work stuck', '审阅者认为工作卡住了'],
    no_progress: ['the Reviewer saw no progress', '审阅者没有看到进展'],
  };
  if (!status) return l('the Reviewer gave no verdict', '审阅者没有给出结论');
  const verdict = verdicts[status] ?? ['the Reviewer asked for another pass', '审阅者要求再改一轮'];
  return l(verdict[0], verdict[1]);
}
