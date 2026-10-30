import { useState } from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faPlay, faStop } from '@fortawesome/free-solid-svg-icons';
import type { Snapshot } from '../../../core/src/types';
import { useI18n } from '../i18n';
import { roleLabel } from '../lib/enumLabels';
import type { WorkStatus } from '../lib/workStatus';
import { Modal } from './Modal';

/** The four states a person asks about: is the team working, waiting on me, paused, or done? */
export type TeamPhase = 'running' | 'waiting_you' | 'paused' | 'stopped' | 'done' | 'unknown';

export function teamPhase(status: WorkStatus): TeamPhase {
  if (status.reason === 'operator_input') return 'waiting_you';
  if (status.state === 'running' || status.state === 'waiting') return 'running';
  if (status.state === 'paused') return 'paused';
  if (status.state === 'idle') return 'done';
  return 'unknown';
}

/** The status dot's tone for a phase: one rule for every status line. */
export const PHASE_TONE: Record<TeamPhase, string> = {
  running: 'running', waiting_you: 'waiting', paused: 'idle', stopped: 'failed', done: 'done', unknown: 'idle',
};

/**
 * The one way to stop the work, and to go on with what stopped short. Stop shows while anything
 * is under way, the Manager's reply to a message sent (`pending`) or the team's work, and says
 * first what it interrupts and what it keeps; Resume shows when the server says there is work to
 * take up.
 */
export function RunControl({ snapshot, pending = false, busy = false, onStop, onResume, className = '', resumeClassName = className }: {
  snapshot: Snapshot; pending?: boolean; busy?: boolean; onStop: () => void; onResume: () => void;
  className?: string; resumeClassName?: string;
}) {
  const { t, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const [asking, setAsking] = useState(false);
  if (!snapshot.daemon.alive && !pending) {
    return snapshot.daemon.resumable ? <button type="button" data-resume-work disabled={busy} onClick={onResume} className={resumeClassName}
      title={t('topbar.continueRunHint')}>
      <FontAwesomeIcon icon={faPlay} className="h-3 w-3" /><span>{t('topbar.continueRun')}</span>
    </button> : null;
  }
  const title = (id: string) => snapshot.backlog.find(item => item.id === id)?.title || id;
  const active = snapshot.roles.filter(role => role.active);
  const affected = [
    ...pending || active.some(role => role.role === 'manager') ? [zh ? '经理正在写的回复' : 'The reply the Manager is writing'] : [],
    ...active.filter(role => role.role !== 'manager').flatMap(({ role, tasks = [] }) => {
      const name = roleLabel(role, t);
      return tasks.length
        ? tasks.map(task => zh ? `${name}在「${title(task)}」上的工作` : `The ${name}'s work on “${title(task)}”`)
        : [zh ? `${name}正在做的工作` : `The ${name}'s work under way`];
    }),
  ];
  return <>
    <button type="button" data-stop-work disabled={busy} onClick={() => setAsking(true)} className={className}
      aria-label={zh ? '停止' : 'Stop'} title={zh ? '停止现在的工作' : 'Stop the work under way'}>
      <FontAwesomeIcon icon={faStop} className="h-3 w-3" /><span>{zh ? '停止' : 'Stop'}</span>
    </button>
    <Modal open={asking} onClose={() => setAsking(false)} label={zh ? '停止现在的工作' : 'Stop the work under way'} width="max-w-md" align="bottom">
      <div className="modal-header"><h2>{zh ? '停止现在的工作？' : 'Stop the work under way?'}</h2></div>
      <div className="px-6 pb-6 text-sm leading-6 text-ink-dim" data-stop-explains>
        <p>{zh ? '会立刻中断：' : 'This interrupts at once:'}</p>
        <ul className="mt-1 list-disc pl-5 text-ink">
          {(affected.length ? affected : [zh ? '团队接下来要做的工作' : 'The work the team would do next']).map(line => <li key={line}>{line}</li>)}
        </ul>
        <p className="mt-3">{zh
          ? `已经写好的文件和记录都会保留。停着时发的消息和回答照样送到，团队在你点「${t('topbar.continueRun')}」后接着做。`
          : `Files and records already written are kept. Messages and answers sent while stopped still arrive; the team takes them up when you click ${t('topbar.continueRun')}.`}</p>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" className="brand-button brand-button-ghost" onClick={() => setAsking(false)}>{t('common.cancel')}</button>
          <button type="button" className="brand-button brand-button-danger" data-stop-confirm onClick={() => { setAsking(false); onStop(); }}>
            {zh ? '停止' : 'Stop'}
          </button>
        </div>
      </div>
    </Modal>
  </>;
}
