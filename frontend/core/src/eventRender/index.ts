import type { EventMsg } from '../types.js';
import type { TypedArgusEvent } from '../eventPayloads.generated.js';
import { isStructuredAgentPayload } from '../events.js';
import { missionOutcomePresentation } from '../missionOutcome.js';
import { formatMissionRouting } from '../missionView.js';

export type RenderLocale = 'en' | 'zh-CN';
export type RenderTone = 'bright' | 'dim' | 'accent' | 'ok' | 'warn' | 'err' | 'info';

export interface RenderContext {
  locale: RenderLocale;
  showReasoning: boolean;
  unknownEventPolicy: 'hide' | 'greppable';
  density: 'compact' | 'full';
}

export interface RenderSegment {
  kind: 'text';
  text: string;
}

export interface RenderModel {
  visibility: 'hidden' | 'normal' | 'alert';
  role: string;
  labelKey: string;
  glyph: string;
  tone: RenderTone;
  segments: RenderSegment[];
  expandable: boolean;
  sensitive: boolean;
  fallback: boolean;
}

interface ModelOptions {
  expandable?: boolean;
  fallback?: boolean;
  sensitive?: boolean;
  visibility?: RenderModel['visibility'];
}

const HANDOFF_LINE = /^(?:MILESTONE_STATUS|NEXT_OWNER|OPERATOR_QUESTION|OPERATOR_OPTIONS)\s*=/i;

function redactSecrets(text: string): { text: string; sensitive: boolean } {
  let sanitized = text;
  sanitized = sanitized.replace(
    /^(\s*(?:authorization|proxy-authorization)\s*:).*$/gim,
    '$1 <REDACTED:token>',
  );
  sanitized = sanitized.replace(
    /^(\s*(?:x-api-key|api-key|cookie|set-cookie)\s*:).*$/gim,
    '$1 <REDACTED:secret>',
  );
  sanitized = sanitized
    .replace(/gh[pousr]_[A-Za-z0-9]{20,}/g, '<REDACTED:github-token>')
    .replace(/xox[baprs]-[A-Za-z0-9-]{10,}/g, '<REDACTED:slack-token>')
    .replace(/AKIA[0-9A-Z]{16}/g, '<REDACTED:aws-key>')
    .replace(/\bbearer\s+[A-Za-z0-9._\-+/=]{16,}/gi, '<REDACTED:token>')
    .replace(
      /(?<![A-Za-z0-9])((?:x[_-]?)?api[_-]?key|client[_-]?secret|private[_-]?key)(['"]?)(\s*[=:])\s*['"]?([^\s'",;]{8,})['"]?/gi,
      '$1$2$3 <REDACTED:secret>',
    )
    .replace(
      /(?<![A-Za-z0-9])(secret|token|password|passwd|auth)(['"]?)(\s*[=:])\s*['"]?([^\s'",;]{8,})['"]?/gi,
      '$1$2$3 <REDACTED:secret>',
    )
    .replace(/\b([a-z][a-z0-9+.\-]*:\/\/)[^/\s:@]+:[^/\s@]+@/gi, '$1<REDACTED:creds>@');
  return { text: sanitized, sensitive: sanitized !== text };
}

function visibleText(value: unknown): { text: string; sensitive: boolean } {
  const stripped = String(value ?? '')
    .split(/\r?\n/)
    .filter((line) => !HANDOFF_LINE.test(line.trim()))
    .join('\n')
    .trim();
  return redactSecrets(stripped);
}

function clean(value: unknown, limit: number): string {
  const text = String(value ?? '')
    .replace(/```[a-z]*\n?/gi, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '[$1]')
    .trim();
  return text.length <= limit ? text : `${text.slice(0, limit - 1).trimEnd()}…`;
}

const row = (event: TypedArgusEvent): EventMsg => event;
const stringField = (event: TypedArgusEvent, key: string): string => String(row(event)[key] ?? '');

function localized(context: RenderContext, english: string, chinese: string): string {
  return context.locale === 'zh-CN' ? chinese : english;
}

function model(
  role: string,
  labelKey: string,
  glyph: string,
  text: string,
  tone: RenderTone,
  options: ModelOptions = {},
): RenderModel {
  const sanitized = visibleText(text);
  return {
    visibility: options.visibility ?? (tone === 'err' || tone === 'warn' ? 'alert' : 'normal'),
    role,
    labelKey,
    glyph,
    tone,
    segments: sanitized.text ? [{ kind: 'text', text: sanitized.text }] : [],
    expandable: options.expandable === true,
    sensitive: sanitized.sensitive || options.sensitive === true,
    fallback: options.fallback === true,
  };
}

function hidden(): RenderModel {
  return {
    visibility: 'hidden', role: 'system', labelKey: 'event.hidden', glyph: '', tone: 'dim',
    segments: [], expandable: false, sensitive: false, fallback: false,
  };
}

function fallback(event: TypedArgusEvent, context: RenderContext): RenderModel {
  if (row(event).operator_alert === true) {
    const text = clean(stringField(event, 'text') || stringField(event, 'reason') || event.type, 170);
    return model('system', 'event.notice', context.density === 'full' ? '👁' : '!', text, 'err');
  }
  if (context.unknownEventPolicy === 'hide') return hidden();
  const type = String(event.type || '?');
  const text = stringField(event, 'text').trim();
  return model('system', 'event.fallback', '•', `[${type}]${text ? ` ${text}` : ''}`, 'dim', { fallback: true });
}

function roleFor(event: TypedArgusEvent, defaultRole = 'engineer'): string {
  const explicit = stringField(event, 'agent_layer');
  if (explicit) return explicit;
  if (event.type.startsWith('life.manager.')) return 'manager';
  if (event.type.startsWith('life.planner.')) return 'planner';
  if (event.type.startsWith('round.review.')) return 'reviewer';
  return defaultRole;
}

function managerFailure(event: TypedArgusEvent, context: RenderContext): string {
  const phase = stringField(event, 'phase');
  const cause = stringField(event, 'cause') || stringField(event, 'backend_error');
  const raw = stringField(event, 'error');
  if (!phase || !cause) return `${localized(context, 'routing failed', '分流失败')} ${clean(raw, context.density === 'full' ? 160 : 140)}`;
  const labels: Record<string, [string, string]> = {
    backend: ['backend', '后端'], parse: ['parse', '解析'], contract: ['contract:', '契约：'], timeout: ['timeout', '超时'],
  };
  const phaseLabel = labels[phase]
    ? localized(context, labels[phase][0], labels[phase][1])
    : phase;
  const attempts = Number(row(event).attempts || 0);
  const attempt = attempts > 1
    ? localized(context, ` (attempt ${attempts})`, ` (第${attempts}次尝试)`)
    : '';
  const summary = `${localized(context, 'routing failed', '分流失败')} · ${phaseLabel} ${cause}${attempt}`;
  return raw ? `${summary} · ${localized(context, 'raw', '原始错误')}: ${raw}` : summary;
}

function roundNumber(event: TypedArgusEvent): string | number {
  const value = row(event).round_index ?? row(event).round;
  return typeof value === 'string' || typeof value === 'number' ? value : '?';
}

function progress(event: TypedArgusEvent, context: RenderContext): RenderModel {
  const kind = stringField(event, 'kind');
  const role = roleFor(event);
  const labelKey = `role.${role}`;
  if (kind === 'reasoning') {
    if (!context.showReasoning) return hidden();
    const body = clean(stringField(event, 'text'), context.density === 'full' ? 280 : 280);
    return body ? model(role, labelKey, '∴', body, 'dim', { expandable: context.density === 'full' }) : hidden();
  }
  if (kind === 'assistant_message' || kind === 'agent_message' || kind === 'message') {
    if (isStructuredAgentPayload(row(event))) return fallback(event, context);
    const body = visibleText(stringField(event, 'text'));
    return body.text
      ? model(role, labelKey, '▌', body.text, 'bright', { expandable: context.density === 'full', sensitive: body.sensitive })
      : hidden();
  }
  if (kind === 'command_execution') {
    const body = stringField(event, 'text') || stringField(event, 'command') || stringField(event, 'action_summary');
    const tone = stringField(event, 'status') === 'failed' ? 'err' : 'dim';
    return body ? model(role, labelKey, '▸ $', body, tone, { expandable: context.density === 'full' }) : hidden();
  }
  if (kind === 'tool_use' || kind === 'file_change') {
    const body = stringField(event, 'text') || stringField(event, 'action_summary');
    if (!body && context.density === 'full') return hidden();
    const placeholder = kind === 'file_change'
      ? localized(context, '(file change)', '（文件变更）')
      : localized(context, '(tool)', '（工具）');
    return model(role, labelKey, kind === 'file_change' ? '✎' : '⚙', body || placeholder, stringField(event, 'status') === 'failed' ? 'err' : 'dim', { expandable: context.density === 'full' });
  }
  if (context.density === 'full') return hidden();
  const body = String(row(event).text ?? row(event).action_summary ?? '').split('\n')[0]?.trim() ?? '';
  return body ? model(role, labelKey, '▸', clean(body, 160), 'dim') : hidden();
}

export function renderEvent(event: TypedArgusEvent, context: RenderContext): RenderModel {
  switch (event.type) {
    case 'engineer.progress':
      return progress(event, context);
    case 'life.manager.intent.started':
      return model('manager', 'role.manager', '🧭', localized(context, 'classifying request…', '判断任务归属…'), 'info');
    case 'life.manager.intent.completed': {
      const routing = formatMissionRouting({
        route: stringField(event, 'route') || 'team', vertical: stringField(event, 'vertical'),
        workflow_mode: stringField(event, 'workflow_mode'), lifetime: stringField(event, 'lifetime'),
        continuous: row(event).continuous === true, open_ended: row(event).open_ended === true,
      });
      return model('manager', 'role.manager', '🧭', `→ ${routing || stringField(event, 'kind') || localized(context, 'resolved', '已确定')}`, 'info');
    }
    case 'life.manager.intent.failed':
      return model('manager', 'role.manager', '⚠', managerFailure(event, context), 'err', { expandable: context.density === 'full' });
    case 'life.manager.stage_decision': {
      const target = stringField(event, 'target_stage') || stringField(event, 'stage') || stringField(event, 'current_stage');
      return model('manager', 'role.manager', '🧭', `${stringField(event, 'action')}${target ? ` → ${target}` : ''} ${clean(stringField(event, 'reason'), context.density === 'full' ? 140 : 120)}`, 'info');
    }
    case 'life.planner.start':
      return model('planner', 'role.planner', '📋', `${localized(context, 'planning', '正在规划')} ${clean(stringField(event, 'objective'), context.density === 'full' ? 160 : 140)}`, 'accent');
    case 'life.planner.verdict': {
      const done = stringField(event, 'status') === 'done' || row(event).project_done === true;
      return done
        ? model('planner', 'role.planner', '🏁', localized(context, 'project done', '项目已完成'), 'ok')
        : model('planner', 'role.planner', '📋', localized(context, `queued ${stringField(event, 'queued') || stringField(event, 'n') || 'next'} task(s)`, `已加入 ${stringField(event, 'queued') || stringField(event, 'n') || '下一'} 个任务`), 'accent');
    }
    case 'life.planner.task_added':
      return model('planner', 'role.planner', '＋', `${localized(context, 'added', '已添加')} ${clean(stringField(event, 'title') || stringField(event, 'objective'), context.density === 'full' ? 160 : 140)}`, 'accent');
    case 'life.planner.task_skipped': {
      const reviewDeferred = stringField(event, 'skip_category') === 'paper_review_purchase_deferred';
      const prefix = reviewDeferred
        ? localized(context, 'review purchase deferred', '已延后重复评审')
        : localized(context, 'skipped duplicate', '已跳过重复任务');
      return model('planner', 'role.planner', '⏭', `${prefix} ${clean(stringField(event, 'title'), context.density === 'full' ? 140 : 120)}`, 'dim');
    }
    case 'life.planner.normalized':
      return model('planner', 'role.planner', '≋', `${localized(context, 'normalized', '已规范化')} · ${clean(stringField(event, 'diagnostic'), 180)}`, 'dim');
    case 'life.planner.waiting':
      return model('planner', 'role.planner', '⌛', `${localized(context, 'waiting', '等待中')} · ${clean(stringField(event, 'reason'), 180)}`, 'info');
    case 'life.planner.waiting_woken':
      return model('planner', 'role.planner', '↻', `${localized(context, 'resumed', '已唤醒')} · ${clean(stringField(event, 'wake_reason'), 180)}`, 'info');
    case 'life.planner.terminal_idle':
      return model('planner', 'role.planner', 'Ⅱ', `${localized(context, 'idle', '空闲')} · ${clean(stringField(event, 'reason'), 180)}`, 'dim');
    case 'life.planner.verification_probe':
      return context.showReasoning
        ? model('planner', 'role.planner', '⌕', `${localized(context, 'verification probe', '验证探测')} · ${clean(stringField(event, 'reason'), 180)}`, 'dim')
        : hidden();
    case 'life.planner.error':
      return model('planner', 'role.planner', '⚠', `${localized(context, 'planner error', 'Planner 错误')} ${clean(stringField(event, 'error') || stringField(event, 'text'), context.density === 'full' ? 160 : 140)}`, 'err');
    case 'life.mission.started':
      return model('engineer', 'role.engineer', '🚀', clean(stringField(event, 'title') || stringField(event, 'objective') || stringField(event, 'text') || localized(context, 'mission started', '任务已开始'), context.density === 'full' ? 180 : 160), 'info');
    case 'round.start':
      return model('engineer', 'role.engineer', '──', localized(context, `round ${roundNumber(event)}`, `第 ${roundNumber(event)} 轮`), 'dim');
    case 'life.phase.started': {
      const phase = stringField(event, 'label') || stringField(event, 'phase');
      const role = roleFor(event);
      return phase ? model(role, `role.${role}`, '🔄', localized(context, `entering ${phase}`, `进入 ${phase}`), 'info') : hidden();
    }
    case 'round.review.started':
      return model('reviewer', 'role.reviewer', '🔄', localized(context, `review round ${roundNumber(event)}`, `审核第 ${roundNumber(event)} 轮`), 'info');
    case 'round.review.deferred':
      return model('engineer', 'role.engineer', '↪', localized(context, `continues before review · ${clean(stringField(event, 'next_step'), context.density === 'full' ? 180 : 160)}`, `审核前继续执行 · ${clean(stringField(event, 'next_step'), context.density === 'full' ? 180 : 160)}`), 'info');
    case 'round.main.completed':
      return model('engineer', 'role.engineer', '✅', localized(context, `round ${roundNumber(event)} completed`, `第 ${roundNumber(event)} 轮已完成`), 'info');
    case 'round.review.completed': {
      const status = stringField(event, 'status');
      const tone: RenderTone = status === 'done' ? 'ok' : status === 'blocked' || status === 'no_progress' ? 'err' : 'warn';
      const glyph = status === 'done' ? '✅' : status === 'blocked' || status === 'no_progress' ? '⛔' : '↻';
      return model('reviewer', 'role.reviewer', glyph, `${status || '?'} · ${clean(stringField(event, 'reason'), context.density === 'full' ? 200 : 160)}`, tone);
    }
    case 'life.mission.completed': {
      const presentation = missionOutcomePresentation(row(event));
      const summary = clean(stringField(event, 'summary'), 240);
      return model('engineer', 'role.engineer', presentation.glyph, summary ? `${presentation.label} · ${summary}` : presentation.label, presentation.tone);
    }
    case 'life.mission.failed':
      return model('engineer', 'role.engineer', '❌', `${localized(context, 'mission failed', '任务失败')} ${clean(stringField(event, 'reason') || stringField(event, 'error'), context.density === 'full' ? 160 : 140)}`, 'err');
    case 'loop.start':
      return model('engineer', 'role.engineer', '▶', clean(stringField(event, 'text') || stringField(event, 'objective'), context.density === 'full' ? 16_000 : 160), 'info', { expandable: context.density === 'full' });
    case 'loop.done':
      return model('engineer', 'role.engineer', '🏁', `${localized(context, 'loop done', '循环完成')} ${clean(stringField(event, 'text'), context.density === 'full' ? 140 : 120)}`, 'dim');
    case 'life.inbox.queued':
      return model('system', 'role.operator', '📥', `${localized(context, 'nudge', '追加指导')} · ${clean(stringField(event, 'text'), context.density === 'full' ? 180 : 160)}`, 'accent');
    case 'daemon.parked':
      return context.density === 'compact' ? hidden() : model('system', 'role.system', 'Ⅱ', `session parked · state saved${stringField(event, 'replaced_by') ? ` · replaced by ${stringField(event, 'replaced_by')}` : ''}`, 'warn');
    case 'provider.request.denied':
      return context.density === 'compact' ? hidden() : model('system', 'event.quota', '⏸', `${stringField(event, 'provider') || 'provider'} request blocked · ${clean(stringField(event, 'reason'), 160)}`, 'warn');
    case 'round.reviewer_backend_failure':
      return model('system', 'event.notice', context.density === 'full' ? '👁' : '!', context.density === 'full' ? `reviewer backend down — holding, won't continue blind · ${clean(stringField(event, 'text'), 150)}` : `reviewer backend down — holding · ${clean(stringField(event, 'text'), 150)}`, 'err');
    case 'round.stall':
      return model('system', 'event.notice', context.density === 'full' ? '👁' : '!', clean(stringField(event, 'text') || localized(context, context.density === 'full' ? 'no forward progress — watching closely' : 'no forward progress', '没有取得进展'), 170), 'warn');
    case 'round.escalated':
      return model('system', 'event.notice', context.density === 'full' ? '👁' : '!', clean(stringField(event, 'text') || localized(context, 'soft round limit — escalating external blockers', '达到软轮次上限 — 正在升级外部阻塞'), 170), 'warn');
    case 'life.planner.stall_escalation':
      return model('system', 'event.notice', context.density === 'full' ? '👁' : '!', `${localized(context, 'planner stalled', 'Planner 停滞')} — ${clean(stringField(event, 'reason') || stringField(event, 'text'), 150)}`, 'warn');
    case 'life.budget.pause':
      return model('system', 'event.watch', '⏸', localized(context, `budget cap reached — paused · ${clean(stringField(event, 'text') || stringField(event, 'reason'), 140)}`, `已达到预算上限 — 已暂停 · ${clean(stringField(event, 'text') || stringField(event, 'reason'), 140)}`), 'warn');
    case 'budget.reservation.denied':
      return model('system', 'event.budget', '$', `${localized(context, 'budget denied', '预算申请被拒绝')} — ${clean(stringField(event, 'reason') || stringField(event, 'text'), context.density === 'full' ? 160 : 150)}`, 'err');
    case 'budget.unpriced.blocked':
      return model('system', 'event.budget', '$', `${localized(context, 'budget blocked by unresolved cost', '预算因成本未确定而阻塞')} — ${clean(stringField(event, 'reason') || stringField(event, 'text'), context.density === 'full' ? 160 : 150)}`, 'err');
    case 'life.lifecycle.block':
      return context.density === 'compact' ? hidden() : model('system', 'event.watch', '⛔', `blocked — needs you · ${clean(stringField(event, 'text') || stringField(event, 'reason'), 150)}`, 'err');
    case 'life.daemon.idle_timeout':
      return model('system', 'event.watch', '🟦', clean(stringField(event, 'text') || localized(context, 'idle timeout — standing by', '空闲超时 — 正在待命'), 150), 'dim');
    case 'operator_alert':
      return model('system', 'event.notice', context.density === 'full' ? '👁' : '!', clean(stringField(event, 'text') || stringField(event, 'reason') || event.type, 170), 'err');

    case 'agent.io.start': case 'agent.io.stream': case 'agent.io.complete': case 'agent.io.error':
    case 'usage.recorded': case 'provider.request.started': case 'provider.request.completed':
    case 'codex.util.completed': case 'skill.cost.completed':
    case 'budget.reservation.created': case 'budget.reservation.settled': case 'budget.reservation.released':
    case 'round.checkpoint.recorded': case 'round.checkpoint.failed': case 'round.secret_redacted':
    case 'role.session.turn': case 'engineer.skill_maintenance.completed': case 'life.status':
    case 'life.mission.skipped': case 'life.mission.orphaned': case 'life.mission.requeued':
    case 'life.manager.plan_challenge.decided': case 'life.vertical.resolved':
    case 'life.manager.backend_resolved': case 'life.planner.backend_resolved':
    case 'life.engineer.backend_resolved': case 'life.reviewer.backend_resolved': case 'life.curator.backend_resolved':
    case 'life.runtime_failure.circuit_opened': case 'life.runtime_failure.circuit_blocked': case 'life.runtime_failure.canary_passed':
    case 'life.plan.revision.proposed': case 'life.plan.revision.rejected': case 'life.plan.revision.committed': case 'life.plan.node.superseded':
    case 'life.lifecycle.transition': case 'life.inbox.drained':
    case 'life.operator_question.pending': case 'life.operator_question.answered':
    case 'project.completed': case 'project.completion_refused':
    case 'daemon.command.submitted': case 'daemon.command.completed': case 'daemon.command.rejected':
    case 'idea.search.started': case 'idea.search.completed': case 'idea.search.skipped':
    case 'venue.research.started': case 'venue.research.completed': case 'research.achievement.certified':
    case 'skill.library.available': case 'skill.created': case 'skill.updated': case 'skill.archived':
    case 'skill.tidied': case 'skill.history.compressed': case 'skill.evolution.completed':
    case 'wiki.initialized': case 'wiki.hook.warning': case 'wiki.created': case 'wiki.updated':
    case 'wiki.retired': case 'wiki.promotion.promoted': case 'wiki.promotion.demoted':
    case 'wiki.retired.compressed': case 'wiki.evolution.completed':
      return fallback(event, context);
    default: {
      const exhaustive: never = event;
      return fallback(exhaustive, context);
    }
  }
}

export function renderText(modelValue: RenderModel): string {
  return modelValue.segments.map((segment) => segment.text).join('');
}
