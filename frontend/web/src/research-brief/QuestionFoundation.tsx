import { useEffect, useState } from 'react';
import { useMutation, useMutationState, useQueryClient } from '@tanstack/react-query';
import { BookOpen, MessageCircle, Plus } from 'lucide-react';
import { ApiError } from '../../../core/src/http';
import { api, type ArtifactInfo } from '../api';
import { Button, RawDisclosure } from '../components/primitives';
import { Modal, ModalHeader } from '../components/Modal';
import { useI18n } from '../i18n';
import { readerFoundationTitle } from '../lib/artifactPresentation';
import { ReaderExplanationStatus } from './ReaderExplanation';
import { beginExplanationProgress, useExplanationProgress } from './progress';
import { foundationChoice, foundationListKey, saveFoundationRequest, selectFoundation, useFoundationList, useFoundationRequest, useSelectedFoundation, type FoundationDraft, type FoundationRequest } from './foundation';

/** One explicit question creates one saved explanation; opening a file only reads it. */
export function QuestionFoundation({ sid, objective, taskId, taskTitle, readOnly, onOpenArtifact }: {
  sid: string; objective?: string; taskId?: string; taskTitle?: string; readOnly?: boolean;
  onOpenArtifact: (path: string) => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const language = zh ? 'zh-CN' : 'en-US';
  const text = (chinese: string, english: string) => zh ? chinese : english;
  const client = useQueryClient();
  const selected = useSelectedFoundation(sid, language);
  const list = useFoundationList(sid);
  const [draft, setDraft] = useState<FoundationDraft | null>(null);
  useEffect(() => setDraft(null), [sid, language]);
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
        const body = { request_id: request.id, question: request.draft.question.trim(), locale: language } satisfies Parameters<typeof api.askReaderFoundation>[2];
        const result = request.draft.parentId
          ? await api.askReaderFoundation(sid, request.draft.parentId, body, observed.update)
          : await api.generateReaderFoundation(sid, {
            ...body, ...(request.draft.sourceTaskId ? { source_task_id: request.draft.sourceTaskId } : {}),
          }, observed.update);
        if (result.reader_foundation?.id !== request.id)
          throw new Error(text('说明请求的结果尚未确认。', 'The result of this explanation request is unconfirmed.'));
        if (request.draft.parentId && (result.reader_foundation.kind !== 'clarification'
          || result.reader_foundation.parent_id !== request.draft.parentId))
          throw new Error(text('追问与来源说明的对应关系尚未确认。', 'The question’s connection to its source is unconfirmed.'));
        return result;
      } finally {
        observed.finish();
        await client.invalidateQueries({ queryKey: foundationListKey(sid) });
      }
    },
    onSuccess: (artifact, request) => {
      // Completing an older request must not undo a question chosen meanwhile.
      if (!request.draft.parentId && artifact.exists && artifact.reader_foundation?.state === 'complete'
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
    onError: (error, request) => {
      // Only this server rejection establishes that no request was reserved.
      // Network failures and ordinary generation errors remain unconfirmed.
      if (error instanceof ApiError && error.code === 'reader_source_unavailable')
        saveFoundationRequest(client, sid, language, { ...request, rejected: 'reader_source_unavailable' });
    },
    retry: false,
  });
  const notes = (list.data ?? []).filter(item => item.reader_foundation?.locale === language);
  const foundations = notes.filter(item => item.reader_foundation?.kind !== 'clarification' && !item.reader_foundation?.parent_id);
  const current = foundations.find(item => item.reader_foundation?.id === selected.id);
  const answers = current ? notes.filter(item => item.reader_foundation?.kind === 'clarification'
    && item.reader_foundation.root_id === current.reader_foundation?.id).reverse() : [];
  const complete = current?.exists && current.reader_foundation?.state === 'complete';
  const waiting = notes.some(item => item.reader_foundation?.state === 'generating');
  const overdue = notes.some(item => item.reader_foundation?.state === 'generating' && item.reader_foundation.deadline_exceeded);
  const failed = notes.filter(item => item.reader_foundation?.state === 'failed');
  const availableParents = notes.filter(item => item.exists && item.reader_foundation?.state === 'complete');
  const creating = generation.isPending || activeRequests.length > 0 || progress.active;
  const requestId = pendingRequest?.id ?? generation.variables?.id;
  const requested = notes.find(item => item.reader_foundation?.id === requestId);
  const confirmedFailure = requested?.reader_foundation?.state === 'failed';
  const confirmedComplete = requested?.reader_foundation?.state === 'complete';
  const requestRejected = pendingRequest?.rejected === 'reader_source_unavailable';
  const unknownResult = !!pendingRequest && !requestRejected && !requested && !creating;
  const creationBlocked = creating || waiting || (!!pendingRequest && !requestRejected) || list.isPending || list.isError;
  useEffect(() => {
    if (pendingRequest && (confirmedFailure || confirmedComplete))
      saveFoundationRequest(client, sid, language, null);
  }, [client, sid, language, pendingRequest, confirmedFailure, confirmedComplete]);
  const submit = () => {
    if (!draft?.question.trim() || readOnly || creationBlocked) return;
    if (draft.parentId && !availableParents.some(item => item.reader_foundation?.id === draft.parentId)) return;
    const id = crypto.randomUUID();
    const request = { id, draft, beforeChoice: foundationChoice(client, sid, language).choice };
    saveFoundationRequest(client, sid, language, request);
    generation.mutate(request);
    setDraft(null);
  };
  const ask = (artifact: ArtifactInfo) => {
    if (readOnly || creationBlocked || !artifact.exists || artifact.reader_foundation?.state !== 'complete') return;
    setDraft({ question: '', parentId: artifact.reader_foundation.id, parentTitle: readerFoundationTitle(artifact.reader_foundation) });
  };
  return <>
    <section className="mx-4 mt-3 rounded-lg border border-line/70 bg-panel px-3 py-2" aria-label={text('问题基础', 'Question foundations')} data-testid="question-foundation" data-project-id={sid}>
      <div className="flex flex-wrap items-center gap-2">
        <BookOpen size={15} className="text-blue-sky" />
        <h2 className="text-sm font-semibold">{text('问题基础', 'Question foundations')}</h2>
        <span className="hidden text-xs text-ink-faint sm:inline">{text('一份说明，陪你读后续进展', 'A saved explanation to help you follow later progress')}</span>
        {!readOnly ? <Button className="ml-auto inline-flex items-center gap-1 text-xs" disabled={creationBlocked}
          onClick={() => setDraft({ question: objective ?? '', sourceTaskId: taskId, sourceTitle: taskTitle })}>
          <Plus size={12} />{text('从基础理解', 'Understand the foundations')}
        </Button> : null}
      </div>
      {foundations.length ? <div className="mt-2 flex flex-wrap items-center gap-2">
        <select className="min-w-0 flex-1 rounded border border-line bg-bg px-2 py-1.5 text-xs text-ink"
          aria-label={text('选择基础说明', 'Choose a foundation explanation')} value={selected.id ?? ''}
          onChange={event => selectFoundation(client, sid, language, event.target.value || null)}>
          <option value="">{text('选择你正在理解的问题', 'Choose the question you are studying')}</option>
          {foundations.map(item => <option key={item.path} value={item.reader_foundation!.id} title={item.reader_foundation!.question}
            disabled={!item.exists || item.reader_foundation!.state !== 'complete'}>
            {readerFoundationTitle(item.reader_foundation!)}{item.reader_foundation!.state === 'generating' ? text(' · 正在整理', ' · preparing')
              : !item.exists ? text(' · 尚未完成', ' · not completed') : ''}
          </option>)}
        </select>
        <Button className="text-xs" disabled={!complete} onClick={() => current && onOpenArtifact(current.path)}>{text('阅读基础说明', 'Read foundations')}</Button>
        {!readOnly && current ? <Button className="inline-flex items-center gap-1 text-xs" disabled={!complete || creationBlocked}
          onClick={() => ask(current)}>
          <MessageCircle size={12} />{text('问这份说明', 'Ask about these foundations')}
        </Button> : null}
      </div> : !list.isPending && !creating ? <p className="mt-2 text-xs text-ink-dim">{text('先选一个想理解的问题；保存后，后续进度会引用同一份说明。', 'Start with a question you want to understand. Later progress can refer to the same saved explanation.')}</p> : null}
      {answers.length ? <div className="mt-3 border-t border-line/60 pt-2" data-testid="foundation-questions" data-foundation-id={current?.reader_foundation?.id}>
        <h3 className="text-xs font-medium text-ink">{text('围绕这份说明的问答', 'Questions about these foundations')}</h3>
        <ul className="mt-1 max-h-48 space-y-2 overflow-y-auto scroll-thin">{answers.map(item => <li key={item.path} className="flex items-start gap-2 text-xs" data-reading-question-id={item.reader_foundation!.id}>
          <div className="min-w-0 flex-1">
            <p className="whitespace-pre-wrap break-words text-ink-dim">{item.reader_foundation!.question}</p>
            <p className="mt-0.5 text-[11px] text-ink-faint">{item.reader_foundation!.state === 'generating'
              ? text('正在回答', 'Preparing an answer') : item.reader_foundation!.state === 'failed' || !item.exists
                ? text('这次回答未完成', 'This answer did not finish') : text('回答已保存', 'Answer saved')}</p>
          </div>
          {item.exists && item.reader_foundation!.state === 'complete' ? <div className="flex shrink-0 flex-wrap gap-1">
            <Button className="text-xs" onClick={() => onOpenArtifact(item.path)}>{text('阅读回答', 'Read answer')}</Button>
            {!readOnly ? <Button className="text-xs" disabled={creationBlocked} onClick={() => ask(item)}>{text('继续问', 'Ask a follow-up')}</Button> : null}
          </div> : null}
        </li>)}</ul>
      </div> : null}
      {list.isError ? <div className="mt-2 flex items-center gap-2 text-xs text-ink-faint" role="status">
        <span>{text('已保存的基础说明暂时读取失败。', 'Saved explanations could not be loaded.')}</span>
        <Button className="text-xs" disabled={list.isFetching} onClick={() => void list.refetch()}>{text('重新读取', 'Reload records')}</Button>
      </div> : null}
      {creating ? <div className="mt-2">{progress.active ? <ReaderExplanationStatus generating phase={progress.phase} />
        : <span role="status" className="text-xs text-ink-faint">{text('正在核对保存结果', 'Checking the saved result')}</span>}</div> : waiting ? <p className="mt-2 text-xs text-ink-faint" role="status">{overdue
        ? text('等待时间已较长，仍未收到完整说明；生成尚未确认结束。', 'The wait is taking longer. A complete explanation has not arrived, and generation has not been confirmed to have stopped.')
        : text('基础说明仍在整理，保存后会出现在这里。', 'An explanation is still being prepared. It will appear here when saved.')}</p> : null}
      {confirmedFailure ? <p className="mt-2 text-xs text-ink-faint" role="status">{text('这次说明未完成，失败记录已保留。可以重新明确生成一份。', 'This explanation did not finish. The failed request is retained; you can explicitly generate a new one.')}</p> : unknownResult ? <p className="mt-2 text-xs text-ink-faint" role="status">{text('这次请求的结果尚未确认；刷新不会重新生成。', 'The result of this request is unconfirmed. Refreshing does not generate it again.')}</p> : null}
      {requestRejected ? <p className="mt-2 text-xs text-ink-faint" role="status">{text('引用的说明目前不可用，这次追问未发起。问题已保留，可以重新选择来源。', 'The reading source is unavailable, so this question was not started. Your question is saved; you can choose another source.')}</p> : null}
      {!readOnly && pendingRequest && !creating && !waiting ? requestRejected ? <Button className="mt-2 text-xs" onClick={() => {
        setDraft(pendingRequest.draft);
        generation.reset();
      }}>{text('编辑问题与来源', 'Edit question and source')}</Button> : <Button className="mt-2 text-xs" onClick={() => generation.mutate(pendingRequest)}>{text('重试同一请求', 'Retry this same request')}</Button> : null}
      {!readOnly && failed.length ? <RawDisclosure label={text(`未完成的说明（${failed.length}）`, `Unfinished explanations (${failed.length})`)}><div className="max-h-[24vh] overflow-y-auto">{failed.map(item => <div key={item.path} className="mt-2 flex items-center gap-2 text-xs text-ink-faint">
        <span className="min-w-0 flex-1 truncate" title={item.reader_foundation!.question}>{text('未完成：', 'Not completed: ')}{item.reader_foundation!.question}</span>
        <Button className="shrink-0 text-xs" disabled={creationBlocked} onClick={() => setDraft({
          question: item.reader_foundation!.question, sourceTaskId: item.reader_foundation!.source_task_id ?? undefined,
          parentId: item.reader_foundation!.parent_id ?? undefined,
          parentTitle: item.reader_foundation!.parent_id ? readerFoundationTitle(notes.find(note => note.reader_foundation?.id === item.reader_foundation!.parent_id)?.reader_foundation
            ?? { question: text('先前选择的说明', 'Previously selected reading') }) : undefined,
        })}>{text('重新编辑问题', 'Edit this question')}</Button>
      </div>)}</div></RawDisclosure> : null}
      <p className="mt-1.5 text-[11px] text-ink-faint">{text('说明和问答会保存下来，供你随时回看。', 'Explanations and questions are saved for later reading.')}</p>
    </section>
    <Modal open={!!draft} onClose={() => setDraft(null)} label={draft?.parentId ? text('继续理解', 'Ask about your reading') : text('从基础理解', 'Understand the foundations')}>
      <ModalHeader title={draft?.parentId ? text('哪里还不明白', 'What is still unclear') : text('你想理解什么', 'What would you like to understand')}
        sub={draft?.parentId ? text('可以引用一句话，或者写下算不明白的那一步。', 'Quote a sentence or describe the step you could not work through.') : text('用自己的话选定一个问题，说明会保存下来供以后阅读。', 'Choose a question in your own words. Its explanation will be saved for later reading.')} />
      <div className="px-6 pb-6">
        {draft?.parentId ? <label className="mb-3 block text-xs text-ink-dim">
          {text('引用的说明或回答', 'Reading source')}
          <select aria-label={text('引用的说明或回答', 'Reading source')} className="mt-1 block w-full min-w-0 rounded border border-line bg-bg px-2 py-2 text-xs text-ink"
            value={draft.parentId} onChange={event => {
              const source = availableParents.find(item => item.reader_foundation?.id === event.target.value);
              if (source) {
                setDraft(previous => previous ? { ...previous, parentId: source.reader_foundation!.id, parentTitle: readerFoundationTitle(source.reader_foundation!) } : previous);
                selectFoundation(client, sid, language, source.reader_foundation!.root_id ?? source.reader_foundation!.id);
              }
            }}>
            {!availableParents.some(item => item.reader_foundation?.id === draft.parentId) ? <option value={draft.parentId} disabled>{draft.parentTitle || text('原来源', 'Original source')}{text(' · 当前不可用', ' · unavailable')}</option> : null}
            {availableParents.map(item => <option key={item.path} value={item.reader_foundation!.id}>{readerFoundationTitle(item.reader_foundation!)}</option>)}
          </select>
        </label> : null}
        <textarea aria-label={text('想理解的问题', 'Question to understand')} className="min-h-40 w-full rounded-lg border border-line bg-bg p-3 text-sm leading-6 text-ink"
          maxLength={8000} value={draft?.question ?? ''} onChange={event => setDraft(previous => previous ? { ...previous, question: event.target.value } : previous)} />
        {draft?.sourceTitle ? <p className="mt-2 text-xs text-ink-faint">{text('从这项任务打开：', 'Opened from this task: ')}{draft.sourceTitle}</p> : null}
        <div className="mt-3 flex justify-end"><Button disabled={!draft?.question.trim() || creationBlocked || readOnly
          || !!draft?.parentId && !availableParents.some(item => item.reader_foundation?.id === draft.parentId)} onClick={submit}>{draft?.parentId ? text('提问', 'Ask question') : text('生成并保存基础说明', 'Generate and save foundations')}</Button></div>
      </div>
    </Modal>
  </>;
}
