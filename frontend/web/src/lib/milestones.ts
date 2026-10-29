import type { EventMsg } from '../api';
import type { MissionMilestone } from '../../../core/src/types';
import type { Locale } from '../i18n';
import { renderLine } from '../../../core/src/eventRender';

/** One milestone as the conversation says it: a plain line, the recorded reason, the files. */
export interface MilestoneLine {
  id: string;
  ts: number;
  kind: string;
  /** The task it belongs to; '' for a hold that names none. */
  itemId: string;
  tone: 'done' | 'running' | 'waiting' | 'failed' | 'idle';
  text: string;
  /** The Reviewer's or the runtime's own words, shown as recorded. */
  detail: string;
  /** Workspace-relative files the round wrote, newest last. */
  files: string[];
}

const IMAGE = /\.(png|jpe?g|gif|webp|svg)$/i;
// Argus's own records and tool caches, never the work; .argus/evidence holds run outputs.
const NOT_THE_WORK = new Set(['.agents', '.autors', '.argus_subagents', '.argus_external_work', '.git', '.venv', 'venv',
  'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache']);

export const isImageFile = (path: string) => IMAGE.test(path);

/** ``path`` as the workspace names it, or '' when it is outside the workspace or not the work. */
export function workFile(path: string, roots: readonly string[]): string {
  let relative = path.replace(/\\/g, '/').trim();
  if (relative.startsWith('/') || /^[A-Za-z]:\//.test(relative)) {
    const root = roots.map(value => value.replace(/\\/g, '/').replace(/\/+$/, '')).filter(Boolean)
      .find(value => relative.startsWith(`${value}/`));
    if (!root) return '';
    relative = relative.slice(root.length + 1);
  }
  relative = relative.replace(/^\.\//, '');
  const [first, second] = relative.split('/');
  if (!relative || NOT_THE_WORK.has(first) || (first === '.argus' && second !== 'evidence')) return '';
  return relative;
}

function reviewText(row: MissionMilestone, zh: boolean): { text: string; tone: MilestoneLine['tone'] } {
  const n = row.round ?? '';
  const status = String(row.status || '');
  if (String(row.source || '').startsWith('engineer')) {
    // The Engineer's own check or hand-over, not an independent review.
    const own: Record<string, [string, string, MilestoneLine['tone']]> = {
      done: [`工程师自查后认为第 ${n} 轮做完了`, `The Engineer checked round ${n} itself and called it done`, 'done'],
      manager: [`工程师把第 ${n} 轮交给经理定夺`, `The Engineer handed round ${n} to the Manager to decide`, 'waiting'],
      blocked: [`工程师把第 ${n} 轮交给经理定夺`, `The Engineer handed round ${n} to the Manager to decide`, 'waiting'],
      operator: [`工程师把第 ${n} 轮的选择交给你`, `The Engineer leaves a choice in round ${n} to you`, 'waiting'],
      planner: [`工程师请规划者重新安排（第 ${n} 轮）`, `The Engineer asked for a new plan (round ${n})`, 'waiting'],
    };
    const [chinese, english, tone] = own[status] ?? [`工程师接着再做一轮（第 ${n} 轮后）`, `The Engineer goes on after round ${n}`, 'idle'];
    return { text: zh ? chinese : english, tone };
  }
  const verdicts: Record<string, [string, string, MilestoneLine['tone']]> = {
    done: [`检查通过：第 ${n} 轮`, `Review accepted round ${n}`, 'done'],
    skipped: [`第 ${n} 轮没有经过检查`, `Round ${n} was not reviewed`, 'idle'],
    planner: [`检查后要求重新安排：第 ${n} 轮`, `Review asked for a new plan after round ${n}`, 'waiting'],
    operator: [`检查后需要你来决定：第 ${n} 轮`, `Review leaves a decision on round ${n} to you`, 'waiting'],
    manager: [`检查认为第 ${n} 轮卡住了`, `Review found round ${n} stuck`, 'failed'],
    blocked: [`检查认为第 ${n} 轮卡住了`, `Review found round ${n} stuck`, 'failed'],
    no_progress: [`检查认为第 ${n} 轮没有进展`, `Review saw no progress in round ${n}`, 'failed'],
  };
  const [chinese, english, tone] = verdicts[status] ?? [`检查退回：第 ${n} 轮需要再改`, `Review sent round ${n} back for another pass`, 'waiting'];
  return { text: zh ? chinese : english, tone };
}

/**
 * The team's recorded milestones as conversation lines, oldest first. A
 * milestone says what happened in plain words; a verdict's reason and an
 * error are the record's own words. A files row keeps only the work's files.
 */
export function milestoneLines(rows: readonly MissionMilestone[], locale: Locale, roots: readonly string[]): MilestoneLine[] {
  const zh = locale === 'zh-CN';
  const lines: MilestoneLine[] = [];
  for (const row of rows) {
    const base = { id: row.id, ts: Number(row.ts) || 0, kind: row.kind, itemId: row.item_id || '', detail: '', files: [] as string[] };
    const n = row.round ?? '';
    switch (row.kind) {
      case 'task_started':
        lines.push({ ...base, tone: 'running', text: zh ? `开始任务：${row.title || '新任务'}` : `Started: ${row.title || 'a new task'}` });
        break;
      case 'task_requeued': {
        const orphaned = String(row.error || '').startsWith('orphaned');
        lines.push({ ...base, tone: 'waiting',
          text: zh ? '上一次运行没有正常结束，任务重新开始' : 'The previous run did not finish; the task started again',
          detail: orphaned ? '' : String(row.error || '') });
        break;
      }
      case 'round_started':
        lines.push({ ...base, tone: 'running', text: zh ? `第 ${n} 轮开始` : `Round ${n} started` });
        break;
      case 'round_finished':
        lines.push({ ...base, tone: row.failed ? 'failed' : 'idle',
          text: row.failed ? zh ? `第 ${n} 轮没有正常结束` : `Round ${n} did not finish normally`
            : zh ? `第 ${n} 轮工作完成` : `Round ${n} of work finished` });
        break;
      case 'review': {
        const { text, tone } = reviewText(row, zh);
        lines.push({ ...base, tone, text, detail: String(row.reason || '') });
        break;
      }
      case 'files': {
        const files = [...new Set((row.paths ?? []).map(path => workFile(path, roots)).filter(Boolean))];
        if (!files.length) break;
        const shown = files.slice(-3).map(path => path.split('/').pop()).join(zh ? '、' : ', ');
        const more = files.length > 3 ? zh ? ` 等 ${files.length} 个文件` : ` and ${files.length - 3} more` : '';
        lines.push({ ...base, tone: 'idle', files,
          text: zh ? `第 ${n} 轮写入了 ${shown}${more}` : `Round ${n} wrote ${shown}${more}` });
        break;
      }
      case 'stopped': {
        // A hold or error reads as the feed says it.
        const event = { ...row, type: row.type || '' } as unknown as EventMsg;
        const said = renderLine(event, { locale, showReasoning: false, unknownEventPolicy: 'hide', density: 'compact' });
        if (said) lines.push({ ...base, tone: 'failed', text: said.text });
        break;
      }
      default:
        break;
    }
  }
  return lines;
}
