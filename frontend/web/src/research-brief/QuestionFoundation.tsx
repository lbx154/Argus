import { useEffect, useState } from 'react';
import { useMutation, useMutationState, useQueryClient } from '@tanstack/react-query';
import { BookOpen, MessageCircle, Plus } from 'lucide-react';
import { api, type ArtifactInfo } from '../api';
import { Button, RawDisclosure } from '../components/primitives';
import { Modal, ModalHeader } from '../components/Modal';
import { useI18n } from '../i18n';
import { ReaderExplanationStatus } from './ReaderExplanation';
import { beginExplanationProgress, useExplanationProgress } from './progress';
import { foundationChoice, foundationListKey, foundationQuestionDraft, saveFoundationRequest, selectFoundation, useFoundationList, useFoundationRequest, useSelectedFoundation, type FoundationRequest } from './foundation';

interface QuestionDraft { question: string; sourceTaskId?: string; sourceTitle?: string }

/** One explicit question creates one saved explanation; opening a file only reads it. */
export function QuestionFoundation({ sid, objective, taskId, taskTitle, readOnly, onOpenArtifact, onAsk }: {
  sid: string; objective?: string; taskId?: string; taskTitle?: string; readOnly?: boolean;
  onOpenArtifact: (path: string) => void; onAsk: (draft: string) => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const language = zh ? 'zh-CN' : 'en-US';
  const text = (chinese: string, english: string) => zh ? chinese : english;
  const client = useQueryClient();
  const selected = useSelectedFoundation(sid, language);
  const list = useFoundationList(sid);
  const [draft, setDraft] = useState<QuestionDraft | null>(null);
  const pendingRequest = useFoundationRequest(sid, language);
  const progressKey = ['reader-foundation-create', sid, language];
  const activeRequests = useMutationState({ filters: { mutationKey: progressKey, status: 'pending' },
    select: mutation => (mutation.state.variables as { id?: string } | undefined)?.id });
  const progress = useExplanationProgress(progressKey, activeRequests.at(-1) ?? pendingRequest?.id);
  const generation = useMutation({
    mutationKey: progressKey,
    mutationFn: async (request: FoundationRequest) => {
      const observed = beginExplanationProgress(client, progressKey, [{ key: request.id }]);
      try {
        const result = await api.generateReaderFoundation(sid, {
          request_id: request.id, question: request.draft.question.trim(), locale: language,
          ...(request.draft.sourceTaskId ? { source_task_id: request.draft.sourceTaskId } : {}),
        }, observed.update);
        if (result.reader_foundation?.id !== request.id)
          throw new Error(text('说明请求的结果尚未确认。', 'The result of this explanation request is unconfirmed.'));
        return result;
      } finally {
        observed.finish();
        await client.invalidateQueries({ queryKey: foundationListKey(sid) });
      }
    },
    onSuccess: (artifact, request) => {
      // Completing an older request must not undo a question chosen meanwhile.
      if (artifact.exists && artifact.reader_foundation?.state === 'complete'
        && foundationChoice(client, sid, language).choice === request.beforeChoice)
        selectFoundation(client, sid, language, artifact.reader_foundation!.id);
      client.setQueryData<ArtifactInfo[]>(foundationListKey(sid), previous => {
        const saved = previous?.find(item => item.path === artifact.path);
        // A pending duplicate response may arrive after the refresh observed completion.
        if (saved && saved.reader_foundation?.state !== 'generating'
          && artifact.reader_foundation?.state === 'generating') return previous;
        return [...(previous ?? []).filter(item => item.path !== artifact.path), artifact];
      });
      void client.invalidateQueries({ queryKey: ['map-copy', 'project', sid] });
    },
    retry: false,
  });
  const notes = (list.data ?? []).filter(item => item.reader_foundation?.locale === language);
  const current = notes.find(item => item.reader_foundation?.id === selected.id);
  const complete = current?.exists && current.reader_foundation?.state === 'complete';
  const waiting = notes.some(item => item.reader_foundation?.state === 'generating');
  const overdue = notes.some(item => item.reader_foundation?.state === 'generating' && item.reader_foundation.deadline_exceeded);
  const failed = notes.filter(item => item.reader_foundation?.state === 'failed');
  const creating = generation.isPending || activeRequests.length > 0 || progress.active;
  const requestId = pendingRequest?.id ?? generation.variables?.id;
  const requested = notes.find(item => item.reader_foundation?.id === requestId);
  const confirmedFailure = requested?.reader_foundation?.state === 'failed';
  const confirmedComplete = requested?.reader_foundation?.state === 'complete';
  const unknownResult = !!pendingRequest && !requested && !creating;
  const creationBlocked = creating || waiting || !!pendingRequest || list.isPending || list.isError;
  useEffect(() => {
    if (pendingRequest && (confirmedFailure || confirmedComplete))
      saveFoundationRequest(client, sid, language, null);
  }, [client, sid, language, pendingRequest, confirmedFailure, confirmedComplete]);
  const submit = () => {
    if (!draft?.question.trim() || readOnly || creationBlocked) return;
    const id = crypto.randomUUID();
    const request = { id, draft, beforeChoice: foundationChoice(client, sid, language).choice };
    saveFoundationRequest(client, sid, language, request);
    generation.mutate(request);
    setDraft(null);
  };
  return <>
    <section className="mx-4 mt-3 rounded-lg border border-line/70 bg-panel px-3 py-2" aria-label={text('问题基础', 'Question foundations')} data-testid="question-foundation" data-project-id={sid}>
      <div className="flex flex-wrap items-center gap-2">
        <BookOpen size={15} className="text-blue-sky" />
        <h2 className="text-sm font-semibold">{text('问题基础', 'Question foundations')}</h2>
        <span className="hidden text-xs text-ink-faint sm:inline">{text('一份说明，陪你读后续进展', 'A saved explanation to help you follow later progress')}</span>
        {!readOnly ? <Button className="ml-auto inline-flex items-center gap-1 text-xs" disabled={creationBlocked}
          onClick={() => setDraft(generation.isError && generation.variables ? generation.variables.draft
            : { question: objective ?? '', sourceTaskId: taskId, sourceTitle: taskTitle })}>
          <Plus size={12} />{text('从基础理解', 'Understand the foundations')}
        </Button> : null}
      </div>
      {notes.length ? <div className="mt-2 flex flex-wrap items-center gap-2">
        <select className="min-w-0 flex-1 rounded border border-line bg-bg px-2 py-1.5 text-xs text-ink"
          aria-label={text('选择基础说明', 'Choose a foundation explanation')} value={selected.id ?? ''}
          onChange={event => selectFoundation(client, sid, language, event.target.value || null)}>
          <option value="">{text('选择你正在理解的问题', 'Choose the question you are studying')}</option>
          {notes.map(item => <option key={item.path} value={item.reader_foundation!.id}
            disabled={!item.exists || item.reader_foundation!.state !== 'complete'}>
            {item.reader_foundation!.question}{item.reader_foundation!.state === 'generating' ? text(' · 正在整理', ' · preparing')
              : !item.exists ? text(' · 尚未完成', ' · not completed') : ''}
          </option>)}
        </select>
        <Button className="text-xs" disabled={!complete} onClick={() => current && onOpenArtifact(current.path)}>{text('阅读基础说明', 'Read foundations')}</Button>
        {!readOnly && current ? <Button className="inline-flex items-center gap-1 text-xs" disabled={!complete}
          onClick={() => onAsk(foundationQuestionDraft(current.reader_foundation!.question, current.path, zh))}>
          <MessageCircle size={12} />{text('写下不懂的地方', 'Draft a question')}
        </Button> : null}
      </div> : !list.isPending && !creating ? <p className="mt-2 text-xs text-ink-dim">{text('先选一个想理解的问题；保存后，后续进度会引用同一份说明。', 'Start with a question you want to understand. Later progress can refer to the same saved explanation.')}</p> : null}
      {list.isError ? <div className="mt-2 flex items-center gap-2 text-xs text-ink-faint" role="status">
        <span>{text('已保存的基础说明暂时读取失败。', 'Saved explanations could not be loaded.')}</span>
        <Button className="text-xs" disabled={list.isFetching} onClick={() => void list.refetch()}>{text('重新读取', 'Reload records')}</Button>
      </div> : null}
      {creating ? <div className="mt-2">{progress.active ? <ReaderExplanationStatus generating phase={progress.phase} />
        : <span role="status" className="text-xs text-ink-faint">{text('正在核对保存结果', 'Checking the saved result')}</span>}</div> : waiting ? <p className="mt-2 text-xs text-ink-faint" role="status">{overdue
        ? text('等待时间已较长，仍未收到完整说明；生成尚未确认结束。', 'The wait is taking longer. A complete explanation has not arrived, and generation has not been confirmed to have stopped.')
        : text('基础说明仍在整理，保存后会出现在这里。', 'An explanation is still being prepared. It will appear here when saved.')}</p> : null}
      {confirmedFailure ? <p className="mt-2 text-xs text-ink-faint" role="status">{text('这次说明未完成，失败记录已保留。可以重新明确生成一份。', 'This explanation did not finish. The failed request is retained; you can explicitly generate a new one.')}</p> : unknownResult ? <p className="mt-2 text-xs text-ink-faint" role="status">{text('这次请求的结果尚未确认；刷新不会重新生成。', 'The result of this request is unconfirmed. Refreshing does not generate it again.')}</p> : null}
      {!readOnly && pendingRequest && !creating && !waiting ? <Button className="mt-2 text-xs" onClick={() => generation.mutate(pendingRequest)}>{text('重试同一请求', 'Retry this same request')}</Button> : null}
      {!readOnly && failed.length ? <RawDisclosure label={text(`未完成的说明（${failed.length}）`, `Unfinished explanations (${failed.length})`)}><div className="max-h-[24vh] overflow-y-auto">{failed.map(item => <div key={item.path} className="mt-2 flex items-center gap-2 text-xs text-ink-faint">
        <span className="min-w-0 flex-1 truncate" title={item.reader_foundation!.question}>{text('未完成：', 'Not completed: ')}{item.reader_foundation!.question}</span>
        <Button className="shrink-0 text-xs" disabled={creationBlocked} onClick={() => setDraft({
          question: item.reader_foundation!.question, sourceTaskId: item.reader_foundation!.source_task_id ?? undefined,
        })}>{text('重新编辑问题', 'Edit this question')}</Button>
      </div>)}</div></RawDisclosure> : null}
      <p className="mt-1.5 text-[11px] text-ink-faint">{text('背景说明单独保存，不计作研究进展。补充入口只准备草稿。', 'Background explanations are saved separately from research progress. The question button only prepares a draft.')}</p>
    </section>
    <Modal open={!!draft} onClose={() => setDraft(null)} label={text('从基础理解', 'Understand the foundations')}>
      <ModalHeader title={text('你想理解什么', 'What would you like to understand')} sub={text('用自己的话选定一个问题，说明会保存下来供以后阅读。', 'Choose a question in your own words. Its explanation will be saved for later reading.')} />
      <div className="px-6 pb-6">
        <textarea aria-label={text('想理解的问题', 'Question to understand')} className="min-h-40 w-full rounded-lg border border-line bg-bg p-3 text-sm leading-6 text-ink"
          maxLength={8000} value={draft?.question ?? ''} onChange={event => setDraft(previous => previous ? { ...previous, question: event.target.value } : previous)} />
        {draft?.sourceTitle ? <p className="mt-2 text-xs text-ink-faint">{text('从这项任务打开：', 'Opened from this task: ')}{draft.sourceTitle}</p> : null}
        <div className="mt-3 flex justify-end"><Button disabled={!draft?.question.trim() || creationBlocked || readOnly} onClick={submit}>{text('生成并保存基础说明', 'Generate and save foundations')}</Button></div>
      </div>
    </Modal>
  </>;
}
