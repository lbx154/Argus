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
  if (!phase || !cause) return `${l('routing failed', '分流失败')} ${trunc(raw, 140)}`;
  const phaseLabel: Record<string, string> = {
    backend: l('backend', '后端'),
    parse: l('parse', '解析'),
    contract: l('contract:', '契约：'),
    timeout: l('timeout', '超时'),
  };
  const attempts = Number((ev as Record<string, unknown>).attempts || 0);
  const attempt = attempts > 1
    ? l(` (attempt ${attempts})`, ` (第${attempts}次尝试)`)
    : '';
  const summary = `${l('routing failed', '分流失败')} · ${phaseLabel[phase] || phase} ${cause}${attempt}`;
  return raw ? `${summary} · ${l('raw', '原始错误')}: ${raw}` : summary;
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
    return body ? { role: 'manager', label: 'Argus', glyph: '◆', text: body, tone: 'bright', rule: true } : null;
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
    return { role: 'manager', label: 'Manager', glyph: '🧭', text: l('classifying request…', '判断任务归属…'), tone: 'info' };
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

  // ── Planner
  if (t === 'life.planner.start')
    return { role: 'planner', label: 'Planner', glyph: '📋', text: `${l('planning', '正在规划')} ${trunc(S(ev, 'objective'), 140)}`, tone: 'accent' };
  if (t === 'life.planner.verdict') {
    const done = S(ev, 'status') === 'done' || (ev as Record<string, unknown>).project_done === true;
    return done
      ? { role: 'planner', label: 'Planner', glyph: '🏁', text: l('project done', '项目已完成'), tone: 'ok' }
      : { role: 'planner', label: 'Planner', glyph: '📋', text: l(`queued ${S(ev, 'queued') || S(ev, 'n') || 'next'} task(s)`, `已加入 ${S(ev, 'queued') || S(ev, 'n') || '下一'} 个任务`), tone: 'accent' };
  }
  if (t === 'life.planner.task_added')
    return { role: 'planner', label: 'Planner', glyph: '＋', text: `${l('added', '已添加')} ${trunc(S(ev, 'title') || S(ev, 'objective'), 140)}`, tone: 'accent' };
  if (t === 'life.planner.task_skipped')
    return { role: 'planner', label: 'Planner', glyph: '⏭', text: `${l('skipped duplicate', '已跳过重复任务')} ${trunc(S(ev, 'title'), 120)}`, tone: 'dim' };
  if (t === 'life.planner.error')
    return { role: 'planner', label: 'Planner', glyph: '⚠', text: `${l('planner error', 'Planner 错误')} ${trunc(S(ev, 'error') || S(ev, 'text'), 140)}`, tone: 'err' };

  // ── Mission / round lifecycle
  if (t === 'life.mission.started' || t === 'mission.started')
    return { role: 'engineer', label: 'Engineer', glyph: '🚀', text: trunc(S(ev, 'title') || S(ev, 'objective') || S(ev, 'text') || l('mission started', '任务已开始'), 160), tone: 'info', rule: true };
  if (t === 'round.started' || t === 'round.start')
    return { role: 'engineer', label: 'Engineer', glyph: '──', text: l(`round ${roundNo(ev)}`, `第 ${roundNo(ev)} 轮`), tone: 'dim', rule: true };
  if (t === 'life.phase.started') {
    const phase = S(ev, 'label') || S(ev, 'phase');
    if (!phase) return null;
    const role = S(ev, 'agent_layer') || 'engineer';
    return { role, label: roleLabel(role), glyph: '🔄', text: l(`entering ${phase}`, `进入 ${phase}`), tone: 'info' };
  }
  if (t === 'round.review.started')
    return { role: 'reviewer', label: 'Reviewer', glyph: '🔄', text: l(`review round ${roundNo(ev)}`, `审核第 ${roundNo(ev)} 轮`), tone: 'info' };
  if (t === 'round.review.deferred')
    return { role: 'engineer', label: 'Engineer', glyph: '↪', text: l(`continues before review · ${trunc(S(ev, 'next_step'), 160)}`, `审核前继续执行 · ${trunc(S(ev, 'next_step'), 160)}`), tone: 'info' };
  if (t === 'round.main.completed')
    return { role: 'engineer', label: 'Engineer', glyph: '✅', text: l(`round ${roundNo(ev)} completed`, `第 ${roundNo(ev)} 轮已完成`), tone: 'info' };
  if (t === 'round.review.completed') {
    const st = S(ev, 'status');
    const tone: Tone = st === 'done' ? 'ok' : st === 'blocked' || st === 'no_progress' ? 'err' : 'warn';
    const glyph = st === 'done' ? '✅' : st === 'blocked' || st === 'no_progress' ? '⛔' : '↻';
    return { role: 'reviewer', label: 'Reviewer', glyph, text: `${st || '?'} · ${trunc(S(ev, 'reason'), 160)}`, tone };
  }
  if (t === 'life.iteration.critic')
    return { role: 'critic', label: 'Critic', glyph: '👔', text: `${S(ev, 'decision') || ''} ${trunc(S(ev, 'reason'), 140)}`, tone: 'info' };
  if (t === 'life.iteration.continued')
    return { role: 'critic', label: 'Critic', glyph: '🔁', text: l('queued next iteration', '已加入下一轮迭代'), tone: 'dim' };
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
    return { role: 'engineer', label: 'Engineer', glyph: '❌', text: `${l('mission failed', '任务失败')} ${trunc(S(ev, 'reason') || S(ev, 'error'), 140)}`, tone: 'err', rule: true };
  if (t === 'loop.start')
    return { role: 'engineer', label: 'Engineer', glyph: '▶', text: trunc(S(ev, 'text') || S(ev, 'objective'), 160), tone: 'info' };
  if (t === 'loop.done')
    return { role: 'engineer', label: 'Engineer', glyph: '🏁', text: `${l('loop done', '循环完成')} ${trunc(S(ev, 'text'), 120)}`, tone: 'dim' };

  // ── inbox / reports (accent)
  if (t === 'life.inbox.queued')
    return { role: 'system', label: l('You', '你'), glyph: '📥', text: `${l('nudge', '追加指导')} · ${trunc(S(ev, 'text'), 160)}`, tone: 'accent' };
  if (t === 'final.report.ready' || t === 'pptx.report.ready')
    return { role: 'system', label: 'Argus', glyph: '📄', text: l('report ready', '报告已就绪'), tone: 'accent' };
  if (t === 'plan.completed')
    return { role: 'planner', label: 'Planner', glyph: '📋', text: l('plan completed', '计划已完成'), tone: 'accent' };
  if (t === 'daemon.stopping')
    return { role: 'system', label: l('Daemon', '守护进程'), glyph: '🛑', text: l('stopping', '正在停止'), tone: 'err' };

  // ── Guardian (监视守护) — Argus Panoptes keeping watch: the signals that fire
  // when a mission stalls, blocks, escalates, or a role backend fails. These are
  // the events that ACTUALLY persist to events.jsonl (the round.watchdog.* idle
  // "waits" are dropped as noise), so THIS is where the operator sees the guardian
  // at work. Anything flagged operator_alert is surfaced loud regardless of type.
  if (t === 'round.reviewer_backend_failure')
    return { role: 'system', label: l('Notice', '通知'), glyph: '!', text: l(`reviewer backend down — holding · ${trunc(S(ev, 'text'), 150)}`, `Reviewer 后端不可用 — 已暂停 · ${trunc(S(ev, 'text'), 150)}`), tone: 'err', rule: true };
  if (t === 'round.stall')
    return { role: 'system', label: l('Notice', '通知'), glyph: '!', text: trunc(S(ev, 'text') || l('no forward progress', '没有取得进展'), 170), tone: 'warn' };
  if (t === 'round.escalated')
    return { role: 'system', label: l('Notice', '通知'), glyph: '!', text: trunc(S(ev, 'text') || l('soft round limit — escalating external blockers', '达到软轮次上限 — 正在升级外部阻塞'), 170), tone: 'warn' };
  if (t === 'life.planner.stall_escalation')
    return { role: 'system', label: l('Notice', '通知'), glyph: '!', text: `${l('planner stalled', 'Planner 停滞')} — ${trunc(S(ev, 'reason') || S(ev, 'text'), 150)}`, tone: 'warn' };
  if (t === 'life.budget.pause')
    return { role: 'system', label: l('Watch', '监控'), glyph: '⏸', text: l(`budget cap reached — paused · ${trunc(S(ev, 'text') || S(ev, 'reason'), 140)}`, `已达到预算上限 — 已暂停 · ${trunc(S(ev, 'text') || S(ev, 'reason'), 140)}`), tone: 'warn' };
  if (t === 'budget.reservation.denied')
    return { role: 'system', label: l('Budget', '预算'), glyph: '$', text: `${l('budget denied', '预算申请被拒绝')} — ${trunc(S(ev, 'reason') || S(ev, 'text'), 150)}`, tone: 'err', rule: true };
  if (t === 'budget.unpriced.blocked')
    return { role: 'system', label: l('Budget', '预算'), glyph: '$', text: `${l('budget blocked by unresolved cost', '预算因成本未确定而阻塞')} — ${trunc(S(ev, 'reason') || S(ev, 'text'), 150)}`, tone: 'err', rule: true };
  if (t === 'life.lifecycle.block') return null;
  if (t === 'life.daemon.idle_timeout')
    return { role: 'system', label: l('Watch', '监控'), glyph: '🟦', text: trunc(S(ev, 'text') || l('idle timeout — standing by', '空闲超时 — 正在待命'), 150), tone: 'dim' };
  // round.watchdog.* only reach the feed in "full" verbosity — still render them.
  if (t === 'round.watchdog.restart_requested')
    return { role: 'system', label: l('Watch', '监控'), glyph: '🔄', text: l(`stall caught — restarting the round · ${trunc(S(ev, 'reason'), 160)}`, `检测到停滞 — 正在重启本轮 · ${trunc(S(ev, 'reason'), 160)}`), tone: 'warn' };
  if (t === 'engineer.failure_nudge')
    return { role: 'engineer', label: 'Engineer', glyph: '⚠', text: `${l('repeated tool failure', '工具重复失败')} — ${trunc(S(ev, 'text') || S(ev, 'reason'), 160)}`, tone: 'warn' };
  if (t === 'mission.idle')
    return { role: 'system', label: 'Argus', glyph: '🟦', text: trunc(S(ev, 'text') || l('idle — awaiting the next mission', '空闲 — 正在等待下一个任务'), 160), tone: 'dim' };
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
