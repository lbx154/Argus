import { Code2, SlidersHorizontal } from 'lucide-react';

// What the conversation, the map and the results do not show: the project's code as git has it,
// and the machine and the project's upkeep.
export const WORKBENCH_MODULES = [
  { id: 'ide', zh: '代码工作区', en: 'Code workspace', icon: Code2 },
  { id: 'runtime', zh: '运行控制', en: 'Runtime controls', icon: SlidersHorizontal },
] as const;
