import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ProgressSourceRef } from '../../../core/src/types';
import { ApiError } from '../../../core/src/http';
import { api, type ArtifactInfo } from '../api';
import { I18nProvider, useI18n } from '../i18n';
import { Modal } from '../components/Modal';
import { MarkdownContent } from '../components/MarkdownContent';
import { ArtifactModal } from '../components/ArtifactModal';
import { MapReaderContent } from '../map/MapReaderContent';
import type { CardCopy } from '../map/presentation';
import { ProgressQuestionButton, ProgressQuestionHistoryButton, ProgressQuestionsProvider, type QuestionSourceContext } from './ProgressQuestions';
import { QuestionFoundation } from './QuestionFoundation';
import { foundationChoice, foundationListKey, selectFoundation } from './foundation';
import ResearchBrief from './ResearchBrief';
import { briefCopyKey, briefLiveKey, briefSelection, currentBriefData } from './model';
import { completedCopy, inputs, source as liveSource } from './testFixtures';

vi.mock('../components/Modal', () => ({
  Modal: ({ open, children }: { open: boolean; children: ReactNode }) => open ? <div role="dialog">{children}</div> : null,
  ModalHeader: ({ title, sub }: { title: string; sub?: string }) => <header><h2>{title}</h2>{sub}</header>,
}));

const firstId = '11111111-1111-4111-8111-111111111111';
const secondId = '22222222-2222-4222-8222-222222222222';
const sourceA: ProgressSourceRef = { source_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', title: 'The original progress explanation',
  generated_at: 100, path: 'reader-progress/project-a/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.md',
  task_id: 'task-a', card_key: 'task-a', copy_revision: 4 };
const sourceB: ProgressSourceRef = { ...sourceA, source_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', title: 'A newer progress explanation',
  generated_at: 200, copy_revision: 5, path: 'reader-progress/project-a/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.md' };
function card(ref = sourceA): CardCopy {
  return { title: ref.title, summary: 'Recorded result', detail: 'Recorded conditions', generated_at: ref.generated_at,
    copy_revision: ref.copy_revision, version: 23, input_revision: 'existing-input-version',
    reader_brief: { why: 'Why it matters', concept: null, scope: 'This case only', next: 'Recorded next step' } };
}
function answer(id: string, question: string, ref = sourceA, parent?: string): ArtifactInfo {
  return { path: `reader-notes/project-a/${id}.md`, name: `${id}.md`, why: question, exists: true,
    kind: 'text', mime: 'text/markdown', size: 120, mtime: 300, source: 'reader_foundation',
    preview: `# Actual saved answer\n\n${question}\n\nThe saved answer for ${ref.title}.`,
    reader_foundation: { id, question, title: 'A saved answer', locale: 'en-US', created_at: 300, version: 1, state: 'complete',
      kind: parent ? 'clarification' : 'progress_answer', parent_id: parent ?? null, root_id: parent ?? id, progress_source: ref } };
}
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (value: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

let renderer: ReactTestRenderer | undefined;
let client: QueryClient;
let props: { sid: string; card?: CardCopy; cardKey?: string; taskId?: string; context?: QuestionSourceContext; readOnly?: boolean; foundation?: boolean; reader?: boolean };
let rows: Record<string, ArtifactInfo[]>;
let localeControl: ReturnType<typeof useI18n>;
function LocaleControl() { localeControl = useI18n(); return null; }
const button = (label: string) => renderer!.root.findAllByType('button').find(node => node.children.includes(label))!;
const dialog = () => renderer!.root.findByProps({ 'data-testid': 'progress-questions' });
const text = () => JSON.stringify(renderer!.toJSON());
const clientFactory = () => new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false, gcTime: Infinity } } });
function tree() {
  const cardKey = props.cardKey ?? sourceA.card_key, taskId = props.taskId ?? sourceA.task_id;
  return <QueryClientProvider client={client}><I18nProvider><LocaleControl />
    <ProgressQuestionsProvider sid={props.sid} readOnly={props.readOnly}>
      {props.reader ? <MapReaderContent card={props.card} cardKey={cardKey} taskId={taskId} originalDetail="Original work record" />
        : <ProgressQuestionButton card={props.card} cardKey={cardKey} taskId={taskId} context={props.context} />}
      <ProgressQuestionHistoryButton />
      {props.foundation ? <QuestionFoundation sid={props.sid} onOpenArtifact={() => {}} /> : null}
    </ProgressQuestionsProvider>
  </I18nProvider></QueryClientProvider>;
}
async function flush() { await act(async () => { await vi.advanceTimersByTimeAsync(25); }); }
async function mount() { await act(async () => { renderer = create(tree()); }); await flush(); }
async function open() { await act(async () => button('Ask about this step').props.onClick()); await flush(); }
function edit(value: string) { act(() => renderer!.root.findByType('textarea').props.onChange({ target: { value } })); }
function submit() { act(() => button('Ask question').props.onClick()); }
function close() { act(() => renderer!.root.findAllByType(Modal).find(node => node.props.open
  && ['Questions about this progress', '进展阅读问答'].includes(node.props.label))!.props.onClose()); }

beforeEach(() => {
  vi.useFakeTimers();
  const storage = new Map<string, string>([['argus.locale', 'en']]);
  vi.stubGlobal('localStorage', { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value) });
  vi.stubGlobal('window', { location: { search: '' } });
  vi.stubGlobal('document', { documentElement: { lang: 'en' } });
  vi.stubGlobal('navigator', { language: 'en-US' });
  vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValueOnce(firstId).mockReturnValueOnce(secondId) });
  client = clientFactory(); props = { sid: 'project-a', card: card() }; rows = { 'project-a': [], 'project-b': [] };
  vi.spyOn(api, 'artifacts').mockImplementation(async sid => rows[sid] ?? []);
  vi.spyOn(api, 'mapQuestionSource').mockImplementation(async (sid, body) => {
    const ref = props.card?.copy_revision === sourceB.copy_revision ? sourceB : sourceA;
    return { ...ref, card_key: body.card_key, task_id: body.task_id, path: `reader-progress/${sid}/${ref.source_id}.md` };
  });
  vi.spyOn(api, 'artifact').mockImplementation(async (sid, path) => rows[sid]?.find(item => item.path === path) ?? {
    path, name: 'retained.md', why: '', exists: true, kind: 'text', mime: 'text/markdown', size: 80, mtime: 100,
    source: 'progress_snapshot', progress_source: sourceA, preview: '# The actual retained progress source\n\nOriginal source text.',
  });
  vi.spyOn(api, 'generateMapCopy').mockRejectedValue(new Error('A reading question must not regenerate the progress card'));
  vi.spyOn(api, 'messageStream').mockRejectedValue(new Error('A reading question must not enter research dispatch'));
});
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; client.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it.each(['button', 'task', 'step'])('uses the same independent editor for a %s reader', async reader => {
  const actualSource = reader === 'step' ? { ...sourceA, card_key: 'earlier-review' } : sourceA;
  props.reader = reader !== 'button'; props.card = card(actualSource); props.cardKey = actualSource.card_key;
  const pending = deferred<ArtifactInfo>();
  const generate = vi.spyOn(api, 'generateReaderFoundation').mockReturnValue(pending.promise);
  await mount();
  expect(api.mapQuestionSource).not.toHaveBeenCalled();
  await open(); edit('Why does the recorded operation work?');
  expect(api.mapQuestionSource).toHaveBeenCalledExactlyOnceWith('project-a', {
    card_key: actualSource.card_key, task_id: actualSource.task_id, locale: 'en-US',
  });
  expect(dialog().props['data-progress-source-id']).toBe(sourceA.source_id);
  expect(generate).not.toHaveBeenCalled();
  props = { ...props, card: card({ ...sourceB, card_key: actualSource.card_key }) };
  act(() => { selectFoundation(client, props.sid, 'en-US', 'a-different-foundation'); renderer!.update(tree()); });
  expect(dialog().props['data-progress-source-id']).toBe(sourceA.source_id);
  expect(dialog().props['data-progress-source-title']).toBe(sourceA.title);
  submit(); await flush();
  expect(generate).toHaveBeenCalledTimes(1);
  expect(generate.mock.calls[0].slice(0, 2)).toEqual(['project-a', { request_id: firstId,
    question: 'Why does the recorded operation work?', locale: 'en-US', progress_source: { source_id: sourceA.source_id } }]);
  const saved = answer(firstId, 'Why does the recorded operation work?', actualSource); rows['project-a'] = [saved];
  await act(async () => pending.resolve(saved)); await flush();
  expect(renderer!.root.findByProps({ 'data-reading-question-id': firstId })).toBeTruthy();
  expect(renderer!.root.findAllByType(MarkdownContent).map(node => node.props.children)).toContain(saved.preview);
  expect(foundationChoice(client, 'project-a', 'en-US').id).toBe('a-different-foundation');
  expect(api.generateMapCopy).not.toHaveBeenCalled(); expect(api.messageStream).not.toHaveBeenCalled();
});

it('opens the actual current brief and its reading modal in the shared dialog without filling research chat', async () => {
  const value = inputs();
  const saved = completedCopy(liveSource.tasks[0], ['start-a', 'main-a'], 100);
  const original = saved.cards.a;
  const ref = { ...sourceA, path: `reader-progress/${value.sid}/${sourceA.source_id}.md`, task_id: 'a', card_key: 'a',
    title: original.title, generated_at: original.generated_at, copy_revision: original.copy_revision! };
  vi.mocked(api.mapQuestionSource).mockResolvedValue(ref);
  client.setQueryData(briefCopyKey(value.sid, 'en-US'), saved);
  client.setQueryData(briefLiveKey(value.sid, briefSelection(value.snapshot, value.view)), currentBriefData(liveSource, value.sid, 'a'));
  const research = vi.fn();
  await act(async () => { renderer = create(<QueryClientProvider client={client}><I18nProvider>
    <ProgressQuestionsProvider sid={value.sid}><ResearchBrief {...value} active={false} onAsk={research} /></ProgressQuestionsProvider>
  </I18nProvider></QueryClientProvider>); }); await flush();
  await open(); expect(dialog().props['data-progress-source-id']).toBe(ref.source_id); close();
  act(() => button('Read explanation').props.onClick());
  const reading = renderer!.root.findByProps({ 'data-testid': 'research-brief-reading' });
  await act(async () => reading.findAllByType('button').find(node => node.children.includes('Ask about this step'))!.props.onClick());
  await flush();
  edit('The question about the explanation already open');
  const updated = { ...saved, cards: { a: { ...saved.cards.a, ...card({ ...sourceB, task_id: 'a', card_key: 'a',
    path: `reader-progress/${value.sid}/${sourceB.source_id}.md` }) } } };
  act(() => { client.setQueryData(briefCopyKey(value.sid, 'en-US'), updated); }); await flush();
  expect(dialog().props['data-progress-source-id']).toBe(ref.source_id);
  expect(renderer!.root.findByType('textarea').props.value).toBe('The question about the explanation already open');
  expect(research).not.toHaveBeenCalled(); expect(api.messageStream).not.toHaveBeenCalled(); expect(api.generateMapCopy).not.toHaveBeenCalled();
});

it('reopens saved questions by source and follows only the explicitly selected answer', async () => {
  const first = answer(firstId, 'The saved first question'); rows['project-a'] = [first];
  props.card = card(sourceB);
  const follow = answer(secondId, 'My explicit follow-up', sourceA, firstId);
  const ask = vi.spyOn(api, 'askReaderFoundation').mockImplementation(async () => { rows['project-a'] = [first, follow]; return follow; });
  const generate = vi.spyOn(api, 'generateReaderFoundation');
  vi.mocked(crypto.randomUUID).mockReset().mockReturnValue(secondId);
  await mount(); act(() => button('Reading questions').props.onClick()); await flush();
  expect(dialog().props['data-progress-source-id']).toBe(sourceA.source_id);
  const choice = renderer!.root.findAllByType('option').find(node => node.props.value === firstId)!;
  expect(choice.children).toEqual(['A saved answer']);
  expect(choice.props.title).toBe(first.reader_foundation!.question);
  const question = renderer!.root.findAllByType('details').find(node => node.findByType('summary').children.includes('View this question'))!;
  expect(question.props.open).toBeUndefined();
  expect(question.findByType('p').children).toEqual([first.reader_foundation!.question]);
  expect(renderer!.root.findAllByType(MarkdownContent).map(node => node.props.children)).toContain(first.preview);
  act(() => button('Read the original explanation').props.onClick()); await flush();
  expect(api.artifact).toHaveBeenCalledWith('project-a', sourceA.path, expect.any(AbortSignal));
  act(() => button('Ask a follow-up to this answer').props.onClick()); edit('My explicit follow-up'); submit(); await flush();
  expect(ask.mock.calls[0].slice(0, 3)).toEqual(['project-a', firstId,
    { request_id: secondId, question: 'My explicit follow-up', locale: 'en-US' }]);
  expect(generate).not.toHaveBeenCalled();
  expect(dialog().props['data-progress-source-id']).toBe(sourceA.source_id);
});

it('reads the complete registered answer when the artifact preview is truncated', async () => {
  const saved = { ...answer(firstId, 'Read the entire saved answer'), preview: '# Partial preview', truncated: true };
  rows['project-a'] = [saved];
  const full = '# Complete saved answer\n\nThe last paragraph is part of the saved response.';
  const raw = vi.spyOn(api, 'artifactBlob').mockResolvedValue(new Blob([full], { type: 'text/markdown' }));
  await mount(); act(() => button('Reading questions').props.onClick()); await flush(); await flush();
  expect(raw).toHaveBeenCalledWith('project-a', saved.path, false, expect.any(AbortSignal));
  expect(renderer!.root.findAllByType(MarkdownContent).map(node => node.props.children)).toContain(full);
});

it('opens actual source and parent citations through protected reads without changing an unfinished follow-up', async () => {
  const first = answer(firstId, 'First saved question');
  const second = { ...answer(secondId, 'Second saved question', sourceA, firstId),
    preview: `# Second saved answer\n\n[Original progress](${sourceA.path})\n\n[Earlier answer](${first.path})` };
  rows['project-a'] = [first, second];
  const nextId = '33333333-3333-4333-8333-333333333333';
  vi.mocked(crypto.randomUUID).mockReset().mockReturnValue(nextId);
  const follow = answer(nextId, 'Unfinished follow-up on the second answer', sourceA, secondId);
  const ask = vi.spyOn(api, 'askReaderFoundation').mockImplementation(async () => { rows['project-a'] = [first, second, follow]; return follow; });
  await mount(); act(() => button('Reading questions').props.onClick()); await flush();
  act(() => button('Ask a follow-up to this answer').props.onClick()); edit('Unfinished follow-up on the second answer');
  const clickCitation = (path: string) => {
    const link = renderer!.root.findAllByType('a').find(node => node.props['data-artifact-path'] === path)!;
    const preventDefault = vi.fn(); expect(link.props.target).toBeUndefined();
    act(() => link.props.onClick({ preventDefault })); expect(preventDefault).toHaveBeenCalledTimes(1);
  };
  clickCitation(sourceA.path); await flush();
  expect(api.artifact).toHaveBeenCalledWith('project-a', sourceA.path, expect.any(AbortSignal));
  expect(renderer!.root.findByProps({ 'data-reading-artifact-path': sourceA.path })).toBeTruthy();
  clickCitation(first.path); await flush();
  expect(api.artifact).toHaveBeenCalledWith('project-a', first.path, expect.any(AbortSignal));
  expect(renderer!.root.findByProps({ 'data-referenced-reading-path': first.path })).toBeTruthy();
  expect(renderer!.root.findByType('textarea').props.value).toBe('Unfinished follow-up on the second answer');
  act(() => renderer!.root.findAllByType('select').find(node => node.props.value === secondId)!.props.onChange({ target: { value: firstId } }));
  expect(renderer!.root.findByType('textarea').props.value).toBe('Unfinished follow-up on the second answer');
  expect(dialog().props['data-progress-source-id']).toBe(sourceA.source_id);
  submit(); await flush();
  expect(ask.mock.calls[0].slice(0, 3)).toEqual(['project-a', secondId, {
    request_id: nextId, question: 'Unfinished follow-up on the second answer', locale: 'en-US',
  }]);
});

it('shows an actual raw-read failure and never falls back to a stale list preview', async () => {
  const saved = { ...answer(firstId, 'Read the complete answer'), preview: '# Stale list body', truncated: true };
  rows['project-a'] = [saved];
  vi.mocked(api.artifact).mockResolvedValue({ ...saved, preview: '# Truncated current GET' });
  const raw = vi.spyOn(api, 'artifactBlob').mockRejectedValueOnce(new Error('The raw artifact could not be read'));
  await mount(); act(() => button('Reading questions').props.onClick()); await flush(); await flush();
  expect(text()).toContain('The saved reading could not be loaded');
  expect(text()).not.toContain('Stale list body'); expect(text()).not.toContain('Truncated current GET');
  raw.mockResolvedValueOnce(new Blob(['# Full original answer\n\nThe final saved paragraph.']));
  act(() => button('Reload').props.onClick()); await flush(); await flush();
  expect(text()).toContain('The final saved paragraph.'); expect(raw).toHaveBeenCalledTimes(2);
});

it('a late success updates only its submitted project and never opens an answer in the new project', async () => {
  const pending = deferred<ArtifactInfo>(); vi.spyOn(api, 'generateReaderFoundation').mockReturnValue(pending.promise);
  await mount(); await open(); edit('Question in the original project'); submit(); await flush();
  props = { ...props, sid: 'project-b' }; act(() => renderer!.update(tree()));
  const saved = answer(firstId, 'Question in the original project'); rows['project-a'] = [saved];
  await act(async () => pending.resolve(saved)); await flush();
  expect(client.getQueryData<ArtifactInfo[]>(foundationListKey('project-a'))).toContainEqual(saved);
  expect(client.getQueryData<ArtifactInfo[]>(foundationListKey('project-b'))).toEqual([]);
  expect(renderer!.root.findAllByProps({ 'data-testid': 'progress-questions' })).toHaveLength(0);
});

it('keeps the request unconfirmed when a response names a different progress source', async () => {
  vi.spyOn(api, 'generateReaderFoundation').mockResolvedValue(answer(firstId, 'The actual question', sourceB));
  await mount(); await open(); edit('The actual question'); submit(); await flush();
  expect(text()).toContain('The result of this request is unconfirmed');
  expect(JSON.parse(localStorage.getItem('argus:reader-foundation:project-a:en-US:request')!).draft.progressSource).toEqual(sourceA);
  expect(renderer!.root.findAllByProps({ 'data-reading-question-id': firstId })).toHaveLength(0);
});

it.each(['project', 'locale'])('closes an unsent draft when its %s changes', async change => {
  const generate = vi.spyOn(api, 'generateReaderFoundation'); await mount(); await open(); edit('Unsent original question');
  act(() => {
    if (change === 'project') { props = { ...props, sid: 'project-b' }; renderer!.update(tree()); }
    else localeControl.setLocale('zh-CN');
  });
  expect(renderer!.root.findAllByProps({ 'data-testid': 'progress-questions' })).toHaveLength(0);
  expect(generate).not.toHaveBeenCalled();
});

it.each(['project', 'locale'])('keeps a late source rejection in the submitted %s scope', async change => {
  const pending = deferred<ArtifactInfo>(); vi.spyOn(api, 'generateReaderFoundation').mockReturnValue(pending.promise);
  await mount(); await open(); edit('Keep this actual question'); submit(); await flush();
  act(() => {
    if (change === 'project') { props = { ...props, sid: 'project-b' }; renderer!.update(tree()); }
    else localeControl.setLocale('zh-CN');
  });
  await act(async () => pending.reject(new ApiError('Unavailable', 422, 'POST', '/reader-foundation', 'reader_source_unavailable')));
  await flush();
  expect(JSON.parse(localStorage.getItem('argus:reader-foundation:project-a:en-US:request')!)).toMatchObject({
    id: firstId, sid: 'project-a', locale: 'en-US', rejected: 'reader_source_unavailable',
    draft: { question: 'Keep this actual question', progressSource: sourceA },
  });
  const otherKey = change === 'project' ? 'argus:reader-foundation:project-b:en-US:request' : 'argus:reader-foundation:project-a:zh-CN:request';
  expect(localStorage.getItem(otherKey)).toBeNull();
  expect(renderer!.root.findAllByProps({ 'data-testid': 'progress-questions' })).toHaveLength(0);
});

it.each([new Error('Network lost'), new ApiError('Other validation', 422, 'POST', '/reader-foundation')])('retains unknown identity across a fresh cache without resubmitting (%s)', async failure => {
  const generate = vi.spyOn(api, 'generateReaderFoundation').mockRejectedValueOnce(failure);
  await mount(); await open(); edit('The question before a transport failure'); submit(); await flush();
  expect(text()).toContain('The result of this request is unconfirmed');
  act(() => renderer!.unmount()); renderer = undefined; client.clear(); client = clientFactory(); props.card = card(sourceB);
  await mount(); expect(generate).toHaveBeenCalledTimes(1);
  act(() => button('Reading questions').props.onClick());
  expect(dialog().props['data-progress-source-id']).toBe(sourceA.source_id);
  expect(text()).toContain('The question before a transport failure');
  const saved = answer(firstId, 'The question before a transport failure');
  generate.mockImplementationOnce(async () => { rows['project-a'] = [saved]; return saved; });
  act(() => button('Retry this same request').props.onClick()); await flush();
  expect(generate).toHaveBeenCalledTimes(2);
  expect(generate.mock.calls[1][1]).toEqual(generate.mock.calls[0][1]);
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
});

it('keeps a definitely rejected source and original question until an explicit new submission', async () => {
  const generate = vi.spyOn(api, 'generateReaderFoundation').mockRejectedValueOnce(
    new ApiError('Unavailable', 422, 'POST', '/reader-foundation', 'reader_source_unavailable'));
  await mount(); await open(); edit('Original rejected question'); submit(); await flush();
  expect(text()).toContain('this question was not started');
  act(() => button('Edit question and source').props.onClick()); edit('A cancelled edit'); close();
  expect(JSON.parse(localStorage.getItem('argus:reader-foundation:project-a:en-US:request')!).draft).toMatchObject({
    question: 'Original rejected question', progressSource: sourceA,
  });
  props.card = card(sourceB); act(() => renderer!.update(tree())); await open(); edit('Explicit question about the new source');
  const saved = answer(secondId, 'Explicit question about the new source', sourceB);
  generate.mockImplementationOnce(async () => { rows['project-a'] = [saved]; return saved; });
  submit(); await flush();
  expect(generate.mock.calls[1][1]).toEqual({ request_id: secondId, locale: 'en-US',
    question: 'Explicit question about the new source', progress_source: { source_id: sourceB.source_id } });
  expect(generate).toHaveBeenCalledTimes(2);
});

it('keeps progress answers out of the foundation selector and allows read-only history', async () => {
  vi.stubGlobal('window', { location: { search: '?reader_preview=question-foundation' } });
  rows['project-a'] = [answer(firstId, 'A progress question')]; props.foundation = true; props.readOnly = true;
  const generate = vi.spyOn(api, 'generateReaderFoundation'); await mount();
  expect(button('Ask about this step')).toBeUndefined();
  expect(renderer!.root.findAllByProps({ 'aria-label': 'Choose a foundation explanation' })).toHaveLength(0);
  act(() => button('Reading questions').props.onClick()); await flush();
  expect(text()).toContain('A progress question'); expect(button('Ask question')).toBeUndefined();
  expect(generate).not.toHaveBeenCalled();
});

it('labels progress snapshots and answers accurately in the shared artifact preview', async () => {
  const retained: ArtifactInfo = { path: sourceA.path, name: `${sourceA.source_id}.md`, why: 'The recorded explanation summary',
    exists: true, kind: 'text', mime: 'text/markdown', size: 80, mtime: 100, source: 'progress_snapshot', progress_source: sourceA,
    preview: '# Actual source\nThe retained explanation.' };
  vi.mocked(api.artifact).mockResolvedValue(retained);
  const preview = (path: string) => <QueryClientProvider client={client}><I18nProvider>
    <ArtifactModal sid="project-a" path={path} onClose={() => {}} />
  </I18nProvider></QueryClientProvider>;
  await act(async () => { renderer = create(preview(sourceA.path)); }); await flush();
  expect(renderer!.root.findByType('h2').children.join('')).toBe(sourceA.title);
  expect(text()).toContain('Saved progress explanation · question source'); expect(text()).not.toContain('Reviewer:');
  const saved = answer(firstId, 'The actual progress question'); vi.mocked(api.artifact).mockResolvedValue(saved);
  act(() => renderer!.update(preview(saved.path))); await flush();
  expect(text()).toContain('Reading question and answer'); expect(text()).not.toContain('Background explanation');
});

it('does not create source material without a saved explanation', async () => {
  props.card = undefined; await mount();
  expect(button('Ask about this step').props.disabled).toBe(true);
  await open();
  props.card = { ...card(), copy_revision: undefined }; act(() => renderer!.update(tree()));
  expect(button('Ask about this step').props.disabled).toBe(true);
  expect(api.mapQuestionSource).not.toHaveBeenCalled();
  expect(api.generateMapCopy).not.toHaveBeenCalled();
});

it.each([
  { ...sourceA, card_key: 'another-card' },
  { ...sourceA, task_id: 'another-task' },
  { ...sourceA, path: 'reader-progress/another-project/source.md' },
  sourceB,
])('rejects a different card, task, project or generation returned for the question (%j)', async ref => {
  vi.mocked(api.mapQuestionSource).mockResolvedValue(ref);
  await mount(); await open();
  expect(renderer!.root.findByProps({ role: 'alert' }).children.join('')).toContain('unavailable or has changed');
  expect(renderer!.root.findAllByProps({ 'data-testid': 'progress-questions' })).toHaveLength(0);
  expect(api.generateMapCopy).not.toHaveBeenCalled();
});

it.each([
  new Error('Network lost'),
  new ApiError('Unavailable', 422, 'POST', '/map-question-source', 'reader_source_unavailable'),
])('shows an actionable source failure without generating or dispatching anything (%s)', async error => {
  vi.mocked(api.mapQuestionSource).mockRejectedValueOnce(error).mockResolvedValueOnce(sourceA);
  const generate = vi.spyOn(api, 'generateReaderFoundation');
  await mount(); await open();
  expect(renderer!.root.findByProps({ role: 'alert' }).children.join('')).toContain('No question was opened');
  expect(generate).not.toHaveBeenCalled();
  expect(api.generateMapCopy).not.toHaveBeenCalled();
  expect(api.messageStream).not.toHaveBeenCalled();
  await open();
  expect(dialog().props['data-progress-source-id']).toBe(sourceA.source_id);
  expect(api.mapQuestionSource).toHaveBeenCalledTimes(2);
});

it.each(['project', 'locale', 'card', 'task', 'generation', 'preview', 'foundation', 'unmount'] as const)(
  'ignores a late source response after the %s context changes', async change => {
    vi.stubGlobal('window', { location: { search: '?reader_preview=question-foundation' } });
    selectFoundation(client, props.sid, 'en-US', firstId);
    const pending = deferred<ProgressSourceRef>();
    vi.mocked(api.mapQuestionSource).mockReturnValue(pending.promise);
    await mount(); await open();
    expect(button('Ask about this step').props.disabled).toBe(true);
    expect(api.mapQuestionSource).toHaveBeenCalledExactlyOnceWith('project-a', {
      card_key: 'task-a', task_id: 'task-a', locale: 'en-US', preview: 'question-foundation', foundation_id: firstId,
    });
    act(() => {
      if (change === 'locale') localeControl.setLocale('zh-CN');
      else if (change === 'unmount') { renderer!.unmount(); renderer = undefined; }
      else {
        if (change === 'project') props.sid = 'project-b';
        if (change === 'card') props.cardKey = 'another-card';
        if (change === 'task') props.taskId = 'another-task';
        if (change === 'generation') props.card = card(sourceB);
        if (change === 'preview') vi.stubGlobal('window', { location: { search: '?reader_preview=learning-path' } });
        if (change === 'foundation') selectFoundation(client, props.sid, 'en-US', secondId);
        renderer!.update(tree());
      }
    });
    await flush();
    await act(async () => pending.resolve(sourceA)); await flush();
    if (renderer) expect(renderer.root.findAllByProps({ 'data-testid': 'progress-questions' })).toHaveLength(0);
    expect(api.generateMapCopy).not.toHaveBeenCalled();
  },
);

it('uses the explicitly opened reader context rather than the background preview or foundation selection', async () => {
  vi.stubGlobal('window', { location: { search: '?reader_preview=learning-path' } });
  selectFoundation(client, props.sid, 'en-US', secondId);
  props.context = { locale: 'en-US', preview: 'question-foundation', foundationId: firstId };
  await mount(); await open();
  expect(api.mapQuestionSource).toHaveBeenCalledExactlyOnceWith('project-a', {
    card_key: 'task-a', task_id: 'task-a', locale: 'en-US', preview: 'question-foundation', foundation_id: firstId,
  });
  expect(dialog().props['data-progress-source-id']).toBe(sourceA.source_id);
});

it('does not let an earlier card response replace the last explicitly requested question', async () => {
  const first = deferred<ProgressSourceRef>(), last = deferred<ProgressSourceRef>();
  vi.mocked(api.mapQuestionSource).mockReturnValueOnce(first.promise).mockReturnValueOnce(last.promise);
  await act(async () => { renderer = create(<QueryClientProvider client={client}><I18nProvider>
    <ProgressQuestionsProvider sid={props.sid}>
      <ProgressQuestionButton card={card()} cardKey="first" taskId="first" />
      <ProgressQuestionButton card={card(sourceB)} cardKey="last" taskId="last" />
    </ProgressQuestionsProvider>
  </I18nProvider></QueryClientProvider>); });
  const trigger = (key: string) => renderer!.root.findAllByType('button').find(node => node.props['data-progress-question-card'] === key)!;
  act(() => trigger('first').props.onClick());
  act(() => trigger('last').props.onClick());
  await act(async () => last.resolve({ ...sourceB, card_key: 'last', task_id: 'last' })); await flush();
  expect(dialog().props['data-reader-card']).toBe('last');
  await act(async () => first.resolve({ ...sourceA, card_key: 'first', task_id: 'first' })); await flush();
  expect(dialog().props['data-reader-card']).toBe('last');
});
