import type { Snapshot } from '../../../core/src/types';
import type { Locale } from '../i18n';
import type { WorkStatus } from '../lib/workStatus';

/** The four states a person asks about: is the team working, waiting on me, paused, or done? */
export type TeamPhase = 'running' | 'waiting_you' | 'paused' | 'stopped' | 'done' | 'unknown';

export function teamPhase(status: WorkStatus, snapshot?: Snapshot): TeamPhase {
  if (status.reason === 'operator_input' || status.reason === 'launch_confirm') return 'waiting_you';
  if (snapshot?.daemon.alive && snapshot.daemon.draining) return 'running';
  if (status.state === 'running' || status.state === 'waiting') return 'running';
  if (status.state === 'paused') return 'paused';
  // A step that did not finish is not "done": the phase and the sentence
  // beside it must say the same thing (rule 6).
  if (status.reason === 'task_failed') return 'stopped';
  if (status.state === 'step_finished' || status.state === 'idle') return 'done';
  return 'unknown';
}

/** The status dot's tone for a phase: one rule for every status line. */
export const PHASE_TONE: Record<TeamPhase, string> = {
  running: 'running', waiting_you: 'waiting', paused: 'idle', stopped: 'failed', done: 'done', unknown: 'idle',
};

export function teamPhaseLabel(phase: TeamPhase, locale: Locale): string {
  const zh = locale === 'zh-CN';
  const labels: Record<TeamPhase, [string, string]> = {
    running: ['运行中', 'Running'],
    waiting_you: ['等你回复', 'Waiting for you'],
    paused: ['已暂停', 'Paused'],
    stopped: ['已停止', 'Stopped'],
    done: ['已完成', 'Done'],
    unknown: ['状态未知', 'Status unknown'],
  };
  return labels[phase][zh ? 0 : 1];
}

const QUEUED = ['pending', 'queued', 'running', 'in_progress', 'claimed', 'paused'];

/** "暂停团队" / "继续": pauses or resumes the background team, never the Manager's reply. */
export function TeamPauseButton({ snapshot, locale, onPause, onResume, busy = false, className = '' }: {
  snapshot: Snapshot; locale: Locale; onPause?: () => void; onResume?: () => void; busy?: boolean; className?: string;
}) {
  const zh = locale === 'zh-CN';
  const daemon = snapshot.daemon;
  if (daemon.alive && daemon.control_available === false) return null;
  if (daemon.alive && daemon.draining) {
    return <button type="button" disabled className={className} data-testid="team-pause">{zh ? '正在收尾，随后暂停' : 'Finishing, then pausing'}</button>;
  }
  if (daemon.alive && onPause) {
    return <button type="button" disabled={busy} onClick={onPause} className={className} data-testid="team-pause"
      title={zh ? '让后台团队做完当前任务后暂停，不会打断它。经理的回复不受影响。' : 'The background team finishes its current task, then pauses. Manager replies are unaffected.'}>
      {zh ? '暂停团队' : 'Pause team'}
    </button>;
  }
  const queued = snapshot.backlog.some((item) => QUEUED.includes(item.status));
  if (!daemon.alive && queued && onResume) {
    return <button type="button" disabled={busy} onClick={onResume} className={className} data-testid="team-resume"
      title={zh ? '让后台团队继续未完成的任务。' : 'Let the background team continue its unfinished tasks.'}>
      {zh ? '继续' : 'Resume'}
    </button>;
  }
  return null;
}
