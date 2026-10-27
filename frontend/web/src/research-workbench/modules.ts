import { CalendarClock, Code2, FlaskConical, SlidersHorizontal } from 'lucide-react';

// Shared research destinations and the workbench's maintenance page.
export const WORKSPACE_DESTINATIONS = [
  { id: 'timeline', zh: '研究排期', en: 'Research timeline', icon: CalendarClock },
  { id: 'experiments', zh: '任务进展', en: 'Work progress', icon: FlaskConical },
  { id: 'ide', zh: '代码工作区', en: 'Code workspace', icon: Code2 },
] as const;

export const WORKBENCH_MODULES = [
  ...WORKSPACE_DESTINATIONS,
  { id: 'runtime', zh: '运行控制', en: 'Runtime controls', icon: SlidersHorizontal },
] as const;
