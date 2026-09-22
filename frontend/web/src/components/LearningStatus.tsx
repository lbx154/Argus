import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LoaderCircle, Sparkles } from 'lucide-react';
import { api, type LearningChannel } from '../api';
import { useI18n } from '../i18n';

/** Poll independently of foreground work: learning continues after the reply. */
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
  const retry = useMutation({ mutationFn: (request: { sid: string; id: string }) => api.retryLearning(request.sid, request.id),
    onSuccess: (data, request) => client.setQueryData(['learning-status', request.sid], data) });
  if (!sid || !query.data?.jobs.length) return null;
  const jobs = query.data.jobs;
  const active = jobs.find(job => job.status === 'running') ?? jobs.find(job => job.status === 'queued');
  const job = active ?? jobs[0];
  const busy = Boolean(active) || query.data.pending > 0;
  const names = zh ? { knowledge: '知识库', skills: '技能库', preferences: '用户偏好' }
    : { knowledge: 'Knowledge base', skills: 'Skill library', preferences: 'Preferences' };
  const channels: LearningChannel[] = channel ? [channel] : ['knowledge', 'skills', 'preferences'];
  const message = (kind: LearningChannel) => {
    const count = job.outcome.counts?.[kind] ?? 0;
    if (busy) return job.status === 'running'
      ? (zh ? `${names[kind]}正在更新` : `Updating ${names[kind].toLowerCase()}`)
      : (zh ? `${names[kind]}等待更新` : `${names[kind]} update queued`);
    if (job.status === 'failed') return zh ? `${names[kind]}更新未完成` : `${names[kind]} update incomplete`;
    return count ? (zh ? `${names[kind]}已更新 · ${count} 条` : `${names[kind]} updated · ${count}`)
      : (zh ? `${names[kind]}已检查，无新增` : `${names[kind]} checked, no additions`);
  };
  const items = (job.outcome.items ?? []).filter(item => !channel || item.channel === channel);
  return <div className="px-2 py-1.5 text-[11px] leading-5 text-ink-dim" data-learning-status={job.status}>
    <div role="status" aria-live="polite" className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
      {busy ? <LoaderCircle className="h-3 w-3 animate-spin text-blue" /> : <Sparkles className="h-3 w-3 text-blue" />}
      {channels.map(kind => <span key={kind}>{message(kind)}</span>)}
      {query.data.pending > 1 && <span>{zh ? `还有 ${query.data.pending - 1} 轮待学习` : `${query.data.pending - 1} more queued`}</span>}
      {!busy && job.status === 'failed' && job.retryable !== false && <button type="button" disabled={retry.isPending}
        className="text-blue underline" onClick={() => retry.mutate({ sid, id: job.id })}>{zh ? '重试学习' : 'Retry learning'}</button>}
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
    {(retry.isError || query.isError) && <p role="alert">{zh ? '暂时无法同步学习状态，请稍后重试。' : 'Learning status could not be refreshed. Please retry.'}</p>}
  </div>;
}
