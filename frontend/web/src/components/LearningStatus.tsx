import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Info, LoaderCircle, Sparkles } from 'lucide-react';
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
  const paused = !busy && job.status === 'skipped' && job.outcome.reason === 'paused';
  // Nothing to learn from this turn: there is no status worth showing. A policy pause is
  // explained by the chat reply itself; the libraries, where learning lives, say why too.
  if (!busy && job.status === 'skipped' && (!paused || !channel)) return null;
  const pauseReasons: Record<string, [string, string]> = {
    cost_unreconciled: ['上一次模型调用的费用尚未结算，结算前暂停新的模型调用', 'model calls are paused until an earlier call\'s cost is settled'],
    budget_exhausted: ['今日模型预算已用完', 'today\'s model budget is used up'],
    provider_cooldown: ['模型服务暂时限流', 'the model provider is briefly rate-limited'],
    operator_pause: ['模型调用已暂停', 'model calls are paused'],
  };
  const pauseReason = pauseReasons[job.outcome.detail ?? ''] ?? pauseReasons.operator_pause;
  const settled = paused || (!busy && job.status === 'failed');
  const names = zh ? { knowledge: '知识库', skills: '技能库', preferences: '用户偏好' }
    : { knowledge: 'Knowledge base', skills: 'Skill library', preferences: 'Preferences' };
  const channels: LearningChannel[] = channel ? [channel] : ['knowledge', 'skills', 'preferences'];
  const message = (kind: LearningChannel) => {
    const count = job.outcome.counts?.[kind] ?? 0;
    if (busy) return job.status === 'running'
      ? (zh ? `${names[kind]}正在更新` : `Updating ${names[kind].toLowerCase()}`)
      : (zh ? `${names[kind]}等待更新` : `${names[kind]} update queued`);
    return count ? (zh ? `${names[kind]}已更新 · ${count} 条` : `${names[kind]} updated · ${count}`)
      : (zh ? `${names[kind]}已检查，无新增` : `${names[kind]} checked, no additions`);
  };
  const items = (job.outcome.items ?? []).filter(item => !channel || item.channel === channel);
  return <div className="px-2 py-1.5 text-[11px] leading-5 text-ink-dim" data-learning-status={job.status}>
    <div role="status" aria-live="polite" className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
      {busy ? <LoaderCircle className="h-3 w-3 animate-spin text-blue" />
        : settled ? <Info className="h-3 w-3 text-ink-faint" /> : <Sparkles className="h-3 w-3 text-blue" />}
      {paused && <span>{zh ? `本轮未学习：${pauseReason[0]}。回复不受影响。` : `Learning skipped: ${pauseReason[1]}. Your reply is unaffected.`}</span>}
      {!busy && job.status === 'failed' && <span>{zh ? '本轮学习未完成，回复不受影响。'
        : 'Learning from this turn did not finish. Your reply is unaffected.'}</span>}
      {!settled && channels.map(kind => <span key={kind}>{message(kind)}</span>)}
      {query.data.pending > 1 && <span>{zh ? `还有 ${query.data.pending - 1} 轮待学习` : `${query.data.pending - 1} more queued`}</span>}
      {settled && job.retryable !== false && <button type="button" disabled={retry.isPending}
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
