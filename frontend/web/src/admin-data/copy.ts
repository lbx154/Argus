import { useI18n, type Locale } from '../i18n';

export function useAdminText() {
  const { locale, t, setLocale } = useI18n();
  return { locale, t, setLocale, text: (zh: string, en: string) => locale === 'zh-CN' ? zh : en };
}

export function dateLabel(value: number | null | undefined, locale: Locale): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? new Intl.DateTimeFormat(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(value * 1000)
    : '—';
}

export function roleName(role: string, locale: Locale): string {
  const names: Record<string, [string, string]> = {
    manager: ['统筹', 'Manager'], planner: ['规划', 'Planner'], engineer: ['执行', 'Engineer'],
    reviewer: ['审查', 'Reviewer'], operator: ['用户', 'User'], unknown: ['角色未记录', 'Unassigned role'],
    system: ['系统', 'System'], developer: ['运行指令', 'Instructions'], user: ['用户 / 任务', 'User / task'],
    assistant: ['模型', 'Assistant'], tool: ['工具', 'Tool'],
  };
  return names[role]?.[locale === 'zh-CN' ? 0 : 1] ?? role;
}

export function stateLabel(state: string | undefined, locale: Locale): string {
  const names: Record<string, [string, string]> = {
    complete: ['调用已结束', 'Call ended'], capturing: ['尚无结束记录', 'No end record yet'],
    interrupted: ['采集中断', 'Capture interrupted'], quarantined: ['需检查记录', 'Record needs attention'],
    approved: ['已验收', 'Reviewed'], not_approved: ['未验收', 'Not reviewed'],
    unknown: ['结果未确认', 'Outcome unconfirmed'], running: ['采集正常', 'Collector running'],
    storage_error: ['存储异常', 'Storage unavailable'], starting: ['正在启动', 'Starting'],
    listening: ['已连接', 'Listening'], unavailable: ['暂不可用', 'Unavailable'],
  };
  return names[state || 'unknown']?.[locale === 'zh-CN' ? 0 : 1] ?? state ?? '—';
}

export function eventLabel(kind: string, locale: Locale): string {
  const names: Record<string, [string, string]> = {
    context: ['Agent 上下文', 'Agent context'], provider_request: ['模型实际输入', 'Model request'],
    message_end: ['公开消息', 'Public message'], agent_end: ['调用结果', 'Call output'],
    message_delta: ['公开输出增量', 'Public output increments'], tool_call: ['工具调用', 'Tool call'],
    tool_result: ['工具结果', 'Tool result'], tool_execution_start: ['工具开始执行', 'Tool execution started'],
    tool_execution_update: ['工具执行更新', 'Tool execution update'], tool_execution_end: ['工具执行结束', 'Tool execution ended'],
    settled: ['采集已收尾', 'Capture settled'], quarantine: ['记录不完整', 'Incomplete record'],
    capture_warning: ['采集提示', 'Capture notice'],
  };
  return names[kind]?.[locale === 'zh-CN' ? 0 : 1] ?? kind;
}
