import type { EventMsg } from '../types.js';
import type { TypedArgusEvent } from '../eventPayloads.generated.js';
import { isStructuredAgentPayload, visibleAgentText } from '../events.js';
import { missionOutcomePresentation } from '../missionOutcome.js';

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

export function renderEvent(event: TypedArgusEvent, context: RenderContext): RenderModel {
  switch (event.type) {
    case 'engineer.progress':
      return progress(event, context);
    case 'life.planner.verdict': {
      const done = stringField(event, 'status') === 'done' || row(event).project_done === true;
      const queued = Number(row(event).enqueued_tasks || 0);
      if (done) return model('planner', 'role.planner', '🏁', localized(context, 'the project is finished', '项目完成了'), 'ok');
      return queued > 0
        ? model('planner', 'role.planner', '📋', localized(context, `lined up ${count(queued, 'task')} to do next`, `排好了接下来要做的 ${queued} 项任务`), 'accent')
        : hidden();
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
    case 'ui.operator': {
      const body = stringField(event, 'text');
      return body.trim() ? model('system', 'role.operator', '›', body, 'accent', { rule: true }) : hidden();
    }
    case 'ui.argus': {
      const body = stringField(event, 'text');
      // A reply can contain an answer, words on the way, its folded process or its current state.
      return body.trim() || row(event).status || [row(event).steps, row(event).process].some(words => Array.isArray(words) && words.length)
        ? model('manager', 'role.argus', '', body, 'bright', { expandable: context.density === 'full', rule: true })
        : hidden();
    }
    // Status lines are the developer log.
    case 'life.status':
    case 'provider.request.started': case 'provider.request.completed':
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
  if (!text && event.type !== 'ui.argus') return null;
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
