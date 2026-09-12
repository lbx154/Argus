import type {
  CollaborationProject, CollaborationTask, ObservedEpisode, ObservedEvent, ObservedPage,
  ProjectIdentity, RecordedRole, UnknownRecord,
} from './types';

const ROLE_LABELS: Record<RecordedRole, string> = {
  manager: '统筹', planner: '规划', engineer: '执行', reviewer: '审查',
  operator: '用户', unknown: '角色未记录',
};
const PRIVATE_TYPES = new Set(['thinking', 'analysis', 'reasoning', 'redacted_thinking', 'signature']);
const ROLE_PATTERN = /^(manager|planner|engineer|reviewer)(?:[.-][a-z0-9_.-]+)?$/;

function record(value: unknown): UnknownRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as UnknownRecord : null;
}

function string(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function json(value: unknown): string {
  return value === undefined || value === null ? '' : JSON.stringify(value, null, 2);
}

export function projectKey(project: ProjectIdentity): string {
  return JSON.stringify([project.tenant_id, project.sid]);
}

export function taskKey(task: ProjectIdentity & { task_id?: string | null }): string {
  return JSON.stringify([task.tenant_id, task.sid, task.task_id ?? null]);
}

export function projectName(project: Pick<CollaborationProject, 'title' | 'sid'>): string {
  return project.title?.trim() || project.sid || '未命名项目';
}

/** Use beside the name so identically named projects remain distinguishable. */
export function projectIdentityLabel(project: ProjectIdentity): string {
  return `${project.tenant_id} / ${project.sid}`;
}

export function taskName(task: Pick<CollaborationTask, 'mission_title' | 'title' | 'task_id'>): string {
  const title = task.mission_title || task.title;
  return title?.trim().replace(/^#+\s*/, '').split('\n')[0]
    || (task.task_id ? `任务 ${task.task_id}` : '项目活动');
}

/** Only producer-recorded role fields are normalized; prompt prose is never used. */
export function normalizeRole(value: unknown): RecordedRole {
  if (typeof value !== 'string') return 'unknown';
  if (Object.prototype.hasOwnProperty.call(ROLE_LABELS, value)) return value as RecordedRole;
  return (value.match(ROLE_PATTERN)?.[1] as RecordedRole | undefined) ?? 'unknown';
}

export function roleLabel(value: unknown): string {
  return ROLE_LABELS[normalizeRole(value)];
}

export function episodeRole(episode: { role?: unknown; runtime?: { run_label?: unknown } }): RecordedRole {
  // An explicit unknown from the API must not be overridden by a prompt or label.
  return typeof episode.role === 'string'
    ? normalizeRole(episode.role) : normalizeRole(episode.runtime?.run_label);
}

export interface TaskDeepLink {
  tenant?: string | null;
  sid?: string | null;
  taskId?: string | null;
}

export function parseTaskLink(search: string | URLSearchParams): TaskDeepLink {
  const params = typeof search === 'string' ? new URLSearchParams(search) : search;
  return {
    tenant: params.get('tenant'), sid: params.get('sid'),
    taskId: params.get('task_id') ?? params.get('task'),
  };
}

/** Missing explicit links return null; an ambiguous SID never selects another tenant. */
export function selectTaskFromLink(
  tasks: readonly CollaborationTask[], input: TaskDeepLink | URLSearchParams | string,
): CollaborationTask | null {
  const link = typeof input === 'string' || input instanceof URLSearchParams ? parseTaskLink(input) : input;
  let candidates = tasks.filter((task) => (!link.tenant || task.tenant_id === link.tenant)
    && (!link.sid || task.sid === link.sid));
  if (link.sid && !link.tenant && new Set(candidates.map(projectKey)).size > 1) return null;
  if (link.taskId !== undefined && link.taskId !== null) {
    candidates = candidates.filter((task) => task.task_id === (link.taskId || null));
    if (!link.sid && new Set(candidates.map(projectKey)).size > 1) return null;
  } else {
    const actualTasks = candidates.filter((task) => task.task_id !== null);
    if (actualTasks.length) candidates = actualTasks;
  }
  return [...candidates].sort((left, right) => (right.last_observed_at ?? 0) - (left.last_observed_at ?? 0)
    || taskKey(left).localeCompare(taskKey(right)))[0] ?? null;
}

/**
 * An episode can span pages. Its sequence is its observation identity, not the
 * text of a delta: repeated equal strings at different sequences are real data.
 * Collection/quality metadata remain source metadata, independent of page size.
 */
export function mergeObservationPages(pages: readonly ObservedPage[]): ObservedEpisode[] {
  const first = pages[0];
  if (!first) return [];
  const scope = JSON.stringify([first.tenant_id, first.sid, first.purpose, first.task_id]);
  const episodes = new Map<number, ObservedEpisode>();
  const events = new Map<number, Map<number, ObservedEvent>>();
  for (const page of pages) {
    if (JSON.stringify([page.tenant_id, page.sid, page.purpose, page.task_id]) !== scope) {
      throw new Error('Cannot merge observations from different project, purpose, or task scopes.');
    }
    for (const episode of page.episodes) {
      if (projectKey(episode) !== projectKey(page)
        || (page.task_id !== null && episode.task_id !== page.task_id)) {
        throw new Error('Observation episode does not match its page scope.');
      }
      const previous = episodes.get(episode.episode_id);
      if (previous && previous.task_id !== episode.task_id) {
        throw new Error('Observation episode changed task association.');
      }
      const bySequence = events.get(episode.episode_id) ?? new Map<number, ObservedEvent>();
      for (const event of episode.events) {
        if (!Number.isSafeInteger(event.sequence) || event.sequence < 0) {
          throw new Error('Observation sequence is invalid.');
        }
        bySequence.set(event.sequence, event);
      }
      events.set(episode.episode_id, bySequence);
      episodes.set(episode.episode_id, { ...episode, events: [] });
    }
  }
  return [...episodes.values()].sort((left, right) => left.episode_id - right.episode_id).map((episode) => ({
    ...episode,
    events: [...events.get(episode.episode_id)!.values()].sort((left, right) => left.sequence - right.sequence),
  }));
}

function privateStructure(value: UnknownRecord): boolean {
  return [value.type, value.role, value.channel].some((item) => typeof item === 'string' && PRIVATE_TYPES.has(item));
}

function publicToolCalls(value: unknown): string {
  if (!Array.isArray(value)) return '';
  return value.map((item) => {
    const call = record(item);
    const fn = record(call?.function);
    if (!call || !fn) return '';
    return [string(fn.name), typeof fn.arguments === 'string' ? fn.arguments : json(fn.arguments)]
      .filter(Boolean).join('\n');
  }).filter(Boolean).join('\n\n');
}

/** Plain public message/content text. Unknown structured blocks are not rendered as prose. */
export function publicText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(publicText).filter(Boolean).join('\n');
  const item = record(value);
  if (!item || privateStructure(item)) return '';
  if (item.type === 'text' || item.type === 'input_text' || item.type === 'output_text') return string(item.text);
  if (item.type === 'toolCall' || item.type === 'tool_call') {
    return [string(item.name), typeof item.arguments === 'string' ? item.arguments : json(item.arguments)]
      .filter(Boolean).join('\n');
  }
  if ('content' in item || 'tool_calls' in item) {
    return [publicText(item.content), publicToolCalls(item.tool_calls)].filter(Boolean).join('\n\n');
  }
  return '';
}

export function publicMessagesText(messages: unknown): string {
  if (!Array.isArray(messages)) return '';
  const labels: Record<string, string> = {
    system: '系统', developer: '开发者', user: '用户', assistant: '模型', toolResult: '工具返回', tool: '工具返回',
  };
  return messages.map((value) => {
    const message = record(value);
    if (!message || privateStructure(message)) return '';
    const text = [publicText(message.content), publicToolCalls(message.tool_calls)].filter(Boolean).join('\n\n');
    if (!text) return '';
    return `${labels[string(message.role)] || string(message.role) || '消息'}\n${text}`;
  }).filter(Boolean).join('\n\n');
}

export interface EventView {
  key: string;
  kind: string;
  title: string;
  category: 'input' | 'output' | 'tool' | 'status' | 'other';
  text: string;
  toolName: string | null;
  toolCallId: string | null;
  /** Only a recorded boolean result sets success/error. A tool start proves neither. */
  result: 'success' | 'error' | 'unknown' | null;
  timestamp: number | null;
}

export function eventView(event: ObservedEvent): EventView {
  const payload = event.payload;
  const labels: Record<string, [string, EventView['category']]> = {
    context: ['模型输入', 'input'], provider_request: ['模型请求', 'input'],
    message_delta: ['模型公开输出片段', 'output'], message_end: ['模型消息', 'output'],
    session_message: ['历史会话消息', 'output'], agent_end: ['模型输出', 'output'],
    tool_call: ['工具调用', 'tool'], tool_result: ['工具返回', 'tool'],
    tool_execution_start: ['工具执行开始', 'tool'], tool_execution_update: ['工具执行进度', 'tool'],
    tool_execution_end: ['工具执行结束', 'tool'], settled: ['本段过程结束', 'status'],
    quarantine: ['过程状态记录', 'status'], capture_warning: ['采集提示', 'status'],
  };
  const [title, category] = labels[event.kind] ?? [`过程事件 · ${event.kind}`, 'other'];
  let text = '';
  if (event.kind === 'message_delta') {
    text = payload.type === 'text_delta' || payload.type === 'toolcall_delta' ? string(payload.delta) : '';
  } else if (['context', 'provider_request', 'message_end', 'session_message', 'agent_end'].includes(event.kind)) {
    text = publicMessagesText(payload.messages);
  } else if (event.kind === 'tool_call') text = json(payload.input);
  else if (event.kind === 'tool_result') text = publicText(payload.content);
  else if (event.kind === 'tool_execution_start') text = json(payload.args);
  else if (event.kind === 'tool_execution_update') text = publicText(payload.partialResult);
  else if (event.kind === 'tool_execution_end') text = publicText(payload.result);
  else if (event.kind === 'quarantine' || event.kind === 'capture_warning') text = string(payload.reason);
  const isResult = event.kind === 'tool_result' || event.kind === 'tool_execution_end';
  return {
    key: event.id ?? String(event.sequence), kind: event.kind, title, category, text,
    toolName: string(payload.toolName) || null, toolCallId: string(payload.toolCallId) || null,
    result: isResult ? payload.isError === true ? 'error' : payload.isError === false ? 'success' : 'unknown' : null,
    timestamp: typeof event.observed_at === 'number' && Number.isFinite(event.observed_at) ? event.observed_at : null,
  };
}

export function collectionLabel(episode: ObservedEpisode): string {
  if (episode.runtime?.recovery) return '历史会话已恢复';
  if (episode.collection.complete) return '本段采集已结束';
  if (episode.state === 'capturing') return '采集中';
  if (episode.collection.event_count > 0) return '已保留部分过程';
  return '尚无事件内容';
}

export function qualityLabel(episode: Pick<ObservedEpisode, 'quality'>): string {
  return episode.quality.approved === true && episode.quality.state === 'approved' ? '质量已验收' : '质量尚未验收';
}
