import type { CommandId, SlashCommand } from '../../../core/src/commands';
import type { Locale } from '../i18n';

const GROUPS: Record<SlashCommand['group'], string> = {
  Everyday: '常用',
  'Task management': '任务管理',
  'Sessions & diagnostics': '会话与诊断',
  Configuration: '配置',
  Other: '其他',
};

const DESCRIPTIONS: Record<CommandId, string> = {
  status: '查看角色、队列、日志和健康状态',
  roles: '查看各角色的后端、模型、推理强度和实时活动',
  journal: '查看近期日志（默认 10 条）',
  backlog: '查看待处理任务（all 包含已完成和已跳过）',
  artifacts: '查看 Reviewer 批准的结果文件（按 Enter 预览）',
  artifact: '预览一个已批准的结果文件',
  events: '搜索动态：all / watch / milestones / messages',
  find: '搜索当前事件缓冲区',
  cancel: '停止等待当前 Manager 回复',
  ask: '直接回答，不排任务、不走 Planner/Engineer/Reviewer',
  task: '直接加入任务队列',
  plan: '预览 Planner 编写的执行计划',
  rewrite: '让 Manager 在发送前改写提示词',
  nudge: '向正在运行的任务注入指导',
  abort: '立即终止正在运行的任务',
  note: '向时间线添加手动备注',
  done: '将任务标记为完成',
  skip: '跳过任务',
  stop: '停止任务的自动迭代',
  item: '查看完整任务契约',
  run: '返回持续更新的任务动态',
  new: '检查、创建并切换到新会话',
  daemons: '查找全部会话并切换或创建',
  resume: '切换到其他项目或会话',
  attach: '跟随其他项目并读取其动态',
  rename: '重命名当前会话',
  doctor: '诊断为什么没有任务运行',
  backend: '查看或更改共享 Runner 后端',
  config: '查看或更改运行时设置',
  identity: '查看或替换操作者身份卡',
  reset: '清除 Manager 的热会话上下文',
  skills: '查看或提升运行时 Skill',
  clear: '清空事件动态视图',
  reconnect: '重新连接实时动态',
  help: '查看快捷键和完整命令参考',
  quit: '离开控制台（后台工作继续运行）',
};

export function commandDescription(command: SlashCommand, locale: Locale): string {
  if (locale === 'zh-CN') return DESCRIPTIONS[command.id];
  return command.id === 'reconnect' ? 'reconnect live activity' : command.desc;
}

export function commandGroup(command: SlashCommand, locale: Locale): string {
  return locale === 'zh-CN' ? GROUPS[command.group] : command.group;
}

export function localizedHelpGroups(
  commands: readonly SlashCommand[],
  locale: Locale,
): Array<{ group: string; rows: Array<{ label: string; desc: string }> }> {
  const groups = new Map<string, Array<{ label: string; desc: string }>>();
  for (const command of commands) {
    const group = commandGroup(command, locale);
    const aliasNote = command.aliases?.length ? `  (= ${command.aliases.join(', ')})` : '';
    const label = `${command.name}${command.arg ? ` ${command.arg}` : ''}${aliasNote}`;
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group)!.push({ label, desc: commandDescription(command, locale) });
  }
  return [...groups.entries()].map(([group, rows]) => ({ group, rows }));
}
