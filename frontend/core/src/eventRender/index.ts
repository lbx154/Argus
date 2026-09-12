import type { EventMsg } from '../types.js';
import type { TypedArgusEvent } from '../eventPayloads.generated.js';
import { canonicalEventType } from '../eventCatalog.js';
import { isStructuredAgentPayload, visibleAgentText } from '../events.js';
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
  /** A boundary in the feed — a task or round starts or ends, a person speaks,
   * or the work stops until someone acts — so a frontend can draw a divider
   * or list it among the milestones. */
  rule: boolean;
  /** The model's own reasoning summary: shown faint, hidden on request. */
  reasoning: boolean;
}

interface ModelOptions {
  expandable?: boolean;
  fallback?: boolean;
  sensitive?: boolean;
  visibility?: RenderModel['visibility'];
  rule?: boolean;
  reasoning?: boolean;
}

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
  return redactSecrets(visibleAgentText(value));
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

function count(value: number, noun: string): string {
  return `${value} ${noun}${value === 1 ? '' : 's'}`;
}

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
    rule: options.rule === true,
    reasoning: options.reasoning === true,
  };
}

function hidden(): RenderModel {
  return {
    visibility: 'hidden', role: 'system', labelKey: 'event.hidden', glyph: '', tone: 'dim',
    segments: [], expandable: false, sensitive: false, fallback: false, rule: false, reasoning: false,
  };
}

function fallback(event: TypedArgusEvent, context: RenderContext): RenderModel {
  if (row(event).operator_alert === true) {
    const text = clean(stringField(event, 'text') || stringField(event, 'reason') || event.type, 170);
    return model('system', 'event.notice', context.density === 'full' ? '👁' : '!', text, 'err', { rule: true });
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
  if (!phase || !cause) return `${localized(context, 'could not work out where this request belongs', '没能判断这个请求该归谁')} ${clean(raw, context.density === 'full' ? 160 : 140)}`;
  const labels: Record<string, [string, string]> = {
    backend: ['model service', '模型服务'], parse: ['reading the answer', '读取回答'], contract: ['answer shape:', '回答格式：'], timeout: ['timed out', '超时'],
  };
  const phaseLabel = labels[phase]
    ? localized(context, labels[phase][0], labels[phase][1])
    : phase;
  const attempts = Number(row(event).attempts || 0);
  const attempt = attempts > 1
    ? localized(context, ` (attempt ${attempts})`, ` (第${attempts}次尝试)`)
    : '';
  const summary = `${localized(context, 'could not work out where this request belongs', '没能判断这个请求该归谁')} · ${phaseLabel} ${cause}${attempt}`;
  return raw ? `${summary} · ${localized(context, 'error text', '原始错误')}: ${raw}` : summary;
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
    const body = clean(stringField(event, 'text'), 280);
    return body
      ? model(role, labelKey, '∴', body, 'dim', { expandable: context.density === 'full', reasoning: true })
      : hidden();
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
  // Older producers wrote `round.started`, `mission.completed`, `loop.completed`…;
  // the catalog names one canonical type for each, and that is the one rendered.
  const canonical = canonicalEventType(row(event).canonical_type ?? event.type);
  if (canonical && canonical !== event.type) {
    return renderEvent({ ...event, type: canonical } as TypedArgusEvent, context);
  }
  switch (event.type) {
    case 'engineer.progress':
      return progress(event, context);
    case 'life.manager.intent.started':
      return model('manager', 'role.manager', '🧭', localized(context, 'working out what kind of request this is…', '判断这是什么样的请求…'), 'info');
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
    case 'life.research.second_reading': {
      const role = roleFor(event, 'manager');
      const supported = clean(stringField(event, 'supported'), 160);
      const base = localized(context, 'reread the evidence and reworked the plan', '重读了证据并重排了计划');
      return model(role, `role.${role}`, '📖', supported ? `${base} · ${supported}` : base, 'info');
    }
    case 'life.letter.written': {
      const role = roleFor(event, 'manager');
      return model(role, `role.${role}`, '✉', localized(context, 'wrote you a letter', '给你写了一封信'), 'accent');
    }
    case 'life.planner.start':
      return model('planner', 'role.planner', '📋', `${localized(context, 'planning', '正在规划')} ${clean(stringField(event, 'objective'), context.density === 'full' ? 160 : 140)}`, 'accent');
    case 'life.planner.verdict': {
      const done = stringField(event, 'status') === 'done' || row(event).project_done === true;
      return done
        ? model('planner', 'role.planner', '🏁', localized(context, 'the project is finished', '项目已完成'), 'ok')
        : model('planner', 'role.planner', '📋', localized(context, `lined up ${stringField(event, 'queued') || stringField(event, 'n') || 'next'} task(s) to do next`, `已排好 ${stringField(event, 'queued') || stringField(event, 'n') || '下一'} 个接下来要做的任务`), 'accent');
    }
    case 'life.planner.task_added':
      return model('planner', 'role.planner', '＋', `${localized(context, 'added a task', '新增任务')} · ${clean(stringField(event, 'title') || stringField(event, 'objective'), context.density === 'full' ? 160 : 140)}`, 'accent');
    case 'life.planner.task_skipped': {
      const reviewDeferred = stringField(event, 'skip_category') === 'paper_review_purchase_deferred';
      const prefix = reviewDeferred
        ? localized(context, 'put off another paper review', '推迟了再买一次评审')
        : localized(context, 'skipped a task already planned', '跳过了已在计划中的任务');
      return model('planner', 'role.planner', '⏭', `${prefix} ${clean(stringField(event, 'title'), context.density === 'full' ? 140 : 120)}`, 'dim');
    }
    case 'life.planner.normalized':
      return model('planner', 'role.planner', '≋', `${localized(context, 'tidied the plan', '整理了计划')} · ${clean(stringField(event, 'diagnostic'), 180)}`, 'dim');
    case 'life.planner.waiting':
      return model('planner', 'role.planner', '⌛', `${localized(context, 'waiting', '等待中')} · ${clean(stringField(event, 'reason'), 180)}`, 'info');
    case 'life.planner.waiting_woken':
      return model('planner', 'role.planner', '↻', `${localized(context, 'resumed', '已唤醒')} · ${clean(stringField(event, 'wake_reason'), 180)}`, 'info');
    case 'life.planner.terminal_idle':
      return model('planner', 'role.planner', 'Ⅱ', `${localized(context, 'nothing left to plan', '暂时没有要规划的')} · ${clean(stringField(event, 'reason'), 180)}`, 'dim');
    case 'life.planner.verification_probe':
      return context.showReasoning
        ? model('planner', 'role.planner', '⌕', `${localized(context, 'checking that the plan still holds', '检查计划是否仍然成立')} · ${clean(stringField(event, 'reason'), 180)}`, 'dim')
        : hidden();
    case 'life.planner.error':
      return model('planner', 'role.planner', '⚠', `${localized(context, 'the Planner hit an error', '规划者出错')} ${clean(stringField(event, 'error') || stringField(event, 'text'), context.density === 'full' ? 160 : 140)}`, 'err');
    case 'life.mission.started':
      return model('engineer', 'role.engineer', '🚀', clean(stringField(event, 'title') || stringField(event, 'objective') || stringField(event, 'text') || localized(context, 'started work on this task', '开始做这项任务'), context.density === 'full' ? 180 : 160), 'info', { rule: true });
    case 'round.start':
      return model('engineer', 'role.engineer', '──', localized(context, `round ${roundNumber(event)}`, `第 ${roundNumber(event)} 轮`), 'dim', { rule: true });
    case 'life.phase.started': {
      const phase = stringField(event, 'label') || stringField(event, 'phase');
      const role = roleFor(event);
      return phase ? model(role, `role.${role}`, '🔄', localized(context, `entering ${phase}`, `进入 ${phase}`), 'info') : hidden();
    }
    case 'round.review.started':
      return model('reviewer', 'role.reviewer', '🔄', localized(context, `the Reviewer checks round ${roundNumber(event)}`, `审阅者检查第 ${roundNumber(event)} 轮`), 'info');
    case 'round.review.deferred':
      return model('engineer', 'role.engineer', '↪', localized(context, `carries on into the next round without a review · ${clean(stringField(event, 'next_step'), context.density === 'full' ? 180 : 160)}`, `不经审阅直接进入下一轮 · ${clean(stringField(event, 'next_step'), context.density === 'full' ? 180 : 160)}`), 'info');
    case 'round.main.completed':
      return model('engineer', 'role.engineer', '✅', localized(context, `finished round ${roundNumber(event)} of work`, `第 ${roundNumber(event)} 轮工作已完成`), 'info');
    case 'round.review.completed': {
      if (event.review_skipped === true) {
        return model('reviewer', 'role.reviewer', '↪', `${localized(context, 'no review this round', '这一轮没有审阅')} · ${clean(stringField(event, 'reason'), context.density === 'full' ? 200 : 160)}`, 'info');
      }
      const status = stringField(event, 'status');
      const tone: RenderTone = status === 'done' ? 'ok' : status === 'blocked' || status === 'no_progress' ? 'err' : 'warn';
      const glyph = status === 'done' ? '✅' : status === 'blocked' || status === 'no_progress' ? '⛔' : '↻';
      return model('reviewer', 'role.reviewer', glyph, `${reviewVerdict(status, context)} · ${clean(stringField(event, 'reason'), context.density === 'full' ? 200 : 160)}`, tone);
    }
    case 'life.mission.completed': {
      const presentation = missionOutcomePresentation(row(event));
      const summary = clean(stringField(event, 'summary'), 240);
      return model('engineer', 'role.engineer', presentation.glyph, summary ? `${presentation.label} · ${summary}` : presentation.label, presentation.tone, { rule: true });
    }
    case 'life.mission.failed':
      return model('engineer', 'role.engineer', '❌', `${localized(context, 'this task failed', '这项任务失败了')} ${clean(stringField(event, 'reason') || stringField(event, 'error'), context.density === 'full' ? 160 : 140)}`, 'err', { rule: true });
    case 'loop.start':
      return model('engineer', 'role.engineer', '▶', clean(stringField(event, 'text') || stringField(event, 'objective'), context.density === 'full' ? 16_000 : 160), 'info', { expandable: context.density === 'full' });
    case 'loop.done':
      return model('engineer', 'role.engineer', '🏁', `${localized(context, 'the run finished', '这次运行结束')} ${clean(stringField(event, 'text'), context.density === 'full' ? 140 : 120)}`, 'dim');
    case 'life.inbox.queued':
      return model('system', 'role.operator', '📥', `${localized(context, 'you added guidance', '你追加了指导')} · ${clean(stringField(event, 'text'), context.density === 'full' ? 180 : 160)}`, 'accent');
    case 'daemon.parked': {
      const replacedBy = stringField(event, 'replaced_by');
      const lead = localized(context, 'Argus set this project aside with its state saved', 'Argus 已把这个项目搁置，状态已保存');
      return model('system', 'role.system', 'Ⅱ', replacedBy ? `${lead} · ${localized(context, `continued as ${replacedBy}`, `由 ${replacedBy} 接续`)}` : lead, 'warn', { rule: true });
    }
    case 'provider.request.denied': {
      const provider = stringField(event, 'provider');
      return model('system', 'event.quota', '⏸', `${localized(context, `a request to ${provider || 'the model service'} was held back`, `发往${provider || '模型服务'}的一次请求被暂缓`)} · ${clean(stringField(event, 'reason'), 160)}`, 'warn', { rule: true });
    }
    case 'round.reviewer_backend_failure': {
      const detail = clean(stringField(event, 'text'), 150);
      const lead = context.density === 'full'
        ? localized(context, "the Reviewer's model service is unreachable — waiting rather than going on unchecked", '审阅者的模型服务连不上 — 先等待，不在无人审阅的情况下继续')
        : localized(context, "the Reviewer's model service is unreachable — waiting", '审阅者的模型服务连不上 — 先等待');
      return model('system', 'event.notice', context.density === 'full' ? '👁' : '!', `${lead} · ${detail}`, 'err', { rule: true });
    }
    case 'round.stall':
      return model('system', 'event.notice', context.density === 'full' ? '👁' : '!', clean(stringField(event, 'text') || localized(context, context.density === 'full' ? 'no progress this round — keeping a close eye on it' : 'no progress this round', '这一轮没有进展'), 170), 'warn');
    case 'round.escalated':
      return model('system', 'event.notice', context.density === 'full' ? '👁' : '!', clean(stringField(event, 'text') || localized(context, 'many rounds without a finish — raising what is blocking the work', '轮次已经很多还没做完 — 把外部阻碍提出来'), 170), 'warn');
    case 'life.planner.stall_escalation':
      return model('system', 'event.notice', context.density === 'full' ? '👁' : '!', `${localized(context, 'the Planner is stuck', '规划者卡住了')} — ${clean(stringField(event, 'reason') || stringField(event, 'text'), 150)}`, 'warn');
    case 'life.budget.pause':
      return model('system', 'event.watch', '⏸', localized(context, `budget cap reached — paused · ${clean(stringField(event, 'text') || stringField(event, 'reason'), 140)}`, `已达到预算上限 — 已暂停 · ${clean(stringField(event, 'text') || stringField(event, 'reason'), 140)}`), 'warn');
    case 'budget.reservation.denied':
      return model('system', 'event.budget', '$', `${localized(context, 'not enough budget for this step', '这一步的预算不够')} — ${clean(stringField(event, 'reason') || stringField(event, 'text'), context.density === 'full' ? 160 : 150)}`, 'err', { rule: true });
    case 'budget.unpriced.blocked':
      return model('system', 'event.budget', '$', `${localized(context, 'held until the cost of this step is known', '这一步的成本还不清楚，先不做')} — ${clean(stringField(event, 'reason') || stringField(event, 'text'), context.density === 'full' ? 160 : 150)}`, 'err', { rule: true });
    case 'life.lifecycle.block':
      return model('system', 'event.watch', '⛔', `${localized(context, 'blocked — needs you', '卡住了 — 需要你来处理')} · ${clean(stringField(event, 'text') || stringField(event, 'reason'), 150)}`, 'err', { rule: true });
    case 'life.daemon.idle_timeout':
      return model('system', 'event.watch', '🟦', clean(stringField(event, 'text') || localized(context, 'nothing to do for a while — standing by', '一段时间没有事做 — 待命中'), 150), 'dim');
    case 'operator_alert':
      return model('system', 'event.notice', context.density === 'full' ? '👁' : '!', clean(stringField(event, 'text') || stringField(event, 'reason') || event.type, 170), 'err', { rule: true });

    case 'life.supervisor.error':
      return model('system', 'role.system', '⚠', `${localized(context, 'Argus hit an internal error while coordinating the work', 'Argus 在统筹工作时遇到内部错误')} · ${clean(stringField(event, 'error'), 160)}`, 'err', { rule: true });
    case 'life.auth_failure':
      return model('system', 'role.system', '🔑', localized(context, 'the model service rejected the sign-in — refresh the credentials and Argus will carry on', '模型服务拒绝了登录 — 更新凭据后 Argus 会继续'), 'err', { rule: true });
    case 'life.planner.deferred':
      return model('planner', 'role.planner', '⌛', `${localized(context, 'planning is on hold', '规划暂缓')} · ${clean(stringField(event, 'reason'), 180)}`, 'dim');
    case 'life.post_mission.stop':
      return model('system', 'role.system', '■', `${localized(context, 'stopped after this task', '这项任务之后停下')} · ${clean(stringField(event, 'reason'), 180)}`, 'info');
    case 'life.planner.final_submission_skipped':
      return model('planner', 'role.planner', '⏭', `${localized(context, 'dropped an outdated final-submission task', '跳过了一项过时的最终提交任务')} · ${clean(stringField(event, 'title'), 140)}`, 'dim');
    case 'life.execution_host.blocked':
      return model('system', 'event.watch', '⛔', clean(stringField(event, 'reason') || localized(context, 'the program that runs code for Argus could not be started', '为 Argus 运行代码的程序无法启动'), context.density === 'full' ? 260 : 180), 'err', { rule: true });
    case 'life.mission.provider_configuration_disabled': {
      const streak = Number(row(event).streak || 0);
      const lead = localized(context, `the model configuration failed ${count(streak, 'time')} in a row — this task is paused until you decide`, `模型配置连续 ${streak} 次失败 — 这项任务已暂停，等待你的决定`);
      const question = clean(stringField(event, 'text'), 160);
      return model('system', 'event.watch', '⛔', question ? `${lead} · ${question}` : lead, 'err', { rule: true });
    }
    case 'life.iteration.continued':
      return model('critic', 'role.critic', '🔁', localized(context, 'lined up the next iteration', '排好了下一轮迭代'), 'dim');
    case 'life.learned_vertical.promoted':
      return model('manager', 'role.manager', '★', localized(context, `made the learned domain "${stringField(event, 'vertical')}" official`, `把新学到的领域「${stringField(event, 'vertical')}」正式收录`), 'accent');
    case 'life.learned_vertical.promotion_failed':
      return model('manager', 'role.manager', '⚠', `${localized(context, `could not record "${stringField(event, 'vertical')}" as a formal domain`, `未能把「${stringField(event, 'vertical')}」收录为正式领域`)} · ${clean(stringField(event, 'error'), 140)}`, 'warn');
    case 'life.review.waived':
      return model('planner', 'role.planner', '↪', `${localized(context, 'this task will skip the independent review', '这项任务将不做独立审阅')} · ${clean(stringField(event, 'reason'), 160)}`, 'dim');
    case 'life.manager.feedback.exhausted': {
      const attempts = Number(row(event).attempts || 0);
      return model('manager', 'role.manager', '⚠', `${localized(context, `the Manager's objection went unanswered ${count(attempts, 'time')} — planning pauses for a while`, `管理者的异议 ${attempts} 次未被回应 — 规划暂停一段时间`)} · ${clean(stringField(event, 'reason'), 140)}`, 'warn');
    }
    case 'life.manager.feedback.persisted': {
      const stage = stringField(event, 'stage');
      const lead = stage
        ? localized(context, `sent the plan back for another pass at ${stage}`, `把计划退回 ${stage} 阶段再改一轮`)
        : localized(context, 'sent the plan back for another pass', '把计划退回再改一轮');
      return model('manager', 'role.manager', '↩', `${lead} · ${clean(stringField(event, 'reason'), 160)}`, 'info');
    }
    case 'life.manager.goal_contract.failed':
      return model('manager', 'role.manager', '⚠', `${localized(context, 'could not save the agreed goal', '未能保存商定的目标')} · ${clean(stringField(event, 'error'), 140)}`, 'warn');
    case 'life.manager.intent.superseded':
      return model('manager', 'role.manager', '↷', localized(context, 'a newer instruction replaced this request', '更新的指令取代了这个请求'), 'info');
    case 'life.manager.project_report.failed':
      return model('manager', 'role.manager', '⚠', `${localized(context, 'could not deliver the project report', '未能送出项目报告')} · ${clean(stringField(event, 'error'), 140)}`, 'warn');
    case 'life.planner.completion_circuit_opened': {
      const rejections = Number(row(event).consecutive_rejections || 0);
      return model('planner', 'role.planner', '⏸', localized(context, `the same completion requirement turned the plan back ${count(rejections, 'time')} — pausing completion attempts until something changes`, `同一项完成要求已 ${rejections} 次打回计划 — 暂停完成尝试，直到情况变化`), 'warn');
    }
    case 'life.planner.completion_rejected':
      return model('manager', 'role.manager', '↩', `${localized(context, 'the Manager did not accept the completion claim', '管理者未接受完成的说法')} · ${clean(stringField(event, 'reason'), 160)}`, 'info');
    case 'life.planner.continuation_required':
      return model('planner', 'role.planner', '↻', localized(context, 'finished one increment; the standing objective continues', '完成了一个增量；长期目标继续推进'), 'dim');
    case 'life.planner.superseded':
      return model('planner', 'role.planner', '↷', localized(context, 'dropped an outdated planning pass after a newer instruction', '收到更新的指令，放弃了过时的规划'), 'dim');
    case 'life.planner.wait_overridden':
      return model('planner', 'role.planner', '＋', `${localized(context, 'scheduled independent work instead of waiting', '不再干等，安排了独立的工作')} · ${clean(stringField(event, 'task_title'), 140)}`, 'info');
    case 'life.daemon.degraded':
      return model('system', 'role.system', '⚠', `${localized(context, 'Argus started, but the objective did not reach the Manager', 'Argus 已启动，但目标没有交到管理者手上')} · ${clean(stringField(event, 'error'), 140)}`, 'warn');
    case 'round.orphan_process_group': {
      const cleaned = row(event).cleanup_succeeded === true;
      return model('engineer', 'role.engineer', '⚠', cleaned
        ? localized(context, 'background processes outlived the turn and were cleaned up', '有后台进程在这一轮结束后仍在运行，已清理')
        : localized(context, 'background processes outlived the turn and could not be cleaned up', '有后台进程在这一轮结束后仍在运行，未能清理'), 'warn');
    }
    case 'round.model_configuration_error':
      return model('engineer', 'role.engineer', '⛔', `${localized(context, 'the model configuration is not usable', '模型配置无法使用')} · ${clean(stringField(event, 'text') || stringField(event, 'error'), 160)}`, 'err', { rule: true });
    case 'round.watchdog.retry': {
      const attempt = Number(row(event).attempt || 0);
      const limit = Number(row(event).max_attempts || 0);
      return model('engineer', 'role.engineer', '↻', localized(context, `the model call failed — retrying in a fresh session (attempt ${attempt} of ${limit})`, `模型调用失败 — 在新会话中重试（第 ${attempt} 次，共 ${limit} 次）`), 'warn');
    }
    case 'round.watchdog.retry_exhausted': {
      const attempt = Number(row(event).attempt || 0);
      const detail = clean(stringField(event, 'fatal_error'), 140);
      const lead = localized(context, `the model call kept failing — giving up after ${count(attempt, 'attempt')}`, `模型调用持续失败 — ${attempt} 次后放弃`);
      return model('engineer', 'role.engineer', '⛔', detail ? `${lead} · ${detail}` : lead, 'err', { rule: true });
    }
    case 'round.backend_failure.backoff': {
      const seconds = Math.round(Number(row(event).seconds || 0));
      return model('engineer', 'role.engineer', '⏳', clean(stringField(event, 'text') || localized(context, `the model service failed — waiting ${seconds}s before trying again`, `模型服务出错 — 等待 ${seconds} 秒后再试`), 200), 'warn');
    }
    case 'round.backend_failure.hold_interrupted':
      return model('engineer', 'role.engineer', '↪', clean(stringField(event, 'text') || localized(context, 'the wait after a model-service failure ended early', '模型服务出错后的等待提前结束'), 180), 'info');
    case 'round.provider_turn_cap.restart':
      return model('engineer', 'role.engineer', '↻', localized(context, 'reached the per-call turn allowance — continuing the same task in a fresh session', '达到单次调用的轮次上限 — 在新会话中继续同一任务'), 'info');
    case 'round.provider_turn_cap.reviewer_restart':
      return model('reviewer', 'role.reviewer', '↻', localized(context, 'the review reached its turn allowance — restarting it in a fresh session', '审阅达到轮次上限 — 在新会话中重新开始'), 'info');
    case 'round.reviewer_backend_failure.backoff': {
      const seconds = Math.round(Number(row(event).seconds || 0));
      const said = clean(stringField(event, 'text'), 200);
      return model('reviewer', 'role.reviewer', '⏳', said || localized(context, `the Reviewer's model service is unavailable — retrying in ${seconds}s`, `审阅者的模型服务不可用 — ${seconds} 秒后重试`), 'warn');
    }
    case 'round.external_work_wait.started':
      return model('engineer', 'role.engineer', '⌛', `${localized(context, 'waiting for background work to report', '等待后台工作汇报')} · ${clean(stringField(event, 'work_id'), 80)}`, 'dim');
    case 'round.external_work_wait.completed':
      return model('engineer', 'role.engineer', '↻', localized(context, 'background work reported — resuming', '后台工作已汇报 — 继续'), 'dim');
    case 'plan.draft.failed':
      return model('planner', 'role.planner', '⚠', `${localized(context, 'could not draft the plan', '未能起草计划')} · ${clean(stringField(event, 'reason'), 140)}`, 'err');
    case 'plan.draft.done': {
      const steps = Number(row(event).steps || 0);
      return model('planner', 'role.planner', '📋', localized(context, `drafted a plan with ${count(steps, 'step')}`, `起草了一份 ${steps} 步的计划`), 'dim');
    }
    case 'team.learning.review.started':
      return model('manager', 'role.manager', '📚', localized(context, 'reviewing what this task taught the team', '整理这项任务给团队带来的经验'), 'dim');
    case 'team.learning.review.failed':
      return model('manager', 'role.manager', '⚠', `${localized(context, 'the team learning review failed', '团队经验整理失败')} · ${clean(stringField(event, 'error'), 140)}`, 'warn');
    case 'team.learning.review.completed': {
      const created = Number(row(event).created || 0);
      const updated = Number(row(event).updated || 0);
      return model('manager', 'role.manager', '📚', localized(context, `kept ${created} new and ${updated} updated team procedures`, `保留了 ${created} 条新的、${updated} 条更新的团队做法`), 'dim');
    }
    case 'team.learning.promotion.quarantined':
      return model('manager', 'role.manager', '⚠', localized(context, 'set aside a new procedure that would have judged its own work', '搁置了一条会评判自己工作的新做法'), 'warn');
    case 'self.learning.review.failed':
      return model('system', 'role.system', '⚠', `${localized(context, "the review of Argus's own conversation habits failed", '对 Argus 自身对话习惯的复盘失败')} · ${clean(stringField(event, 'error'), 140)}`, 'warn');
    case 'domain.promotion':
      return model('manager', 'role.manager', '★', clean(stringField(event, 'text'), 180), 'dim');
    case 'idea.portfolio.formed': {
      const selection = row(event).selection;
      const routeId = selection && typeof selection === 'object'
        ? String((selection as Record<string, unknown>).route_id ?? '')
        : '';
      const routes = Number(row(event).route_count || 0);
      const said = clean(stringField(event, 'text'), 180);
      const formed = routes > 0
        ? localized(context, `formed an idea portfolio of ${count(routes, 'route')}`, `组成了 ${routes} 条思路的组合`)
        : localized(context, 'formed an idea portfolio', '组成了思路组合');
      return model('engineer', 'role.engineer', '💡', routeId
        ? localized(context, `chose route ${routeId} from the idea portfolio`, `从思路组合中选定了路线 ${routeId}`)
        : said || formed, 'accent');
    }
    case 'user.note': {
      const tags = Array.isArray(row(event).tags) ? (row(event).tags as unknown[]).map(String) : [];
      if (tags.includes('planner')) {
        return model('planner', 'role.planner', '📝', localized(context, 'updated the research plan', '更新了研究计划'), 'dim');
      }
      const body = clean(stringField(event, 'summary') || stringField(event, 'text') || stringField(event, 'title'), 180);
      return model('system', 'role.operator', '📝', `${localized(context, 'you added a note', '你添加了一条笔记')}${body ? ` · ${body}` : ''}`, 'accent');
    }
    case 'ui.operator': {
      const body = stringField(event, 'text');
      return body.trim() ? model('system', 'role.operator', '›', body, 'accent', { rule: true }) : hidden();
    }
    case 'ui.argus': {
      const body = stringField(event, 'text');
      // A reply that is still being worked on has steps before it has words.
      const working = Array.isArray(row(event).steps) && (row(event).steps as unknown[]).length > 0;
      return body.trim() || working
        ? model('manager', 'role.argus', '▌', body, 'bright', { expandable: context.density === 'full', rule: true })
        : hidden();
    }

    case 'agent.io.start': case 'agent.io.stream': case 'agent.io.complete': case 'agent.io.error':
    case 'usage.recorded': case 'provider.request.started': case 'provider.request.completed':
    case 'codex.util.completed': case 'skill.cost.completed':
    case 'budget.reservation.created': case 'budget.reservation.settled': case 'budget.reservation.released':
    case 'round.checkpoint.recorded': case 'round.checkpoint.failed': case 'round.secret_redacted':
    case 'role.session.turn': case 'engineer.skill_maintenance.completed': case 'life.status':
    case 'life.mission.skipped': case 'life.mission.orphaned': case 'life.mission.requeued':
    case 'life.manager.plan_challenge.decided': case 'life.vertical.resolved':
    case 'life.manager.backend_resolved': case 'life.planner.backend_resolved':
    case 'life.planner.dependency_dropped': case 'life.planner.parallel_dropped':
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
    // Bookkeeping a reader of the feed does not need: retries, receipts, and
    // state the sidebar or a later message already shows.
    case 'life.planner.verdict.discarded': case 'life.manager.feedback.unresolved':
    case 'life.manager.project_report': case 'life.plan.revision.rolled_back':
    case 'life.planner.completion_circuit.notify_failed': case 'life.planner.completion_circuit_holding':
    case 'life.planner.external_poll_suppressed': case 'life.planner.waiting_contract.normalized':
    case 'life.daemon.ready': case 'plan.draft.start':
    case 'team.learning.review.skipped': case 'self.learning.review.started': case 'self.learning.review.completed':
    case 'manager.live_view.updated': case 'manager.live_view.rejected': case 'idea.portfolio.nested_skipped':
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

// Role names stay English in both languages: they are the names of the team
// members, not sentences. The other keys name who is speaking when it is not
// a role — the person at the keyboard, or Argus keeping watch.
const LABELS: Record<string, [english: string, chinese: string]> = {
  'role.manager': ['Manager', 'Manager'],
  'role.planner': ['Planner', 'Planner'],
  'role.engineer': ['Engineer', 'Engineer'],
  'role.reviewer': ['Reviewer', 'Reviewer'],
  'role.critic': ['Critic', 'Critic'],
  'role.system': ['Argus', 'Argus'],
  'role.argus': ['Argus', 'Argus'],
  'role.operator': ['You', '你'],
  'event.notice': ['Notice', '通知'],
  'event.watch': ['Watch', '监控'],
  'event.budget': ['Budget', '预算'],
  'event.quota': ['Quota', '额度'],
  'event.fallback': ['Argus', 'Argus'],
  'event.hidden': ['', ''],
};

/** The label a frontend prints for a model's `labelKey`, in the reader's language. */
export function renderLabel(labelKey: string, locale: RenderLocale): string {
  const pair = LABELS[labelKey];
  if (pair) return locale === 'zh-CN' ? pair[1] : pair[0];
  const role = labelKey.startsWith('role.') ? labelKey.slice('role.'.length) : labelKey;
  return role ? `${role.slice(0, 1).toUpperCase()}${role.slice(1)}` : '';
}

/** One line of a feed: the model flattened to what a row shows. */
export interface RenderedLine {
  role: string;
  /** The stable key behind `label`, for a frontend with a voice of its own. */
  labelKey: string;
  label: string;
  glyph: string;
  text: string;
  tone: RenderTone;
  /** A boundary in the feed — draw a divider, count it as a milestone. */
  rule: boolean;
  /** The model's reasoning summary — shown faint. */
  reasoning: boolean;
  /** Keep the complete wrapped body rather than truncating it to one line. */
  expand: boolean;
  /** Something that looked like a credential was redacted from the text. */
  sensitive: boolean;
}

/**
 * Render one event as a feed line, or null when the feed hides it. Every
 * frontend reads the daemon's raw events through this: the same event, the
 * same sentence, whichever screen shows it.
 */
export function renderLine(event: EventMsg, context: RenderContext): RenderedLine | null {
  const modelValue = renderEvent(event as TypedArgusEvent, context);
  if (modelValue.visibility === 'hidden') return null;
  const text = renderText(modelValue);
  if (!text && !(event.type === 'ui.argus' && Array.isArray(event.steps) && event.steps.length > 0)) return null;
  return {
    role: modelValue.role,
    labelKey: modelValue.labelKey,
    label: renderLabel(modelValue.labelKey, context.locale),
    glyph: modelValue.glyph,
    text,
    tone: modelValue.tone,
    rule: modelValue.rule,
    reasoning: modelValue.reasoning,
    expand: modelValue.expandable,
    sensitive: modelValue.sensitive,
  };
}

/** The Reviewer's verdict in words a reader outside the project understands. */
function reviewVerdict(status: string, context: RenderContext): string {
  const verdicts: Record<string, [string, string]> = {
    done: ['the Reviewer was satisfied', '审阅者认可了这一轮'],
    blocked: ['the Reviewer found the work stuck', '审阅者认为工作卡住了'],
    no_progress: ['the Reviewer saw no progress', '审阅者没有看到进展'],
  };
  if (!status) return localized(context, 'the Reviewer gave no verdict', '审阅者没有给出结论');
  const verdict = verdicts[status] ?? ['the Reviewer asked for another pass', '审阅者要求再改一轮'];
  return localized(context, verdict[0], verdict[1]);
}
