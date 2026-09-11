import { Code2, FlaskConical, FolderKanban } from 'lucide-react';

// One navigation definition for both the tab strip and the overview cards.
export const WORKSPACE_DESTINATIONS = [
  { id: 'experiments', zh: '运行进程', en: 'Execution', zhDesc: '查看当前步骤、任务路线，以及团队里谁在做什么。', enDesc: 'Follow the current step, the task route, and who on the team is doing what.', icon: FlaskConical, color: 'blue' },
  { id: 'ide', zh: 'AI IDE', en: 'AI IDE', zhDesc: '阅读项目文件，查看 Git 状态与 Argus 活动。', enDesc: 'Read project files, Git state, and Argus activity.', icon: Code2, color: 'emerald' },
] as const;

export const WORKBENCH_MODULES = [
  { id: 'overview', zh: '项目概览', en: 'Project overview', icon: FolderKanban },
  ...WORKSPACE_DESTINATIONS,
] as const;
