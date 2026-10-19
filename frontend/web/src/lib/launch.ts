import type { LaunchSettings, Snapshot } from '../../../core/src/types';

export const DEFAULT_LAUNCH_SETTINGS: LaunchSettings = {
  first_result_hours: null,
  target_level: 'auto',
  deliverable: 'any',
  hold: true,
};

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
    .filter((item) => ['pending', 'paused_operator'].includes(item.status))
    .map((item) => item.title || item.objective)
    .filter(Boolean)
    .slice(0, 8);
}
