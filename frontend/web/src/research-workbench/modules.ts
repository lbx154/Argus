import { Code2, FlaskConical, SlidersHorizontal } from 'lucide-react';

// The research destinations and the workbench's maintenance page.
export const WORKBENCH_MODULES = [
  { id: 'experiments', zh: '任务进展', en: 'Work progress', icon: FlaskConical },
  { id: 'ide', zh: '代码工作区', en: 'Code workspace', icon: Code2 },
  { id: 'runtime', zh: '运行控制', en: 'Runtime controls', icon: SlidersHorizontal },
] as const;
