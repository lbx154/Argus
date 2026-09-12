// Plain-language rendering of the machine phrases the Argus backend still
// writes into mission views: role labels, timeline titles, review reasons,
// route statuses and bookkeeping event names. Every entry is looked up by a
// stable code first (the backend is gaining `kind` codes) and only then by the
// legacy English phrase, so the phrase table can shrink as codes arrive
// without any caller changing. Text that is not recognised passes through
// unchanged, and the technical remainder of a recognised sentence is returned
// separately so it can live in a tooltip instead of the page.

import type { Locale } from '../i18n';

type Pair = readonly [zh: string, en: string];

function pick(pair: Pair, locale: Locale): string {
  return locale === 'zh-CN' ? pair[0] : pair[1];
}

function fill(template: string, variables: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (_match, name: string) => variables[name] ?? '');
}

/**
 * Sentences keyed by the backend's stable kinds (mission_view schema 7), plus
 * a few frontend-only codes for bookkeeping events and the older labels the
 * TS mirror reducer still writes for live events. Kinds whose sentence needs a
 * number are filled by the phrase rules below.
 */
export const PLAIN_BY_KIND: Record<string, Pair> = {
  grounding_started: ['正在阅读这项请求，弄清楚它要做什么', 'Reading the request to understand what it asks'],
  goal_framed: ['已经理解这项请求，项目目标已经确定。', 'The request is understood and the project goal is set.'],
  goal_not_understood: ['这项请求还没有被理解清楚。', 'The request could not be understood yet.'],
  request_not_routed: ['这项请求没能被安排下去。', 'The request could not be routed to the team.'],
  stage_advanced: ['研究进入了下一阶段。', 'The research moved to the next stage.'],
  stage_entered: ['研究进入「{stage}」阶段。', 'The research moved to the {stage} stage.'],
  stage_held: ['当前阶段暂不推进。', 'The current stage is on hold.'],
  stage_rolled_back: ['研究退回到了前一阶段。', 'The research went back a stage.'],
  stage_completed: ['当前阶段已完成。', 'The current stage is complete.'],
  stage_reconsidered: ['阶段安排已重新考虑。', 'The stage plan was reconsidered.'],
  stage_reviewed: ['阶段已复核。', 'The stage was reviewed.'],
  planning_started: ['正在规划下一步工作', 'Planning the next step'],
  task_added: ['计划中加入了一项新任务。', 'A new task was added to the plan.'],
  research_route_added: ['计划中加入了一条新的研究路线。', 'A new research route was added to the plan.'],
  planning_complete: ['下一步的计划已经排好。', 'The next step is planned.'],
  project_finished: ['项目已经完成。', 'The project is finished.'],
  task_delivered: ['成果已交付。', 'The result was delivered.'],
  planner_waiting: ['规划者在等待前一步完成。', 'The Planner is waiting for the previous step.'],
  planner_idle: ['暂时没有需要规划的工作。', 'Nothing to plan right now.'],
  planning_failed: ['这次规划没有成功。', 'Planning did not succeed this time.'],
  mission_started: ['开始处理这项任务', 'Work on the task started'],
  awaiting_engineer: ['等待工程师完成这一轮。', 'Waiting for the Engineer to finish the round.'],
  waiting_external: ['等待外部工作完成。', 'Waiting on work outside Argus.'],
  mission_completed: ['任务已完成。', 'The task is complete.'],
  mission_continued: ['任务将继续进行。', 'The task continues.'],
  mission_certified: ['成果已通过独立核对。', 'The result passed an independent check.'],
  mission_incomplete: ['任务没有完成。', 'The task was not completed.'],
  mission_stalled: ['任务停滞不前。', 'The task stalled.'],
  mission_blocked: ['任务被挡住了。', 'The task is blocked.'],
  mission_failed: ['任务没能完成。', 'The task could not be completed.'],
  mission_paused: ['任务在完成前暂停；进度已保存，可以继续。', 'The task was paused before it finished; its progress is saved and it can be resumed.'],
  mission_ended: ['任务已结束。', 'The task ended.'],
  mission_ready: ['可以开始新任务。', 'Ready for a new task.'],
  round_in_progress: ['这一轮正在进行。', 'The round is in progress.'],
  round_running: ['第 {n} 轮正在进行。', 'Round {n} is in progress.'],
  round_started: ['第 {n} 轮工作开始。', 'Round {n} started.'],
  progress_tool: ['正在使用工具', 'Using a tool'],
  progress_command: ['正在运行命令', 'Running a command'],
  progress_message: ['正在汇报进展', 'Reporting progress'],
  progress_file: ['正在更新项目文件', 'Updating project files'],
  progress_inspecting: ['正在查看项目状态', 'Looking at the project state'],
  engineer_round_finished: ['工程师完成了这一轮，结果已准备好供核对。', 'The Engineer finished the round; the result is ready to be checked.'],
  checking_started: ['正在阅读工程师的结果并核对证据', "Reading the Engineer's result and checking the evidence"],
  continuing_before_check: ['先继续下一轮，核对稍后进行。', 'Continuing to the next round; the check comes later.'],
  check_postponed: ['这一轮的核对推迟了。', 'The check of this round was postponed.'],
  round_not_judged: ['这一轮没有人审阅。', 'No one reviewed this round.'],
  results_accepted: ['结果通过了核对。', 'The result passed the check.'],
  another_attempt_requested: ['审阅者要求再试一次。', 'The reviewer asked for another attempt.'],
  attempt_blocked: ['这次尝试被挡住了。', 'This attempt is blocked.'],
  replan_requested: ['审阅者要求重新规划。', 'The reviewer asked for a new plan.'],
  results_not_final: ['结果还不是最终的。', 'The result is not final yet.'],
  handed_off: ['已交给下一位。', 'Handed off to the next role.'],
  waiting: ['等待中', 'Waiting'],
  live_activity: ['正在工作', 'Working'],
  role_failed: ['{role} 执行失败', '{role} failed'],
  capability_unlocked: ['解锁了一项新能力。', 'A new capability was unlocked.'],
  capability_upgraded: ['一项能力得到了升级。', 'A capability was upgraded.'],
  capability_promoted: ['一项能力被提升为来源。', 'A capability was promoted to a source.'],
  knowledge_captured: ['记下了一条新知识。', 'New knowledge was captured.'],
  knowledge_refined: ['一条知识得到了完善。', 'A piece of knowledge was refined.'],
  knowledge_promoted: ['一条知识被提升。', 'A piece of knowledge was promoted.'],
  knowledge_demoted: ['一条知识被降级。', 'A piece of knowledge was demoted.'],
  knowledge_retired: ['一条知识已退役。', 'A piece of knowledge was retired.'],
  'event.model_call.started': ['开始调用模型', 'Model call started'],
  'event.model_call.completed': ['模型调用完成', 'Model call completed'],
  'event.model_call.failed': ['模型调用失败', 'Model call failed'],
  'event.usage.recorded': ['已记录用量', 'Usage recorded'],
  'event.budget.reserved': ['已预留预算', 'Budget reserved'],
  'event.budget.settled': ['预算已结算', 'Budget settled'],
  'event.model_turn': ['与模型完成一次往返', 'One exchange with the model'],
  'event.instruction.received': ['收到你的指令', 'Your instruction was received'],
  'event.instruction.done': ['指令已执行', 'Instruction carried out'],
  'event.inbox.processed': ['已处理收件箱', 'Inbox processed'],
  'event.round.started': ['新的一轮开始', 'A new round started'],
  'event.round.finished': ['执行者完成了这一轮', 'The Engineer finished the round'],
  'event.review.finished': ['这一轮的审阅结束', 'Review of the round finished'],
  'event.retry.waiting': ['等待后重试', 'Waiting before retrying'],
  'event.retry.interrupted': ['等待被打断', 'The wait was interrupted'],
  'event.status': ['状态更新', 'Status update'],
  'event.planning.started': ['开始规划', 'Planning started'],
  'event.planning.failed': ['规划没有完成', 'Planning did not complete'],
  'event.planning.tidied': ['计划已整理', 'Plan tidied'],
  'event.planning.decided': ['规划结论', 'Planning decision'],
  'event.task.started': ['开始执行任务', 'Task execution started'],
  'event.task.ended': ['任务执行结束', 'Task execution ended'],
  'event.skills.ready': ['技能库已就绪', 'Skill library ready'],
  'event.routes.formed': ['已形成候选路线组合', 'Candidate route portfolio formed'],
  'event.lessons.skipped': ['本次没有总结经验', 'No lessons recorded this time'],
  'event.argus.reply': ['Argus 的回复', 'Reply from Argus'],
};

/** @deprecated Kept for callers written against the first draft; use PLAIN_BY_KIND. */
export const PLAIN_BY_CODE = PLAIN_BY_KIND;

/**
 * Why work stopped, by the stop kind or pause status the backend records.
 * Each entry is a short clause so it fits after a label ("Task ended — …")
 * and inside a sentence ("The work stopped because …").
 */
const STOP_KINDS: Record<string, Pair> = {
  daemon_shutdown: ['Argus 被暂停', 'Argus was paused'],
  paused_daemon_shutdown: ['Argus 被暂停', 'Argus was paused'],
  operator_pause: ['你暂停了它', 'you paused it'],
  paused_operator: ['你暂停了它', 'you paused it'],
  operator_abort: ['你停止了它', 'you stopped it'],
  budget_exhausted: ['预算用完了', 'the budget ran out'],
  budget_pause: ['预算用完了', 'the budget ran out'],
  paused_budget: ['预算用完了', 'the budget ran out'],
  provider_cooldown: ['模型服务正在限流', 'the model service is cooling down'],
  paused_provider_cooldown: ['模型服务正在限流', 'the model service is cooling down'],
  provider_fence: ['模型服务不可用', 'the model service is unavailable'],
  paused_provider_fence: ['模型服务不可用', 'the model service is unavailable'],
  backend_unavailable: ['模型服务不可用', 'the model service is unavailable'],
  transient_error: ['出现了暂时性的问题', 'a temporary problem came up'],
  permanent_error: ['遇到了无法自动恢复的错误', 'an error could not be recovered from'],
  operator_input_required: ['需要你的回复', 'your reply is needed'],
  planner_empty_plan: ['需要一份新的计划', 'a new plan is needed'],
};

/** Route and mission statuses as short words. */
const STATUSES: Record<string, Pair> = {
  pending: ['待开始', 'Waiting'],
  queued: ['待开始', 'Waiting'],
  not_started: ['待开始', 'Not started'],
  waiting: ['等待中', 'Waiting'],
  idle: ['空闲', 'Idle'],
  running: ['进行中', 'In progress'],
  in_progress: ['进行中', 'In progress'],
  claimed: ['进行中', 'In progress'],
  active: ['进行中', 'In progress'],
  working: ['进行中', 'In progress'],
  framed: ['已明确目标', 'Objective settled'],
  grounding: ['了解项目中', 'Getting oriented'],
  planned: ['已规划', 'Planned'],
  done: ['已完成', 'Done'],
  complete: ['已完成', 'Done'],
  completed: ['已完成', 'Done'],
  success: ['已完成', 'Done'],
  accepted: ['已完成', 'Done'],
  failed: ['失败', 'Failed'],
  error: ['出错', 'Failed'],
  blocked: ['受阻', 'Blocked'],
  rejected: ['需要修改', 'Needs changes'],
  continue: ['需要修改', 'Needs changes'],
  replan: ['需要重新规划', 'Needs replanning'],
  skipped: ['已跳过', 'Skipped'],
  ended: ['已结束', 'Ended'],
  incomplete: ['未完成', 'Not finished'],
  stalled: ['停滞', 'Stalled'],
  paused: ['已暂停', 'Paused'],
  stopped: ['已停止', 'Stopped'],
  cancelled: ['已停止', 'Stopped'],
  aborted: ['已停止', 'Stopped'],
  paused_external_work: ['等待外部工作完成', 'Waiting for external work'],
  superseded: ['已由新方案替代', 'Replaced by a newer plan'],
};

/** Why a round produced no judgment or a task stopped, by the backend's cause code. */
const CAUSES: Record<string, Pair> = {
  engineer_service_dropped: [
    '模型服务在工程师得出可核对的结果之前中断了会话，因此没有可以审阅的内容。Argus 会换一个新会话重试。',
    'The model service dropped the session before the Engineer produced a checkable result, so there was nothing to review. Argus will retry in a fresh session.',
  ],
  reviewer_unreachable: [
    '审阅者的会话在得出结论前就结束了，Argus 会再试一次。',
    "The reviewer's session ended before it reached a conclusion; Argus will try again.",
  ],
  argus_stopped: ['Argus 被停止了，任务随之暂停。', 'Argus was stopped, so the task paused.'],
  task_cancelled: ['这项任务被取消了。', 'The task was cancelled.'],
  session_length_limit: [
    '这一轮达到了单次会话的长度上限；已做的工作保留，会在新的会话里继续。',
    'The round reached the length limit of one session; the work so far is kept and continues in a fresh session.',
  ],
  tools_unavailable: ['执行环境里缺少需要的工具。', 'The tools the work needs are not available in the execution environment.'],
  model_unavailable: ['模型服务暂时不可用。', 'The model service is unavailable for now.'],
  sign_in_failed: ['模型服务的登录没有成功。', 'Signing in to the model service did not succeed.'],
  paused: ['任务被暂停了。', 'The task was paused.'],
  unknown: ['原因暂时不明。', 'The reason is not known yet.'],
  retry_wait_cut_short: ['在等待重试时 Argus 被要求停止。', 'Argus was asked to stop while waiting to retry.'],
};

/** Research stages by id or label. */
const STAGES: Record<string, Pair> = {
  idea: ['选题', 'Idea'],
  scope: ['研究定义', 'Scope'],
  research: ['文献与假设', 'Literature and hypotheses'],
  literature: ['文献与假设', 'Literature and hypotheses'],
  implementation: ['方法实现', 'Implementation'],
  implement: ['方法实现', 'Implementation'],
  experiment: ['实验验证', 'Experiments'],
  experiments: ['实验验证', 'Experiments'],
  analysis: ['结果分析', 'Analysis'],
  writing: ['论文写作', 'Writing'],
  review: ['最终审核', 'Final review'],
  delivery: ['成果交付', 'Delivery'],
  optimize: ['优化', 'Optimization'],
  solve: ['推导与验证', 'Derivation and verification'],
  hold: ['已暂停', 'Paused'],
  paused: ['已暂停', 'Paused'],
};

/** Raw event types that are pure bookkeeping, named for people. */
const EVENT_TYPES: Record<string, string> = {
  'provider.request.started': 'event.model_call.started',
  'provider.request.completed': 'event.model_call.completed',
  'provider.request.failed': 'event.model_call.failed',
  'agent.io.start': 'event.model_call.started',
  'agent.io.complete': 'event.model_call.completed',
  'usage.recorded': 'event.usage.recorded',
  'budget.reservation.created': 'event.budget.reserved',
  'budget.reservation.settled': 'event.budget.settled',
  'role.session.turn': 'event.model_turn',
  'daemon.command.submitted': 'event.instruction.received',
  'daemon.command.completed': 'event.instruction.done',
  'life.inbox.drained': 'event.inbox.processed',
  'round.start': 'event.round.started',
  'round.main.completed': 'event.round.finished',
  'round.review.completed': 'event.review.finished',
  'round.backend_failure.backoff': 'event.retry.waiting',
  'round.backend_failure.hold_interrupted': 'event.retry.interrupted',
  'life.status': 'event.status',
  'life.planner.start': 'event.planning.started',
  'life.planner.error': 'event.planning.failed',
  'life.planner.normalized': 'event.planning.tidied',
  'life.planner.verdict': 'event.planning.decided',
  'loop.start': 'event.task.started',
  'loop.done': 'event.task.ended',
  'skill.library.available': 'event.skills.ready',
  'idea.portfolio.formed': 'event.routes.formed',
  'team.learning.review.skipped': 'event.lessons.skipped',
  'ui.argus': 'event.argus.reply',
};

/** A research stage as a word people use; unknown stages pass through. */
export function plainStage(stage: string | null | undefined, locale: Locale): string {
  const raw = String(stage ?? '').trim();
  const pair = STAGES[raw.toLowerCase()];
  return pair ? pick(pair, locale) : raw;
}

export function plainStopKind(kind: string | null | undefined, locale: Locale): string {
  const key = String(kind ?? '').trim().toLowerCase();
  const pair = STOP_KINDS[key];
  return pair ? pick(pair, locale) : key;
}

/** The stop kind as a whole sentence, for a line of its own. */
export function plainStopSentence(kind: string | null | undefined, locale: Locale): string {
  const key = String(kind ?? '').trim().toLowerCase();
  const pair = STOP_KINDS[key];
  if (!pair) return key;
  return locale === 'zh-CN' ? `因为${pair[0]}，工作停了下来。` : `The work stopped because ${pair[1]}.`;
}

function withStopSuffix(base: string, stopKind: string | undefined, locale: Locale): string {
  if (!stopKind) return base;
  const reason = plainStopKind(stopKind, locale);
  return locale === 'zh-CN'
    ? `${base.replace(/。$/, '')}（${reason}）。`
    : `${base.replace(/\.$/, '')} — ${reason}.`;
}

export function plainProgress(done: number, total: number, locale: Locale): string {
  if (!total) return pick(['尚未规划', 'Not planned yet'], locale);
  return locale === 'zh-CN' ? `已完成 ${done} / ${total}` : `${done} of ${total} done`;
}

type Render = string | ((match: RegExpMatchArray, locale: Locale) => string);

/** Legacy English titles and labels (the TS mirror reducer and older snapshots), matched whole. */
const PHRASES: Array<[RegExp, Render]> = [
  [/^(?:Review not performed|No review this round)$/i, 'round_not_judged'],
  [/^Goal framed$/i, 'goal_framed'],
  [/^(?:Grounding project|Project grounding started)$/i, 'grounding_started'],
  [/^Manager routing failed$/i, 'request_not_routed'],
  [/^Stage reviewed$/i, 'stage_reviewed'],
  [/^(?:Planning next work|Planning started)$/i, 'planning_started'],
  [/^Planning complete$/i, 'planning_complete'],
  [/^Planner waiting$/i, 'planner_waiting'],
  [/^Project reviewed$/i, 'project_finished'],
  [/^Research branch added$/i, 'research_route_added'],
  [/^Task added$/i, 'task_added'],
  [/^Reporting progress$/i, 'progress_message'],
  [/^(?:Running a command|running project command)$/i, 'progress_command'],
  [/^(?:Using a tool|using a tool)$/i, 'progress_tool'],
  [/^inspecting project state$/i, 'progress_inspecting'],
  [/^(?:Engineer handoff ready|Work ready for review)$/i, 'engineer_round_finished'],
  [/^(?:Review started|Reviewing benchmark evidence)$/i, 'checking_started'],
  [/^(?:Continuing before review|Continued before review)$/i, 'continuing_before_check'],
  [/^Review deferred for one round$/i, 'check_postponed'],
  [/^Waiting for the Engineer to finish$/i, 'awaiting_engineer'],
  [/^Waiting on external work$/i, 'waiting_external'],
  [/^(?:Evidence accepted|Accepted evidence)$/i, 'results_accepted'],
  [/^(?:Attempt rejected|Requested another attempt)$/i, 'another_attempt_requested'],
  [/^Handed off$/i, 'handed_off'],
  [/^(?:Mission started|Starting mission)$/i, 'mission_started'],
  [/^(?:Task completed|Mission complete)$/i, 'mission_completed'],
  [/^Task failed$/i, 'mission_failed'],
  [/^Awaiting Planner$/i, 'planner_waiting'],
  [/^Ready for a new mission$/i, 'mission_ready'],
  [/^Waiting$/i, 'waiting'],
  [/^Working$/i, 'live_activity'],
  [/^Capability unlocked$/i, 'capability_unlocked'],
  [/^Capability upgraded$/i, 'capability_upgraded'],
  [/^Capability promoted to source$/i, 'capability_promoted'],
  [/^Knowledge captured$/i, 'knowledge_captured'],
  [/^Knowledge refined$/i, 'knowledge_refined'],
  [/^Knowledge promoted$/i, 'knowledge_promoted'],
  [/^Knowledge demoted$/i, 'knowledge_demoted'],
  [/^Knowledge retired$/i, 'knowledge_retired'],
  [/^(?:request · started|io · start)$/i, 'event.model_call.started'],
  [/^(?:request · completed|io · complete)$/i, 'event.model_call.completed'],
  [/^usage · recorded$/i, 'event.usage.recorded'],
  [/^reservation · created$/i, 'event.budget.reserved'],
  [/^reservation · settled$/i, 'event.budget.settled'],
  [/^Round (\d+) started$/i, (match, locale) => fill(pick(PLAIN_BY_KIND.round_started, locale), { n: match[1] })],
  [/^Running round (\d+)$/i, (match, locale) => fill(pick(PLAIN_BY_KIND.round_running, locale), { n: match[1] })],
  [/^Stage (?:·|→) (.+)$/i, (match, locale) => fill(pick(PLAIN_BY_KIND.stage_entered, locale), { stage: plainStage(match[1], locale) })],
  [/^Mission (ended|incomplete|stalled|blocked|failed)(?: · (\S+))?$/i, (match, locale) => withStopSuffix(
    pick(PLAIN_BY_KIND[`mission_${match[1].toLowerCase()}`], locale), match[2], locale,
  )],
  [/^(Manager|Planner|Engineer|Reviewer) failed$/i, (match, locale) => fill(pick(PLAIN_BY_KIND.role_failed, locale), { role: match[1] })],
  [/^(\d+) \/ (\d+) complete$/i, (match, locale) => plainProgress(Number(match[1]), Number(match[2]), locale)],
  [/^Not planned$/i, (_match, locale) => plainProgress(0, 0, locale)],
];

/** Where a sentence came from: the backend's stable kind and the language it wrote in. */
export interface PlainSource {
  kind?: string | null;
  /** "zh" or "en" from the mission view; empty when the backend has not said. */
  language?: string | null;
}

function uiLanguage(locale: Locale): string {
  return locale === 'zh-CN' ? 'zh' : 'en';
}

/**
 * A title or short label in plain words. When the backend names what happened
 * with a kind and already wrote the sentence in the reader's language, that
 * sentence is used as it came; a kind in another language is rendered from the
 * table; text without a kind is matched against the legacy phrases; anything
 * else is returned unchanged.
 */
export function plainStatus(raw: string | null | undefined, locale: Locale, source?: PlainSource | string | null): string {
  const text = String(raw ?? '').trim();
  const origin: PlainSource = typeof source === 'string' ? { kind: source } : source ?? {};
  const own = origin.kind ? PLAIN_BY_KIND[origin.kind] : undefined;
  if (origin.kind) {
    const otherLanguage = Boolean(origin.language) && origin.language !== uiLanguage(locale);
    if (own && (otherLanguage || !text) && !own[0].includes('{')) return pick(own, locale);
    if (text && !otherLanguage) return text;
  }
  for (const [pattern, render] of PHRASES) {
    const match = text.match(pattern);
    if (!match) continue;
    return typeof render === 'string' ? pick(PLAIN_BY_KIND[render], locale) : render(match, locale);
  }
  if (own) {
    const template = pick(own, locale);
    if (!template.includes('{')) return template;
    const number = text.match(/\d+/)?.[0];
    if (number && template.includes('{n}')) return fill(template, { n: number });
    return text;
  }
  const status = STATUSES[text.toLowerCase()];
  return status ? pick(status, locale) : text;
}

/** The sentence for a cause code, or an empty string when the code is unknown. */
export function plainCause(cause: string | null | undefined, locale: Locale): string {
  const pair = CAUSES[String(cause ?? '').trim().toLowerCase()];
  return pair ? pick(pair, locale) : '';
}

/** A route, task or role status as one short word. Unknown statuses pass through. */
export function plainRouteStatus(status: string | null | undefined, locale: Locale): string {
  const key = String(status ?? '').trim().toLowerCase();
  const pair = STATUSES[key];
  return pair ? pick(pair, locale) : key;
}

/** A raw event type as a plain name, or null when it is not a known bookkeeping event. */
export function plainEventName(type: string | null | undefined, locale: Locale): string | null {
  const code = EVENT_TYPES[String(type ?? '').trim()];
  return code ? pick(PLAIN_BY_KIND[code], locale) : null;
}

/** Events that only record accounting and model traffic, not research work. */
export function isBookkeepingEvent(type: string | null | undefined): boolean {
  return /^(?:provider\.|agent\.io\.|usage\.|budget\.|role\.session\.turn$)/.test(String(type ?? ''));
}

export interface PlainDetail {
  /** The sentence for the page. */
  text: string;
  /** Streak counters, exit codes and receipts that were dropped from the page. */
  technical: string;
}

type DetailRender = Pair | ((match: RegExpMatchArray, locale: Locale) => string | PlainDetail);

/** Sentence openers the backend writes into reasons and details, matched at the start of a paragraph. */
const DETAIL_SENTENCES: Array<[RegExp, DetailRender]> = [
  [/^Next action:\s*/i, ['下一步：', 'Next: ']],
  [/^(?:原因：|Reason:)\s*/i, ['原因：', 'Reason: ']],
  [/^The backend has failed the same way (\d+) times in a row \(([^)]*)\)\. Waiting ([\d.]+)s before the next attempt[^\n]*/i,
    (match, locale) => ({
      text: locale === 'zh-CN'
        ? `模型服务已连续 ${match[1]} 次以同样的方式失败；为了不让持续的故障白白花钱，${match[3]} 秒后再试。`
        : `The model service has failed the same way ${match[1]} times in a row; waiting ${match[3]} seconds before trying again so a standing failure does not keep costing money.`,
      technical: match[2],
    })],
  // "review: skipped (backend failure) — " only announces the sentence that follows it.
  [/^review: \w+(?: \([^)]*\))? — /i, ['', '']],
  [/^status=(\S+) rounds=(\d+) reason=/i, (match, locale) => {
    const stop = STOP_KINDS[match[1].toLowerCase()];
    const how = stop ? pick(stop, locale) : plainRouteStatus(match[1], locale);
    return locale === 'zh-CN'
      ? `任务已结束（${how}），共 ${match[2]} 轮。`
      : `Task ended — ${how}, after ${match[2]} rounds.`;
  }],
  [/^engineer round (\d+) \((?:fresh session|new session)\)\.?$/i,
    (match, locale) => locale === 'zh-CN' ? `第 ${match[1]} 轮，在新的会话里开始。` : `Round ${match[1]}, started in a fresh session.`],
  [/^engineer round (\d+) \([^)]*\)\.?$/i,
    (match, locale) => locale === 'zh-CN' ? `第 ${match[1]} 轮开始。` : `Round ${match[1]} started.`],
  [/^backend failure; retrying in a fresh \w+ session after ([\d.]+)s\.?$/i,
    (match, locale) => locale === 'zh-CN'
      ? `模型服务调用失败，${match[1]} 秒后在新的会话里重试。`
      : `The model service call failed; retrying in a fresh session after ${match[1]} seconds.`],
  [/^planner error: (.*; retry later)\.?$/i, (match, locale) => ({
    text: locale === 'zh-CN' ? '规划这一步没有完成，稍后会重试。' : 'Planning did not complete; it will be retried later.',
    technical: match[1],
  })],
  [/^mission failed\.?$/i, ['任务失败了。', 'The task failed.']],
  [/^Engineer backend failed before a trustworthy completed turn; reviewer skipped\.?/i,
    ['这一轮模型服务没有完成调用，所以没有人审阅。', 'The model service did not complete this round, so no one reviewed it.']],
  [/^Reviewer backend unavailable for (\d+) consecutive attempt\(s\); failing loud rather than settling the round without a real review\.?/i,
    (match, locale) => locale === 'zh-CN'
      ? `审阅者的模型服务连续 ${match[1]} 次没有响应，这一轮因此没有得到审阅。`
      : `The reviewer's model service did not respond ${match[1]} times in a row, so this round was not reviewed.`],
  [/^The Reviewer's session ended before it reached a conclusion, so this round was not judged\.?/i,
    ['审阅者在得出结论前就中断了，这一轮没有被评判。', 'The reviewer stopped before reaching a conclusion, so this round was not judged.']],
  [/^The wait after a repeated backend failure ended early: daemon stop requested\.?/i,
    ['在等待重试时 Argus 被要求停止，任务随之暂停。', 'Argus was asked to stop while waiting to retry, so the task paused.']],
  [/^Backend call paused before a trustworthy completed turn \(stop_kind=(\w+)\); reviewer skipped\.?/i,
    (match, locale) => locale === 'zh-CN'
      ? `这一轮在得出可信结果前被暂停（${plainStopKind(match[1], locale)}），所以没有人审阅。`
      : `This round was paused before it produced a trustworthy result (${plainStopKind(match[1], locale)}), so no one reviewed it.`],
  [/^One Engineer call used its whole per-call provider-turn allowance \([^)]*\); reviewer skipped\. The work so far is kept and the task continues in a fresh session from the checkpoint\.?/i,
    ['这一轮用完了单次调用的额度；已做的工作保留，会在新的会话里接着做，本轮没有审阅。', 'This round used up its per-call allowance; the work so far is kept and continues in a fresh session. No review this round.']],
  [/^Execution host is unavailable; reviewer skipped\.?/i,
    ['执行环境暂时不可用，这一轮没有审阅。', 'The execution environment is unavailable, so this round was not reviewed.']],
  [/^Copilot CLI exited with code \d+\.?/i,
    ['模型服务的调用没有完成。', 'The model service call did not complete.']],
  [/^Grounded bounded plan completed with (\d+) step\(s\)\.?/i,
    (match, locale) => locale === 'zh-CN' ? `规划完成，共 ${match[1]} 步。` : `Planning finished with ${match[1]} steps.`],
  [/^Retry in a fresh \w+ session; do not resume the failed thread\.(?:\s*If this repeats, pause the daemon and reduce concurrent \w+ load\.)?/i,
    ['接下来会在新的会话里重试这一轮。', 'The round will be retried in a fresh session.']],
  [/^Restart the daemon to resume this mission from its checkpoint\.?/i,
    ['重新运行 Argus 即可从保存的进度继续。', 'Run Argus again to continue from the saved progress.']],
  [/^Resume this mission when the operator is ready\.?/i,
    ['准备好后即可恢复这项任务。', 'Resume the task whenever you are ready.']],
  [/^Resume from the persisted checkpoint after the blocking budget or provider condition has been cleared\.?/i,
    ['预算或模型服务恢复后，即可从保存的进度继续。', 'Once the budget or model service is back, the task continues from the saved progress.']],
  [/^inspecting project state$/i, ['正在查看项目状态', 'Looking at the project state']],
  [/^running project command$/i, ['正在运行项目命令', 'Running a project command']],
  [/^using a tool$/i, ['正在使用工具', 'Using a tool']],
];

/** Text that reads as a counter, a receipt or an exit code rather than a sentence. */
const TECHNICAL_TAIL = /^(?:[\w.]+_(?:streak|code|error|kind)=|error=|exit=|fatal_error=|Runner receipt:|stop_kind=|backend_)/i;

function plainParagraph(paragraph: string, locale: Locale): PlainDetail {
  let rest = paragraph.trim();
  const out: string[] = [];
  const technical: string[] = [];
  let recognised = false;
  while (rest) {
    let matched = false;
    for (const [pattern, render] of DETAIL_SENTENCES) {
      const match = rest.match(pattern);
      if (!match) continue;
      const rendered = typeof render === 'function' ? render(match, locale) : pick(render, locale);
      if (typeof rendered === 'string') out.push(rendered);
      else {
        out.push(rendered.text);
        if (rendered.technical) technical.push(rendered.technical);
      }
      rest = rest.slice(match[0].length).replace(/^[\s;,]+/, '');
      matched = true;
      recognised = true;
      break;
    }
    if (matched) continue;
    if (recognised && TECHNICAL_TAIL.test(rest)) {
      technical.push(rest);
    } else {
      out.push(rest);
    }
    rest = '';
  }
  const joiner = locale === 'zh-CN' ? '' : ' ';
  return { text: out.filter(Boolean).join(joiner).replace(/：\s+/g, '：'), technical: technical.join(' ') };
}

/**
 * A reason or detail in plain words. Known openers are replaced sentence by
 * sentence; the counters and receipts that follow them are returned as
 * `technical` for a tooltip. Unknown text is returned unchanged.
 */
export interface PlainDetailSource extends PlainSource {
  cause?: string | null;
  technical?: string | null;
}

export function plainDetail(raw: string | null | undefined, locale: Locale, source?: PlainDetailSource): PlainDetail {
  const text = String(raw ?? '').trim();
  if (source) {
    const otherLanguage = Boolean(source.language) && source.language !== uiLanguage(locale);
    const byCause = otherLanguage ? plainCause(source.cause, locale) : '';
    if (byCause) return { text: byCause, technical: String(source.technical ?? '') };
    if (source.technical) {
      // The backend already separated the sentence from its record.
      return { text, technical: String(source.technical) };
    }
  }
  if (!text) return { text: '', technical: '' };
  // Each line is read on its own so an opener such as "原因：" can introduce
  // a known sentence; the original line breaks are kept.
  const parts = text.split(/(\n[ \t]*\n|\n)/);
  const rendered: string[] = [];
  const technical: string[] = [];
  parts.forEach((part, index) => {
    if (index % 2 === 1) {
      rendered.push(part.includes('\n\n') || /\n[ \t]*\n/.test(part) ? '\n\n' : '\n');
      return;
    }
    const paragraph = plainParagraph(part, locale);
    rendered.push(paragraph.text);
    if (paragraph.technical) technical.push(paragraph.technical);
  });
  return {
    text: rendered.join('').replace(/\n{3,}/g, '\n\n').trim(),
    technical: technical.join(' '),
  };
}
