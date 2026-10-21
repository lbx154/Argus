import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Info, LoaderCircle, Sparkles } from 'lucide-react';
import { api, type LearningChannel } from '../api';
import { useI18n } from '../i18n';
import { stopClause } from '../../../core/src/missionOutcome';

/** Poll independently of foreground work: reflection runs after a mission ends. */
export function LearningStatus({ sid, channel, onOpenKnowledge, onOpenSkills }: {
  sid: string | null;
  channel?: LearningChannel;
  onOpenKnowledge?: () => void;
  onOpenSkills?: () => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const client = useQueryClient();
  const key = ['learning-status', sid];
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) => api.learningStatus(sid!, signal),
    enabled: Boolean(sid), refetchInterval: 2_000, staleTime: 1_000 });
  const revision = useRef('');
  useEffect(() => {
    const current = `${sid}:${query.data?.revision ?? 0}`;
    if (!query.data?.revision || current === revision.current) return;
    revision.current = current;
    // Refresh the actual pages as soon as the learning receipt changes.
    for (const name of ['skill-library', 'wiki-library', 'knowledge-feed']) {
      void client.invalidateQueries({ queryKey: [name] });
    }
  }, [sid, query.data?.revision, client]);
  if (!sid || !query.data?.jobs.length) return null;
  const jobs = query.data.jobs;
  const active = jobs.find(job => job.status === 'running');
  const job = active ?? jobs[0];
  const busy = Boolean(active) || query.data.pending > 0;
  const paused = !busy && job.status === 'skipped' && job.outcome.reason === 'paused';
  // Nothing to learn from this turn: there is no status worth showing. A policy pause is
  // explained by the chat reply itself; the libraries, where learning lives, say why too.
  if (!busy && job.status === 'skipped' && (!paused || !channel)) return null;
  const pauseReason = stopClause(job.outcome.detail, zh) || stopClause('operator_pause', zh);
  const settled = paused || (!busy && job.status === 'failed');
  const names = zh ? { knowledge: '知识库', skills: '技能库', preferences: '用户偏好' }
    : { knowledge: 'Knowledge base', skills: 'Skill library', preferences: 'Preferences' };
  const allChannels: LearningChannel[] = channel ? [channel] : ['knowledge', 'skills', 'preferences'];
  // A finished check that added nothing is noise for the operator; only what
  // changed (or is still being worked on) is shown. The event log keeps the rest.
  const channels = busy ? allChannels : allChannels.filter(kind => (job.outcome.counts?.[kind] ?? 0) > 0);
  if (!busy && !settled && !channels.length) return null;
  const message = (kind: LearningChannel) => {
    const count = job.outcome.counts?.[kind] ?? 0;
    if (busy) return zh ? `${names[kind]}正在更新` : `Updating ${names[kind].toLowerCase()}`;
    return zh ? `${names[kind]}已更新 · ${count} 条` : `${names[kind]} updated · ${count}`;
  };
  const items = (job.outcome.items ?? []).filter(item => !channel || item.channel === channel);
  return <div className="px-2 py-1.5 text-[11px] leading-5 text-ink-dim" data-learning-status={job.status}>
    <div role="status" aria-live="polite" className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
      {busy ? <LoaderCircle className="h-3 w-3 animate-spin text-blue" />
        : settled ? <Info className="h-3 w-3 text-ink-faint" /> : <Sparkles className="h-3 w-3 text-blue" />}
      {paused && <span>{zh ? `本轮未学习：${pauseReason}。回复不受影响。` : `Learning skipped: ${pauseReason}. Your reply is unaffected.`}</span>}
      {!busy && job.status === 'failed' && <span>{zh ? '本轮学习未完成，回复不受影响。'
        : 'Learning from this turn did not finish. Your reply is unaffected.'}</span>}
      {!settled && channels.map(kind => <span key={kind}>{message(kind)}</span>)}
      {query.data.pending > 1 && <span>{zh ? `还有 ${query.data.pending - 1} 轮待学习` : `${query.data.pending - 1} more queued`}</span>}
    </div>
    {busy && !channel && <p className="text-ink-faint">{zh ? '正在整理本轮的知识、方法和偏好，你可以继续工作。' : 'Saving useful knowledge, methods and preferences. You can keep working.'}</p>}
    {items.length > 0 && !channel && <details className="mt-0.5">
      <summary className="cursor-pointer text-blue">{zh ? '查看本轮学到了什么' : 'What this turn taught Argus'}</summary>
      <ul className="pl-4 list-disc">{items.map((item, index) => <li key={index}>
        {(item.channel === 'skills' ? onOpenSkills : onOpenKnowledge)
          ? <button type="button" className="text-left underline" onClick={item.channel === 'skills' ? onOpenSkills : onOpenKnowledge}>{item.title}</button>
          : item.title}
      </li>)}</ul>
    </details>}
    {query.isError && <p role="alert">{zh ? '暂时无法同步学习状态，请稍后重试。' : 'Learning status could not be refreshed. Please retry.'}</p>}
  </div>;
}
