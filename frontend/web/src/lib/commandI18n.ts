import type { CommandId, SlashCommand } from '../../../core/src/commands';
import type { Locale } from '../i18n';

const GROUPS: Record<SlashCommand['group'], string> = {
  Everyday: '常用',
  'Task management': '任务管理',
  'Sessions & diagnostics': '项目与诊断',
  Configuration: '配置',
  Other: '其他',
};

const DESCRIPTIONS: Record<CommandId, string> = {
  crystalpilot: '在当前 Argus 会话启用晶体学工具，保持原生界面',
  status: '查看角色、待办和运行状况',
  roles: '查看各角色的后端、模型、推理强度和实时活动',
  backlog: '查看待处理任务（all 包含已完成和已跳过）',
  artifacts: '查看审阅者认可的结果文件（按 Enter 预览）',
  artifact: '预览一个已批准的结果文件',
  events: '搜索动态：all / watch / milestones / messages',
  find: '搜索当前事件缓冲区',
  cancel: '不再等这次回复',
  ask: '直接回答，不安排任务',
  task: '直接交给团队做',
  plan: '预览规划者排的计划',
  rewrite: '发送前先帮你改写一下',
  nudge: '向正在运行的任务注入指导',
  abort: '立即终止正在运行的任务',
  note: '向时间线添加手动备注',
  item: '查看这项任务的完整要求',
  run: '返回持续更新的任务动态',
  new: '检查、创建并切换到新项目',
  daemons: '查找全部会话并切换或创建',
  resume: '切换到其他项目',
  attach: '跟随其他项目并读取其动态',
  rename: '重命名当前项目',
  doctor: '诊断为什么没有任务运行',
  backend: '查看或更改用的模型后端',
  config: '查看或更改运行时设置',
  identity: '查看或替换你的身份卡',
  reset: '让经理重新理解这段对话',
  skills: '查看或提升技能',
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
