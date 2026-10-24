import type { LaunchSettings, Snapshot } from '../../../core/src/types';

export const DEFAULT_LAUNCH_SETTINGS: LaunchSettings = {
  first_result_hours: null,
  target_level: 'auto',
  deliverable: 'any',
  hold: false,
};

/** "什么时候问我": when Argus stops for the person before team work starts. */
export type AskMode = 'every_task' | 'first_task' | 'beyond_authority';
// Product decision: stage rulings go to the Manager unless the person opts in.
export const DEFAULT_ASK_MODE: AskMode = 'beyond_authority';
export const ASK_MODES: AskMode[] = ['every_task', 'first_task', 'beyond_authority'];

export function askModeLabel(mode: AskMode, zh: boolean): string {
  return {
    every_task: zh ? '每个新任务都先给我看计划' : 'Show me the plan for every new task',
    first_task: zh ? '只在第一次确认' : 'Only confirm the first task',
    beyond_authority: zh ? '只有超出我授权的事才问我' : 'Only ask about things beyond what I authorised',
  }[mode];
}

/** The launch settings a message carries: the first task gets all of them; later
 * tasks only carry the plan hold, and only when the person asked for it every time. */
export function launchForMessage(settings: LaunchSettings, ask: AskMode, fresh: boolean): LaunchSettings | undefined {
  if (fresh) return { ...settings, hold: ask !== 'beyond_authority' };
  return ask === 'every_task' ? { ...DEFAULT_LAUNCH_SETTINGS, hold: true } : undefined;
}

const askKey = (sid: string) => `argus.askMode.${sid}`;
export function readAskMode(sid: string | null | undefined): AskMode {
  if (!sid) return DEFAULT_ASK_MODE;
  try {
    const stored = window.localStorage.getItem(askKey(sid)) as AskMode | null;
    return stored && ASK_MODES.includes(stored) ? stored : DEFAULT_ASK_MODE;
  } catch { return DEFAULT_ASK_MODE; }
}
export function writeAskMode(sid: string | null | undefined, mode: AskMode): void {
  if (!sid) return;
  try { window.localStorage.setItem(askKey(sid), mode); } catch { /* per-viewer convenience only */ }
}

const NEVER_RAN = new Set(['skipped', 'cancelled']);

/** Launch settings belong to a project's first task: nothing queued, run or held yet.
 * Work withdrawn before it started (skipped) does not count, so a rewritten
 * request gets the same settings and confirmation. */
export function isFreshProject(snapshot: Pick<Snapshot, 'backlog' | 'daemon' | 'continuous' | 'launch_hold'> | null | undefined): boolean {
  if (!snapshot) return false;
  return snapshot.backlog.every((item) => NEVER_RAN.has(item.status))
    && !snapshot.daemon.alive
    && !snapshot.continuous?.enabled
    && !snapshot.launch_hold;
}

/** The request as the operator typed it, without the appended launch settings. */
export function stripLaunchNote(text: string): string {
  const cut = [text.indexOf('【开工设置'), text.indexOf('[Launch settings')].filter((index) => index >= 0);
  return (cut.length ? text.slice(0, Math.min(...cut)) : text).trim();
}

const LEVEL_TEXT: Record<string, [string, string]> = {
  exploratory: ['探索性（有限、可验证的调查）', 'exploratory (finite, verifiable investigation)'],
  publishable: ['可发表（原创研究，通常耗时较长）', 'publishable (original research, usually long)'],
  doctoral: ['博士论文级', 'doctoral'],
};

/** One recorded requirement in plain words; Manager-owned ones are marked as Argus's judgment. */
export function describeRequirement(requirement: { text: string; authority: string }, zh: boolean): string {
  const judged = requirement.authority === 'manager' ? (zh ? '（Argus 的判断，可调整）' : ' (Argus’s judgment, adjustable)') : '';
  const level = /^research target level:\s*(\S+)/i.exec(requirement.text);
  if (level) {
    const words = LEVEL_TEXT[level[1].toLowerCase()];
    return `${zh ? '目标级别：' : 'Target level: '}${words ? words[zh ? 0 : 1] : level[1]}${judged}`;
  }
  const venue = /^target venue:\s*(.+)$/i.exec(requirement.text);
  if (venue) return `${zh ? '目标会议/期刊：' : 'Target venue: '}${venue[1]}${judged}`;
  return `${requirement.text}${judged}`;
}

export function launchSettingsSummary(settings: Partial<LaunchSettings> | undefined, zh: boolean): string {
  if (!settings) return '';
  const parts: string[] = [];
  const hours = settings.first_result_hours;
  if (hours) {
    parts.push(zh
      ? `首个结果 ${hours >= 24 && hours % 24 === 0 ? `${hours / 24} 天` : `${hours} 小时`}内`
      : `first result within ${hours >= 24 && hours % 24 === 0 ? `${hours / 24} day(s)` : `${hours} h`}`);
  }
  if (settings.target_level === 'exploratory') parts.push(zh ? '探索性' : 'exploratory');
  if (settings.target_level === 'publishable') parts.push(zh ? '冲刺可发表' : 'aim to publish');
  const deliverable = settings.deliverable && settings.deliverable !== 'any' ? settings.deliverable : '';
  if (deliverable) {
    const names: Record<string, [string, string]> = {
      report: ['报告', 'report'], code: ['代码', 'code'], paper: ['论文稿', 'paper draft'], data: ['数据/结果表', 'data tables'],
    };
    parts.push(names[deliverable]?.[zh ? 0 : 1] ?? deliverable);
  }
  return parts.join(' · ');
}

export function launchHoldPlan(backlog: Snapshot['backlog']): string[] {
  return backlog
    .filter((item) => ['pending', 'parked'].includes(item.status))
    .map((item) => item.title || item.objective)
    .filter(Boolean)
    .slice(0, 8);
}
