import type { EventMsg } from '../types.js';
import type { TypedArgusEvent } from '../eventPayloads.generated.js';
import { canonicalEventType } from '../eventCatalog.js';
import { isStructuredAgentPayload, visibleAgentText } from '../events.js';
import { missionOutcomePresentation, settledStatus, stopClause } from '../missionOutcome.js';
import { managerIntentSummary } from '../managerIntent.js';

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

/**
 * Flag credential-shaped text without rewriting it. Argus shows text as it was
 * written: masked text cannot be verified by anyone reading it, and an operator
 * who wants masking turns on ARGUS_SKILL_REDACT_SECRETS, which masks upstream
 * before events reach any screen. `sensitive` still marks what looks like one.
 */
export function scanSecrets(text: string): { text: string; sensitive: boolean } {
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
  return { text, sensitive: sanitized !== text };
}

function visibleText(value: unknown): { text: string; sensitive: boolean } {
  return scanSecrets(visibleAgentText(value));
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

/** A Manager check that did not finish: what it means for the work, or null
 * when nothing needs saying (cancelled, superseded, or stopped by you). */
function supervisionFailure(event: TypedArgusEvent, context: RenderContext): { text: string; tone: RenderTone } | null {
  const code = stringField(event, 'error_code');
  const stage = stringField(event, 'failure_stage');
  const stopKind = stringField(event, 'stop_kind');
  const status = stringField(event, 'status');
  const quiet = ['daemon_shutdown', 'operator_pause', 'operator_abort'].includes(stopKind)
    || ['cancelled', 'superseded'].includes(code) || ['cancelled', 'superseded'].includes(status);
  if (code === 'cancelled' && stage === 'commit' && status === 'issued') {
    return { text: localized(context, 'An adjustment to the work was interrupted; it will be applied when work resumes.', '对工作的调整被打断了，恢复后会接着生效。'), tone: 'warn' };
  }
  if (quiet) return null;
  const why: Record<string, [string, string]> = {
    trial_quota_exceeded: ['the trial quota is running low', '试用额度不够了'],
    timeout: ['the model took too long to answer', '模型太久没有回应'],
  };
  const cause = (chinese: boolean) => (Object.hasOwn(why, code) ? why[code][chinese ? 1 : 0] : '')
    || stopClause(code, chinese) || stopClause(stopKind, chinese);
  const [en, zh] = cause(false) ? [` (${cause(false)})`, `（${cause(true)}）`] : ['', ''];
  const text = stage === 'commit' && ['continue', 'steer', 'stop'].includes(stringField(event, 'action'))
    ? localized(context, `The Manager decided on an adjustment, but it couldn't be applied${en}; the work carries on as it was.`, `经理想调整一下工作，但没能生效${zh}；工作按原样继续。`)
    : localized(context, `The Manager couldn't finish looking over the progress${en}; nothing was adjusted.`, `经理这次没能看完进展${zh}，没有做任何调整。`);
  return { text, tone: 'warn' };
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

/** Why the Manager could not take a request, in words; the raw error stays in the record. */
function managerFailure(event: TypedArgusEvent, context: RenderContext): string {
  const phase = stringField(event, 'phase');
  const why: Record<string, [string, string]> = {
    backend: ['the model service did not answer', '模型服务没有回应'],
    timeout: ['the model took too long to answer', '模型太久没有回应'],
  };
  const cause = Object.hasOwn(why, phase) ? why[phase] : ["the model's answer couldn't be understood", '没看懂模型的回答'];
  return localized(
    context,
    `couldn't work out how to handle this request (${cause[0]}), so nothing was started`,
    `没弄明白该怎么处理这个请求（${cause[1]}），所以还没开始做`,
  );
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
      ? model(role, labelKey, '', body.text, 'bright', { expandable: context.density === 'full', sensitive: body.sensitive })
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

/** A stage decision as the Manager's sentence; the deterministic bookkeeping
 * that closes a single-stage task is not news, and only the Manager's own
 * reasoning is worth showing beside it. */
function stageDecision(event: TypedArgusEvent, context: RenderContext): RenderModel {
  const action = stringField(event, 'action');
  const target = stageName(
    stringField(event, 'target_stage') || stringField(event, 'stage') || stringField(event, 'current_stage'),
    context.locale,
  );
  const own = stringField(event, 'source') === 'manager_llm';
  if (!own && (action === 'complete' || action === 'hold')) return hidden();
  const phrases: Record<string, [string, string]> = {
    advance: [`moved on to the ${target} stage`, `进入「${target}」阶段`],
    rollback: [`went back to the ${target} stage`, `退回「${target}」阶段`],
    hold: [`stays in the ${target} stage for now`, `先留在「${target}」阶段`],
    complete: [`the ${target} stage is done`, `「${target}」阶段完成`],
  };
  const phrase = phrases[action] ? localized(context, ...phrases[action]) : action;
  const reason = own ? clean(stringField(event, 'reason'), context.density === 'full' ? 140 : 120) : '';
  return model('manager', 'role.manager', '🧭', reason ? `${phrase} · ${reason}` : phrase, 'info');
}

export function renderEvent(event: TypedArgusEvent, context: RenderContext): RenderModel {
  // Older producers wrote `round.started`, `mission.completed`, `loop.completed`…;
  // the catalog names one canonical type for each, and that is the one rendered.
  const canonical = canonicalEventType(row(event).canonical_type ?? event.type);
  if (canonical && canonical !== event.type) {
    return renderEvent({ ...event, type: canonical } as TypedArgusEvent, context);
  }
  switch (event.type) {
    case 'advisor.consultation.requested':
    case 'life.manager.supervision.issued':
      return hidden();
    case 'advisor.consultation.completed': {
      const question = stringField(event, 'question').trim();
      const answer = stringField(event, 'summary').trim();
      if (!question && !answer) return hidden();
      return model('advisor', 'role.advisor', '◇', [question, answer].filter(Boolean).join('\n'), 'info', { expandable: true });
    }
    case 'advisor.consultation.cancelled': {
      const question = clean(stringField(event, 'question'), 160);
      return model('advisor', 'role.advisor', '◇', `${localized(context, 'Consultation cancelled', '已取消咨询')}${question ? ` · ${question}` : ''}`, 'dim');
    }
    case 'advisor.consultation.failed':
    case 'advisor.consultation.timed_out':
    case 'advisor.consultation.model_mismatch': {
      const reason = event.type.endsWith('model_mismatch')
        ? localized(context, 'The provider returned a different model', '接入返回的模型与所选模型不一致')
        : event.type.endsWith('timed_out') ? localized(context, 'The advisor did not respond in time', '顾问未在时限内返回')
          : localized(context, 'The consultation did not finish', '顾问咨询未完成');
      const question = clean(stringField(event, 'question'), 160);
      return model('advisor', 'role.advisor', '◇', `${reason}${question ? ` · ${question}` : ''}`, 'warn', { expandable: true });
    }
    case 'life.manager.supervision.applied':
      return model('manager', 'role.manager', '🧭', stringField(event, 'summary') || stringField(event, 'reason'), 'info', { expandable: true, rule: true });
    case 'life.peer.message.processed':
      return model('manager', 'role.peer', '↔', stringField(event, 'text'), 'info', { expandable: true });
    case 'life.observer.note': {
      const to = renderLabel(`role.${stringField(event, 'to')}`, context.locale);
      const text = stringField(event, 'text');
      return model('observer', 'role.observer', '👁', localized(context, `to ${to}: ${text}`, `对${to}：${text}`), 'warn', { expandable: true });
    }
    case 'life.manager.supervision.failed': {
      const failure = supervisionFailure(event, context);
      return failure ? model('manager', 'role.manager', '🧭', failure.text, failure.tone, { expandable: true }) : hidden();
    }
    case 'engineer.progress':
      return progress(event, context);
    case 'life.manager.intent.started':
      return model('manager', 'role.manager', '🧭', localized(context, 'Working out how to handle your request…', '正在安排处理方式…'), 'info');
    case 'life.manager.intent.completed':
      return model('manager', 'role.manager', '🧭', managerIntentSummary(row(event), context.locale), 'info', { expandable: true });
    case 'life.manager.intent.failed':
      return model('manager', 'role.manager', '⚠', managerFailure(event, context), 'err', { expandable: context.density === 'full' });
    case 'life.manager.stage_decision':
      return stageDecision(event, context);
    case 'life.letter.written': {
      const role = roleFor(event, 'manager');
      return model(role, `role.${role}`, '✉', localized(context, 'wrote you a letter', '给你写了一封信'), 'accent');
    }
    case 'life.planner.start':
      // The goal is the operator's own words; saying it back adds nothing.
      return model('planner', 'role.planner', '📋', localized(context, 'working out the next step', '正在规划下一步'), 'accent');
    case 'life.planner.verdict': {
      const done = stringField(event, 'status') === 'done' || row(event).project_done === true;
      const queued = Number(row(event).enqueued_tasks || 0);
      if (done) return model('planner', 'role.planner', '🏁', localized(context, 'the project is finished', '项目完成了'), 'ok');
      return queued > 0
        ? model('planner', 'role.planner', '📋', localized(context, `lined up ${count(queued, 'task')} to do next`, `排好了接下来要做的 ${queued} 项任务`), 'accent')
        : hidden();
    }
    case 'life.planner.task_added':
      return model('planner', 'role.planner', '＋', `${localized(context, 'added a task', '新增任务')} · ${clean(stringField(event, 'title') || stringField(event, 'objective'), context.density === 'full' ? 160 : 140)}`, 'accent');
    case 'life.planner.task_skipped':
      return model('planner', 'role.planner', '⏭', `${localized(context, 'skipped a task already planned', '跳过了已在计划中的任务')} ${clean(stringField(event, 'title'), context.density === 'full' ? 140 : 120)}`, 'dim');
    // Waiting is not news: a planning pass that starts nothing says so in the
    // record, and a question that only you can answer reaches you on its card.
    case 'life.planner.preview_skipped':
    case 'life.planner.normalized':
    case 'life.planner.idle':
    case 'life.planner.terminal_idle':
      return hidden();
    case 'life.planner.verification_probe':
      return context.showReasoning
        ? model('planner', 'role.planner', '⌕', `${localized(context, 'checking that the plan still holds', '检查计划是否仍然成立')} · ${clean(stringField(event, 'reason'), 180)}`, 'dim')
        : hidden();
    case 'life.planner.error': {
      // A failed planning pass is retried on its own; only one you need to
      // know about carries words for you.
      const said = stringField(event, 'text');
      return row(event).operator_alert === true && said ? model('planner', 'role.planner', '⚠', said, 'warn', { rule: true }) : hidden();
    }
    case 'life.mission.started':
      return model('engineer', 'role.engineer', '·', clean(stringField(event, 'title') || stringField(event, 'objective') || stringField(event, 'text') || localized(context, 'started work on this task', '开始做这项任务'), context.density === 'full' ? 180 : 160), 'info', { rule: true });
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
      const status = settledStatus(event);
      const residualRisk = status === 'done' ? clean(stringField(event, 'residual_risk'), 200) : '';
      if (residualRisk) {
        // Approved with one check left unverified on an accepted risk: never a plain pass.
        return model('reviewer', 'role.reviewer', '⚠', `${localized(context, 'verified with residual risk', '已验证，但有残余风险')}: ${residualRisk} · ${clean(stringField(event, 'reason'), context.density === 'full' ? 200 : 160)}`, 'warn');
      }
      const stuck = ['manager', 'blocked', 'no_progress'].includes(status);
      const tone: RenderTone = status === 'done' ? 'ok' : stuck ? 'err' : 'warn';
      const glyph = status === 'done' ? '✅' : stuck ? '⛔' : status === 'operator' ? '❓' : '↻';
      const verdict = roundVerdict(event, status, context);
      return model(verdict.role, `role.${verdict.role}`, glyph, `${verdict.text} · ${clean(stringField(event, 'reason'), context.density === 'full' ? 200 : 160)}`, tone);
    }
    case 'life.mission.completed': {
      const presentation = missionOutcomePresentation(row(event), context.locale);
      // An independent review told its conclusion on its own row and closes the task
      // under its name; without one, the summary is the only account of the result, told here once.
      const reviewed = row(event).success === true && stringField(event, 'review_source') === 'reviewer';
      const by = reviewed ? 'reviewer' : 'engineer';
      const summary = reviewed && presentation.tone === 'ok' ? '' : clean(stringField(event, 'summary'), 240);
      const risky = presentation.tone === 'ok' && Boolean(stringField(event, 'residual_risk'));
      const label = risky ? localized(context, 'completed with residual risk', '已完成，但有残余风险') : presentation.label;
      return model(by, `role.${by}`, risky ? '⚠' : presentation.glyph, summary ? `${label} · ${summary}` : label, risky ? 'warn' : presentation.tone, { rule: true });
    }
    case 'life.mission.failed':
      return model('engineer', 'role.engineer', '❌', `${localized(context, 'this task failed', '这项任务失败了')} ${clean(stringField(event, 'reason') || stringField(event, 'error'), context.density === 'full' ? 160 : 140)}`, 'err', { rule: true });
    case 'loop.start':
      // A team task already announced itself when it started.
      if (stringField(event, 'item_id')) return hidden();
      return model('engineer', 'role.engineer', '▶', clean(stringField(event, 'text') || stringField(event, 'objective'), context.density === 'full' ? 16_000 : 160), 'info', { expandable: context.density === 'full' });
    case 'loop.done': {
      // A team task's end is told by its completion row, under whoever closed it.
      if (stringField(event, 'item_id')) return hidden();
      const rounds = Number(row(event).rounds) || 0;
      return model('engineer', 'role.engineer', '🏁', rounds
        ? localized(context, `the run finished after ${count(rounds, 'round')}`, `这次运行结束，共 ${rounds} 轮`)
        : localized(context, 'the run finished', '这次运行结束'), 'dim');
    }
    case 'life.inbox.queued':
      // The Manager's notes to the team ride the same inbox; only your words are yours.
      if (stringField(event, 'source').startsWith('manager')) return hidden();
      return model('system', 'role.operator', '📥', `${localized(context, 'you added guidance', '你追加了指导')} · ${clean(stringField(event, 'text'), context.density === 'full' ? 180 : 160)}`, 'accent');
    case 'daemon.parked': {
      const replacedBy = stringField(event, 'replaced_by');
      const lead = localized(context, 'Argus set this project aside with its state saved', 'Argus 已把这个项目搁置，状态已保存');
      return model('system', 'role.system', 'Ⅱ', replacedBy ? `${lead} · ${localized(context, `continued as ${replacedBy}`, `由 ${replacedBy} 接续`)}` : lead, 'warn', { rule: true });
    }
    case 'accounting.journal_repaired':
      return hidden();
    case 'provider.request.denied': {
      const provider = stringField(event, 'provider');
      return model('system', 'event.quota', '⏸', `${localized(context, `a request to ${provider || 'the model service'} was held back`, `发往${provider || '模型服务'}的一次请求被暂缓`)} · ${clean(stringField(event, 'reason'), 160)}`, 'warn', { rule: true });
    }
    case 'round.reviewer_backend_failure': {
      const detail = clean(stringField(event, 'text'), 150);
      const lead = localized(context, "the Reviewer's model service is unreachable — waiting", '审阅者的模型服务连不上 — 先等待');
      return model('system', 'event.notice', context.density === 'full' ? '👁' : '!', `${lead} · ${detail}`, 'err', { rule: true });
    }
    case 'round.stall':
      return model('system', 'event.notice', context.density === 'full' ? '👁' : '!', clean(stringField(event, 'text') || localized(context, 'no progress this round', '这一轮没有进展'), 170), 'warn');
    case 'life.budget.pause':
      if (stringField(event, 'stop_kind') === 'cost_unreconciled')
        return model('system', 'event.budget', '$', localized(context, 'Provider usage awaits reconciliation', '调用费用待对账'), 'warn');
      return model('system', 'event.watch', '⏸', localized(context, `budget cap reached — paused · ${clean(stringField(event, 'text') || stringField(event, 'reason'), 140)}`, `已达到预算上限 — 已暂停 · ${clean(stringField(event, 'text') || stringField(event, 'reason'), 140)}`), 'warn');
    case 'budget.reservation.denied':
      return model('system', 'event.budget', '$', `${localized(context, 'budget denied', '预算申请被拒绝')} — ${clean(stringField(event, 'reason') || stringField(event, 'text'), context.density === 'full' ? 160 : 150)}`, 'err', { rule: true });
    case 'budget.unpriced.blocked':
      return model('system', 'event.budget', '$', localized(context, 'Provider usage awaits reconciliation', '调用费用待对账'), 'warn');
    case 'budget.unpriced.acknowledged':
      return model('system', 'event.budget', '$', localized(context, 'Single-call cost risk approved; original usage remains pending', '已批准单笔费用风险；原始费用仍待对账'), 'warn');
    case 'life.lifecycle.block':
      return model('system', 'event.watch', '⛔', `${localized(context, 'blocked — needs you', '卡住了 — 需要你来处理')} · ${clean(stringField(event, 'text') || stringField(event, 'reason'), 150)}`, 'err', { rule: true });
    case 'life.daemon.idle_timeout':
      return model('system', 'event.watch', '🟦', clean(stringField(event, 'text') || localized(context, 'nothing to do for a while — standing by', '一段时间没有事做 — 待命中'), 150), 'dim');
    case 'operator_alert':
      return model('system', 'event.notice', context.density === 'full' ? '👁' : '!', clean(stringField(event, 'text') || stringField(event, 'reason') || event.type, 170), 'err', { rule: true });

    case 'life.supervisor.error':
      return model('system', 'role.system', '⚠', localized(context, 'Argus hit an internal error and stopped the task it was on', 'Argus 内部出了错，手上的任务先停下了'), 'err', { rule: true });
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
      return model('planner', 'role.planner', '↪', localized(context, 'this task skips the independent review', '这项任务不安排独立审阅'), 'dim');
    case 'life.manager.feedback.exhausted':
      return hidden();
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
    // The chat tells you when completion attempts pause; the rest is the
    // team's own back-and-forth.
    case 'life.planner.completion_circuit_opened':
    case 'life.planner.completion_rejected':
    case 'life.planner.continuation_required':
    case 'life.planner.superseded':
    case 'round.orphan_process_group':
      return hidden();
    case 'life.daemon.degraded':
      // A dispatched objective that degrades is told by the alert beside it.
      if (row(event).objective_dispatched === true) return hidden();
      return model('system', 'role.system', '⚠', localized(context, "Argus started, but your goal didn't reach the Manager; send it again", 'Argus 启动了，但你的目标没有交到经理手上，请再发一次'), 'warn');
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
      return model('engineer', 'role.engineer', '↻', localized(context, 'reached the per-call turn allowance — continuing in a fresh session', '达到单次调用的轮次上限 — 在新会话中继续'), 'info');
    case 'round.provider_turn_cap.reviewer_restart':
      return model('reviewer', 'role.reviewer', '↻', localized(context, 'the review reached its turn allowance — restarting it in a fresh session', '审阅达到轮次上限 — 在新会话中重新开始'), 'info');
    case 'round.reviewer_backend_failure.backoff': {
      const seconds = Math.round(Number(row(event).seconds || 0));
      const said = clean(stringField(event, 'text'), 200);
      return model('reviewer', 'role.reviewer', '⏳', said || localized(context, `the Reviewer's model service is unavailable — retrying in ${seconds}s`, `审阅者的模型服务不可用 — ${seconds} 秒后重试`), 'warn');
    }
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
    case 'domain.promotion':
      return model('manager', 'role.manager', '★', clean(stringField(event, 'text'), 180), 'dim');
    case 'user.note': {
      const tags = Array.isArray(row(event).tags) ? (row(event).tags as unknown[]).map(String) : [];
      // A chat config change already answers in the conversation; its note is
      // grounding for later turns, not something the operator wrote.
      if (tags.includes('config')) return hidden();
      if (tags.includes('planner')) {
        return model('planner', 'role.planner', '📝', localized(context, 'updated the plan', '更新了计划'), 'dim');
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
      const working = row(event).live === true || (Array.isArray(row(event).steps) && (row(event).steps as unknown[]).length > 0);
      return body.trim() || working
        ? model('manager', 'role.argus', '', body, 'bright', { expandable: context.density === 'full', rule: true })
        : hidden();
    }

    case 'life.operator_status':
      return model('system', 'role.system', 'ℹ', stringField(event, 'text'), 'info');

    // Status lines are the developer log.
    case 'life.status':
    case 'agent.io.start': case 'agent.io.stream': case 'agent.io.complete': case 'agent.io.error':
    case 'provider.request.started': case 'provider.request.completed':
    case 'codex.util.completed': case 'skill.cost.completed':
    case 'budget.reservation.created': case 'budget.reservation.settled': case 'budget.reservation.released':
    case 'round.secret_redacted':
    case 'role.session.turn': case 'engineer.skill_maintenance.completed':
    case 'life.mission.skipped': case 'life.mission.orphaned': case 'life.mission.requeued':
    case 'life.manager.plan_challenge.decided': case 'life.vertical.resolved':
    case 'life.manager.backend_resolved': case 'life.planner.backend_resolved':
    case 'life.planner.dependency_dropped': case 'life.planner.parallel_dropped':
    case 'life.engineer.backend_resolved': case 'life.reviewer.backend_resolved':
    case 'life.runtime_failure.circuit_opened': case 'life.runtime_failure.circuit_blocked': case 'life.runtime_failure.canary_passed':
    case 'life.runtime.incident.detected': case 'life.runtime.incident.recovered': case 'life.runtime.incident.escalated':
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
    case 'knowledge.learned': case 'knowledge.recalled':
    // Bookkeeping a reader of the feed does not need: retries, receipts, and
    // state the sidebar or a later message already shows.
    case 'life.planner.verdict.discarded': case 'life.manager.feedback.unresolved':
    case 'life.manager.inbox': case 'life.plan.revision.rolled_back':
    case 'life.planner.completion_circuit.notify_failed': case 'life.planner.completion_circuit_holding':
    case 'life.daemon.ready': case 'plan.draft.start':
    case 'team.learning.review.skipped':
    case 'manager.live_view.updated': case 'manager.live_view.rejected':
    // A canvas write: open maps refetch the canvas; nothing to read in the feed.
    case 'project.canvas.updated':
    case 'manager.turn.started': case 'manager.turn.cancelled': case 'life.config.parse_failed':
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

/** The team members by the names the reader uses, the one copy every frontend
 *  shows; a critic reviews, so it is shown as the Reviewer. The last keys name
 *  who is speaking when it is not a role — Argus keeping watch, or the person
 *  at the keyboard. */
export const ROLE_NAMES: Record<string, [english: string, chinese: string]> = {
  'role.manager': ['Manager', '经理'],
  'role.planner': ['Planner', '规划者'],
  'role.engineer': ['Engineer', '工程师'],
  'role.reviewer': ['Reviewer', '审阅者'],
  'role.advisor': ['Advisor', '顾问'],
  'role.observer': ['Observer', '监督者'],
  'role.peer': ['Project exchange', '项目交流'],
  'role.critic': ['Reviewer', '审阅者'],
  'role.system': ['Argus', 'Argus'],
  'role.argus': ['Argus', 'Argus'],
  'role.operator': ['You', '你'],
};

/** Research stages by id or label. */
const STAGE_NAMES: Record<string, [english: string, chinese: string]> = {
  idea: ['Idea', '选题'],
  scope: ['Scope', '研究定义'],
  research: ['Literature and hypotheses', '文献与假设'],
  literature: ['Literature and hypotheses', '文献与假设'],
  implementation: ['Implementation', '方法实现'],
  implement: ['Implementation', '方法实现'],
  experiment: ['Experiments', '实验验证'],
  experiments: ['Experiments', '实验验证'],
  analysis: ['Analysis', '结果分析'],
  writing: ['Writing', '论文写作'],
  review: ['Final review', '最终审核'],
  delivery: ['Delivery', '成果交付'],
  optimize: ['Optimization', '优化'],
  solve: ['Derivation and verification', '推导与验证'],
  hold: ['Paused', '已暂停'],
  paused: ['Paused', '已暂停'],
};

/** A research stage as a word people use; unknown stages pass through. */
export function stageName(stage: string | null | undefined, locale: RenderLocale): string {
  const raw = String(stage ?? '').trim();
  const pair = STAGE_NAMES[raw.toLowerCase()];
  return pair ? (locale === 'zh-CN' ? pair[1] : pair[0]) : raw;
}

const LABELS: Record<string, [english: string, chinese: string]> = {
  ...ROLE_NAMES,
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
  /** Something in the text looks like a credential (it is shown as written). */
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
  if (!text && !(event.type === 'ui.argus' && (event.live === true || (Array.isArray(event.steps) && event.steps.length > 0)))) return null;
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

/** Who settled a round and where the work went, in words a reader outside the
 * project understands. An Engineer's handoff or self-check settles a round as
 * a review would, but no Reviewer took part (argus/engineer/round_routing.py):
 * `review_source` says whose it is, read as the map reads it
 * (argus/webapi/map_view.py), and the settled status where the work went. */
function roundVerdict(event: TypedArgusEvent, status: string, context: RenderContext): { role: 'engineer' | 'reviewer'; text: string } {
  const role = stringField(event, 'review_source').startsWith('engineer_') ? 'engineer' : 'reviewer';
  const verdicts: Record<typeof role, Record<string, [string, string]>> = {
    reviewer: {
      done: ['the Reviewer was satisfied', '审阅者认可了这一轮'],
      manager: ['the Reviewer found the work stuck', '审阅者认为工作卡住了'],
      // The host stopped the round, or an older release's Reviewer found it stuck.
      blocked: ['the Reviewer found the work stuck', '审阅者认为工作卡住了'],
      operator: ['the Reviewer leaves the choice to you', '审阅者把选择交给你'],
      planner: ['the Reviewer asked the Planner for a new plan', '审阅者请规划者重新安排'],
      no_progress: ['the Reviewer saw no progress', '审阅者没有看到进展'],
      '': ['the Reviewer gave no verdict', '审阅者没有给出结论'],
      other: ['the Reviewer asked for another pass', '审阅者要求再改一轮'],
    },
    // Keyed by the holder the Engineer names (argus/core/handoff.py).
    engineer: {
      done: ['the Engineer checked its own work and called it done', '工程师自查后认为做完了'],
      manager: ['the Engineer handed the work to the Manager to decide', '工程师把工作交给经理定夺'],
      // An older release wrote an Engineer's handoff to the Manager as `blocked`.
      blocked: ['the Engineer handed the work to the Manager to decide', '工程师把工作交给经理定夺'],
      operator: ['the Engineer leaves the choice to you', '工程师把选择交给你'],
      planner: ['the Engineer asked the Planner for a new plan', '工程师请规划者重新安排'],
      other: ['the Engineer goes on with another round', '工程师接着再做一轮'],
    },
  };
  const [english, chinese] = verdicts[role][status] ?? verdicts[role].other;
  return { role, text: localized(context, english, chinese) };
}
