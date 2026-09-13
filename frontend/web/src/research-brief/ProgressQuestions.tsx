import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BookOpen, MessageCircle } from 'lucide-react';
import type { ProgressSourceRef } from '../../../core/src/types';
import { api, type ArtifactInfo } from '../api';
import { Button } from '../components/primitives';
import { Modal, ModalHeader } from '../components/Modal';
import { MarkdownContent } from '../components/MarkdownContent';
import { useI18n } from '../i18n';
import type { CardCopy } from '../map/presentation';
import type { FoundationDraft } from './foundation';
import { readerFoundationTitle } from '../lib/artifactPresentation';
import { ReadingQuestionEditor } from './ReadingQuestionEditor';
import { ReaderExplanationStatus } from './ReaderExplanation';
import { isProgressSourceRef, progressSourceForCard } from './progressSource';
import { useReadingRequest } from './useReadingRequest';

interface ProgressQuestionsContext {
  sid: string;
  readOnly: boolean;
  hasHistory: boolean;
  open: (source?: ProgressSourceRef) => void;
}
const Questions = createContext<ProgressQuestionsContext | null>(null);

/** Small shared entry; the application owns one question modal, never a chat per card. */
export function ProgressQuestionButton({ card, cardKey, taskId, readOnly = false }: {
  card?: CardCopy; cardKey: string; taskId: string; readOnly?: boolean;
}) {
  const questions = useContext(Questions);
  const zh = useI18n().locale === 'zh-CN';
  if (!questions || questions.readOnly || readOnly) return null;
  const source = progressSourceForCard(card, cardKey, taskId);
  const available = source?.path.startsWith(`reader-progress/${questions.sid}/`);
  return <Button className="inline-flex items-center gap-1 text-xs" disabled={!available}
    title={!available ? zh ? '这版说明的保存来源尚不可用。' : 'The saved source for this explanation is unavailable.' : undefined}
    data-testid="progress-question-trigger" data-progress-question-card={cardKey} data-progress-source-id={source?.source_id}
    onClick={() => { if (available && source) questions.open(source); }}>
    <MessageCircle size={12} />{zh ? '问这一步' : 'Ask about this step'}
  </Button>;
}

export function ProgressQuestionHistoryButton() {
  const questions = useContext(Questions);
  const zh = useI18n().locale === 'zh-CN';
  if (!questions?.hasHistory) return null;
  return <Button className="inline-flex items-center gap-1 text-xs" onClick={() => questions.open()}>
    <BookOpen size={12} />{zh ? '阅读问答' : 'Reading questions'}
  </Button>;
}

/** Existing protected artifact reads are the only source of displayed saved Markdown. */
function SavedReading({ sid, path, artifacts, onOpenArtifact }: {
  sid: string; path: string; artifacts: Array<Pick<ArtifactInfo, 'path' | 'storage_path'>>; onOpenArtifact: (path: string) => void;
}) {
  const zh = useI18n().locale === 'zh-CN';
  const reading = useQuery({ queryKey: ['reading-question-artifact', sid, path],
    queryFn: async ({ signal }) => {
      const file = await api.artifact(sid, path, signal);
      if (file.exists && file.path === path && (file.truncated || typeof file.preview !== 'string')) {
        const body = await api.artifactBlob(sid, path, false, signal);
        return { ...file, preview: await body.text(), truncated: false };
      }
      return file;
    }, retry: false,
    staleTime: Infinity, refetchOnWindowFocus: false });
  if (reading.isPending) return <p className="text-xs text-ink-faint">{zh ? '正在读取保存内容…' : 'Loading saved reading…'}</p>;
  if (reading.isError || !reading.data?.exists || reading.data.path !== path) return <p role="status" className="text-xs text-ink-faint">
    {zh ? '保存内容暂时无法读取。' : 'The saved reading could not be loaded.'}
    <Button className="ml-2 text-xs" onClick={() => void reading.refetch()}>{zh ? '重新读取' : 'Reload'}</Button>
  </p>;
  return <div className="min-w-0 max-w-full overflow-x-auto break-words" data-reading-artifact-path={path}>
    <MarkdownContent artifacts={[...artifacts, ...(reading.data.reader_foundation?.sources ?? [])]}
      onOpenArtifact={onOpenArtifact}>{reading.data.preview ?? ''}</MarkdownContent>
    {reading.data.truncated ? <p className="mt-2 text-xs text-ink-faint">{zh ? '此处显示保存内容的节选。' : 'This view shows an excerpt of the saved reading.'}</p> : null}
  </div>;
}

/** Drafts pin the source seen at opening; submitted requests keep their original scope. */
export function ProgressQuestionsProvider({ sid, readOnly = false, children }: {
  sid: string | null | undefined; readOnly?: boolean; children: ReactNode;
}) {
  const locale = useI18n().locale;
  const language = locale === 'zh-CN' ? 'zh-CN' : 'en-US';
  const zh = language === 'zh-CN';
  const request = useReadingRequest(sid ?? '', language, !!sid);
  const [selection, setSelection] = useState<{ sid: string; locale: string; source: ProgressSourceRef } | null>(null);
  const [draft, setDraft] = useState<FoundationDraft | null>(null);
  const [answerId, setAnswerId] = useState<string | null>(null);
  const [showSource, setShowSource] = useState(false);
  const [linkedPath, setLinkedPath] = useState<string | null>(null);
  useEffect(() => {
    setSelection(null); setDraft(null); setAnswerId(null); setShowSource(false); setLinkedPath(null);
  }, [sid, language]);
  const current = selection && selection.sid === sid && selection.locale === language ? selection : null;
  const source = current?.source;
  const progressNotes = request.notes.filter(item => isProgressSourceRef(item.reader_foundation?.progress_source));
  const pending = request.pendingRequest?.draft.progressSource ? request.pendingRequest : null;
  const sources = new Map<string, ProgressSourceRef>();
  for (const item of progressNotes) {
    const ref = item.reader_foundation!.progress_source!;
    if (ref.path.startsWith(`reader-progress/${sid}/`)) sources.set(ref.source_id, ref);
  }
  if (pending?.draft.progressSource) sources.set(pending.draft.progressSource.source_id, pending.draft.progressSource);
  if (source) sources.set(source.source_id, source);
  const artifactReferences = [...(request.list.data ?? []), ...[...sources.values()].map(ref => ({ path: ref.path }))];
  const openArtifact = (path: string) => {
    // MarkdownContent resolves only registered references; the protected GET
    // validates the path again. Opening a citation never changes the draft.
    if (path === source?.path) { setShowSource(true); setLinkedPath(null); }
    else setLinkedPath(path);
  };
  const notes = source ? progressNotes.filter(item => item.reader_foundation!.progress_source!.source_id === source.source_id) : [];
  const answer = notes.find(item => item.reader_foundation?.id === answerId);
  const pendingHere = pending?.draft.progressSource?.source_id === source?.source_id ? pending : null;
  const confirmed = pendingHere ? notes.find(item => item.reader_foundation?.id === pendingHere.id) : undefined;
  const rejectedHere = !!pendingHere && pendingHere.rejected === 'reader_source_unavailable';
  const unknownHere = !!pendingHere && !rejectedHere && !confirmed && !request.creating;
  const open = (ref?: ProgressSourceRef) => {
    const chosen = ref ?? pending?.draft.progressSource ?? [...sources.values()].at(-1);
    if (!sid || !isProgressSourceRef(chosen) || !chosen.path.startsWith(`reader-progress/${sid}/`)) return;
    setSelection({ sid, locale: language, source: { ...chosen } });
    setShowSource(false);
    setLinkedPath(null);
    const latest = progressNotes.filter(item => item.reader_foundation?.progress_source?.source_id === chosen.source_id).at(-1);
    setAnswerId(latest?.reader_foundation?.id ?? null);
    setDraft(ref && !readOnly ? { question: '', progressSource: { ...chosen } } : null);
  };
  const close = () => { setSelection(null); setDraft(null); setAnswerId(null); setShowSource(false); setLinkedPath(null); };
  const chooseSource = (ref: ProgressSourceRef) => {
    if (!sid) return;
    setSelection({ sid, locale: language, source: { ...ref } });
    setAnswerId(null); setShowSource(false); setLinkedPath(null);
    setDraft(previous => previous ? { question: previous.question, progressSource: { ...ref } } : null);
  };
  const submit = () => {
    if (readOnly || !current || !draft?.progressSource || draft.progressSource.source_id !== current.source.source_id) return;
    if (draft.parentId && !notes.some(item => item.reader_foundation?.id === draft.parentId
      && item.exists && item.reader_foundation?.state === 'complete')) return;
    const id = request.submit(draft);
    if (id) { setAnswerId(id); setDraft(null); }
  };
  const questionIsBusy = !!pendingHere && request.creating || notes.some(item => item.reader_foundation?.state === 'generating');
  return <Questions.Provider value={{ sid: sid ?? '', readOnly, hasHistory: sources.size > 0, open }}>
    {children}
    <Modal open={!!current} onClose={close} label={zh ? '进展阅读问答' : 'Questions about this progress'}>
      {current && source ? <>
        <ModalHeader title={zh ? '哪里还不明白' : 'What is still unclear'} sub={source.title} />
        <div className="space-y-4 px-6 pb-6" data-testid="progress-questions" data-project-id={current.sid}
          data-progress-source-id={source.source_id} data-progress-source-title={source.title}
          data-reader-card={source.card_key} data-reader-task-id={source.task_id}>
          <div className="rounded-lg border border-line/70 p-3 text-xs text-ink-dim">
            <p>{zh ? '引用打开提问时的这版说明：' : 'Using the explanation selected when this question was opened: '}{source.title}</p>
            <time dateTime={new Date(source.generated_at * 1000).toISOString()} className="mt-1 block text-ink-faint">
              {new Date(source.generated_at * 1000).toLocaleString(locale)}
            </time>
            <Button className="mt-2 text-xs" data-reading-source-path={source.path} onClick={() => setShowSource(value => !value)}>
              {showSource ? zh ? '收起原说明' : 'Hide original explanation' : zh ? '阅读当时说明' : 'Read the original explanation'}
            </Button>
            {showSource ? <div className="mt-3 text-[13px] leading-6"><SavedReading sid={current.sid} path={source.path}
              artifacts={artifactReferences} onOpenArtifact={openArtifact} /></div> : null}
          </div>
          {sources.size > 1 ? <label className="block text-xs text-ink-dim">{zh ? '查看已保存的进展问答' : 'Saved progress questions'}
            <select className="mt-1 block w-full rounded border border-line bg-bg px-2 py-2 text-xs" value={source.source_id}
              disabled={request.creating} onChange={event => { const ref = sources.get(event.target.value); if (ref) chooseSource(ref); }}>
              {[...sources.values()].map(ref => <option key={ref.source_id} value={ref.source_id}>{ref.title} · {new Date(ref.generated_at * 1000).toLocaleString(locale)}</option>)}
            </select>
          </label> : null}
          {notes.length ? <label className="block text-xs text-ink-dim">{zh ? '已保存的问题' : 'Saved questions'}
            <select className="mt-1 block w-full rounded border border-line bg-bg px-2 py-2 text-xs" value={answerId ?? ''}
              onChange={event => { setAnswerId(event.target.value || null); setLinkedPath(null); }}>
              <option value="">{zh ? '选择一个问题' : 'Choose a question'}</option>
              {notes.map(item => <option key={item.path} value={item.reader_foundation!.id}>{item.reader_foundation!.question}</option>)}
            </select>
          </label> : null}
          {answer ? <section className="space-y-3 text-[13px] leading-6" data-reading-question-id={answer.reader_foundation!.id}>
            <p className="whitespace-pre-wrap break-words font-medium">{answer.reader_foundation!.question}</p>
            {answer.exists && answer.reader_foundation!.state === 'complete'
              ? <SavedReading sid={current.sid} path={answer.path} artifacts={artifactReferences} onOpenArtifact={openArtifact} />
              : <p role="status" className="text-xs text-ink-faint">{answer.reader_foundation!.state === 'generating'
                ? zh ? '回答仍在整理，尚未确认结束。' : 'The answer is still being prepared; completion is unconfirmed.'
                : zh ? '这次回答未完成，原问题已保留。' : 'This answer did not finish; its question is retained.'}</p>}
            {!readOnly && answer.exists && answer.reader_foundation!.state === 'complete' ? <Button className="text-xs" disabled={request.creationBlocked}
              onClick={() => setDraft({ question: '', progressSource: { ...source }, parentId: answer.reader_foundation!.id,
                parentTitle: readerFoundationTitle(answer.reader_foundation!) })}>{zh ? '继续问这份回答' : 'Ask a follow-up to this answer'}</Button> : null}
            {!readOnly && answer.reader_foundation!.state === 'failed' ? <Button className="text-xs" disabled={request.creationBlocked}
              onClick={() => setDraft({ question: answer.reader_foundation!.question, progressSource: { ...source },
                parentId: answer.reader_foundation!.parent_id ?? undefined })}>{zh ? '重新编辑问题' : 'Edit this question'}</Button> : null}
          </section> : null}
          {linkedPath ? <section className="space-y-2 rounded-lg border border-line/70 p-3 text-[13px] leading-6" data-referenced-reading-path={linkedPath}>
            <div className="flex items-center justify-between gap-2 text-xs text-ink-faint">
              <span>{zh ? '引用的保存内容' : 'Referenced saved reading'}</span>
              <Button className="text-xs" onClick={() => setLinkedPath(null)}>{zh ? '收起引用' : 'Close referenced reading'}</Button>
            </div>
            <SavedReading sid={current.sid} path={linkedPath} artifacts={artifactReferences} onOpenArtifact={openArtifact} />
          </section> : null}
          {questionIsBusy ? <ReaderExplanationStatus generating phase={pendingHere ? request.progress.phase : undefined} /> : null}
          {pendingHere ? <div className="space-y-2 text-xs text-ink-faint" data-pending-reading-question={pendingHere.id}>
            <p className="whitespace-pre-wrap break-words">{pendingHere.draft.question}</p>
            {rejectedHere ? <p role="status">{zh ? '引用的说明目前不可用，这次追问未发起。问题与原来源已保留。' : 'The reading source is unavailable, so this question was not started. Its question and original source are saved.'}</p>
              : unknownHere ? <p role="status">{zh ? '这次请求的结果尚未确认；刷新不会重新生成。' : 'The result of this request is unconfirmed. Refreshing does not generate it again.'}</p> : null}
            {!readOnly && !request.creating && !request.waiting ? <Button className="text-xs" onClick={() => {
              if (rejectedHere) { setDraft({ ...pendingHere.draft }); request.generation.reset(); }
              else request.retrySavedRequest(pendingHere);
            }}>{rejectedHere ? zh ? '编辑问题与来源' : 'Edit question and source' : zh ? '重试同一请求' : 'Retry this same request'}</Button> : null}
          </div> : pending && request.creationBlocked ? <p role="status" className="text-xs text-ink-faint">
            {zh ? '另一个阅读请求尚未结束。' : 'Another reading request is still unresolved.'}
            <Button className="ml-2 text-xs" onClick={() => open()}>{zh ? '查看原问题' : 'View the original question'}</Button>
          </p> : null}
          {request.list.isError ? <p role="status" className="text-xs text-ink-faint">{zh ? '保存的问答暂时读取失败。' : 'Saved questions could not be loaded.'}
            <Button className="ml-2 text-xs" onClick={() => void request.list.refetch()}>{zh ? '重新读取' : 'Reload'}</Button>
          </p> : null}
          {draft && !readOnly ? <section className="border-t border-line/60 pt-3">
            {draft.parentId ? <p className="mb-2 text-xs text-ink-dim">{zh ? '继续引用这份回答：' : 'Following up on this answer: '}{draft.parentTitle || notes.find(item => item.reader_foundation?.id === draft.parentId)?.reader_foundation?.question}</p> : null}
            <ReadingQuestionEditor draft={draft} onChange={setDraft} onSubmit={submit} disabled={request.creationBlocked} />
          </section> : !readOnly ? <Button className="text-xs" disabled={request.creationBlocked}
            onClick={() => setDraft({ question: '', progressSource: { ...source } })}>{zh ? '问这版说明' : 'Ask about this explanation'}</Button> : null}
        </div>
      </> : null}
    </Modal>
  </Questions.Provider>;
}
