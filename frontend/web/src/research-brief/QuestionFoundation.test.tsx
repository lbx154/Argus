import { useState, type ComponentProps, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '../../../core/src/http';
import { api, type ArtifactInfo } from '../api';
import { ArtifactModal } from '../components/ArtifactModal';
import { Modal } from '../components/Modal';
import { MarkdownContent } from '../components/MarkdownContent';
import { I18nProvider, useI18n } from '../i18n';
import { QuestionFoundation } from './QuestionFoundation';
import { foundationChoice, foundationListKey, selectFoundation } from './foundation';

vi.mock('../components/Modal', () => ({
  Modal: ({ open, children }: { open: boolean; children: ReactNode }) => open ? <div role="dialog">{children}</div> : null,
  ModalHeader: ({ title }: { title: string }) => <h2>{title}</h2>,
}));

const firstId = '11111111-1111-4111-8111-111111111111';
const nextId = '22222222-2222-4222-8222-222222222222';
function note(id: string, question: string, locale: 'en-US' | 'zh-CN' = 'en-US', state: 'complete' | 'failed' | 'generating' = 'complete'): ArtifactInfo {
  return { path: `research/foundations/${id}.md`, name: `${id}.md`, why: '', exists: state === 'complete',
    kind: 'text', mime: 'text/markdown', size: 80, mtime: 100, source: 'reader_foundation',
    preview: `# Saved foundation\n\n${question}\n\nA retained explanation with its own question.`,
    reader_foundation: { id, question, locale, state, version: 1, created_at: 100, source_task_id: 'aaaaaaaaaaaa' } };
}

function answer(id: string, question: string, parentId: string, rootId = parentId, state: 'complete' | 'failed' | 'generating' = 'complete'): ArtifactInfo {
  const value = note(id, question, 'en-US', state);
  return { ...value, reader_foundation: { ...value.reader_foundation!, kind: 'clarification', parent_id: parentId, root_id: rootId } };
}

type Props = Omit<ComponentProps<typeof QuestionFoundation>, 'onOpenArtifact'>;
let props: Props;
let renderer: ReactTestRenderer | undefined;
let client: QueryClient;
let rows: Record<string, ArtifactInfo[]>;
let localeControl: ReturnType<typeof useI18n>;
const opened = vi.fn();
function LocaleControl() { localeControl = useI18n(); return null; }
function Surface({ value, readArtifact = false }: { value: Props; readArtifact?: boolean }) {
  const [path, setPath] = useState<string | null>(null);
  return <><QuestionFoundation {...value} onOpenArtifact={file => { opened(file); setPath(file); }} />
    {readArtifact ? <ArtifactModal sid={value.sid} path={path} onSelectPath={file => { opened(file); setPath(file); }} onClose={() => setPath(null)} /> : null}</>;
}
const tree = (readArtifact = false) => <QueryClientProvider client={client}><I18nProvider>
  <LocaleControl /><Surface value={props} readArtifact={readArtifact} />
</I18nProvider></QueryClientProvider>;
const button = (label: string) => renderer!.root.findAllByType('button').find(node => node.children.includes(label))!;
const select = () => renderer!.root.findAllByType('select').find(node => ['Choose a foundation explanation', '选择基础说明'].includes(node.props['aria-label']))!;
const pageText = () => JSON.stringify(renderer!.toJSON());
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((accept, fail) => { resolve = accept; reject = fail; });
  return { promise, resolve, reject };
}
async function flush(ms = 25) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
async function mount(readArtifact = false) { await act(async () => { renderer = create(tree(readArtifact)); }); await flush(); }
function edit(question: string) {
  act(() => button('Understand the foundations').props.onClick());
  act(() => renderer!.root.findByType('textarea').props.onChange({ target: { value: question } }));
}

beforeEach(() => {
  vi.useFakeTimers();
  const storage = new Map<string, string>([['argus.locale', 'en']]);
  vi.stubGlobal('localStorage', { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value) });
  vi.stubGlobal('window', { location: { search: '?reader_preview=question-foundation' } });
  vi.stubGlobal('document', { documentElement: { lang: 'en' } });
  vi.stubGlobal('navigator', { language: 'en-US' });
  vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValueOnce(firstId).mockReturnValueOnce(nextId) });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false, gcTime: Infinity } } });
  props = { sid: 'project-a', objective: 'The original user question', taskId: 'aaaaaaaaaaaa', taskTitle: 'Original source task' };
  rows = { 'project-a': [], 'project-b': [] };
  vi.spyOn(api, 'artifacts').mockImplementation(async sid => rows[sid] ?? []);
  vi.spyOn(api, 'generateMapCopy').mockRejectedValue(new Error('Reading foundations must not invoke map generation'));
  vi.spyOn(api, 'messageStream').mockRejectedValue(new Error('Reading questions must not enter research dispatch'));
  opened.mockReset();
});
afterEach(() => {
  act(() => renderer?.unmount()); renderer = undefined;
  client.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();
});

it.each([false, true])('opening a saved artifact reads it without generating or submitting a follow-up (readOnly=%s)', async readOnly => {
  const saved = note('saved', 'What does this comparison mean?');
  rows['project-a'] = [saved]; props = { ...props, readOnly };
  const generate = vi.spyOn(api, 'generateReaderFoundation');
  const clarify = vi.spyOn(api, 'askReaderFoundation');
  const read = vi.spyOn(api, 'artifact').mockResolvedValue(saved);
  await mount(true);
  act(() => select().props.onChange({ target: { value: 'saved' } }));
  await flush();
  act(() => button('Read foundations').props.onClick());
  await flush();
  expect(read).toHaveBeenCalledTimes(1);
  expect(read.mock.calls[0].slice(0, 2)).toEqual(['project-a', saved.path]);
  expect(vi.mocked(api.artifacts).mock.calls.every(call => call[2] === true)).toBe(true);
  expect(renderer!.root.findAllByType(MarkdownContent).map(node => node.props.children)).toContain(saved.preview);
  if (readOnly) {
    expect(button('Understand the foundations')).toBeUndefined();
    expect(button('Ask about these foundations')).toBeUndefined();
  } else {
    act(() => button('Ask about these foundations').props.onClick());
    expect(renderer!.root.findByType('textarea').props.value).toBe('');
    expect(pageText()).toContain(saved.reader_foundation!.question);
    expect(button('Ask question').props.disabled).toBe(true);
  }
  expect(generate).not.toHaveBeenCalled();
  expect(clarify).not.toHaveBeenCalled();
  expect(api.messageStream).not.toHaveBeenCalled();
  expect(api.generateMapCopy).not.toHaveBeenCalled();
  expect(crypto.randomUUID).not.toHaveBeenCalled();
});

it('asks about the pinned document, retains a later root choice, and reads the answer without research dispatch', async () => {
  const a = note('root-a', 'First saved question'), b = note('root-b', 'Another saved question');
  rows['project-a'] = [a, b];
  const pending = deferred<ArtifactInfo>();
  const clarify = vi.spyOn(api, 'askReaderFoundation').mockReturnValue(pending.promise);
  const generate = vi.spyOn(api, 'generateReaderFoundation');
  await mount();
  act(() => select().props.onChange({ target: { value: 'root-a' } })); await flush();
  act(() => button('Ask about these foundations').props.onClick());
  act(() => renderer!.root.findByType('textarea').props.onChange({ target: { value: '  How does the changed example use this rule?  ' } }));
  act(() => select().props.onChange({ target: { value: 'root-b' } })); await flush();
  expect(pageText()).toContain('First saved question');
  act(() => button('Ask question').props.onClick()); await flush();
  expect(clarify.mock.calls[0].slice(0, 3)).toEqual(['project-a', 'root-a', {
    request_id: firstId, question: 'How does the changed example use this rule?', locale: 'en-US',
  }]);
  const saved = answer(firstId, 'How does the changed example use this rule?', 'root-a');
  rows['project-a'] = [a, b, saved];
  await act(async () => { pending.resolve(saved); await vi.advanceTimersByTimeAsync(25); });
  expect(select().props.value).toBe('root-b');
  expect(renderer!.root.findAllByType('option').map(node => node.props.value)).toEqual(['', 'root-a', 'root-b']);
  expect(button('Read answer')).toBeUndefined();
  act(() => select().props.onChange({ target: { value: 'root-a' } })); await flush();
  act(() => button('Read answer').props.onClick());
  expect(opened).toHaveBeenLastCalledWith(saved.path);
  expect(clarify).toHaveBeenCalledTimes(1);
  expect(generate).not.toHaveBeenCalled();
  expect(api.messageStream).not.toHaveBeenCalled();
  expect(api.generateMapCopy).not.toHaveBeenCalled();
});

it('retains a follow-up parent and question across an unknown result and a fresh query client', async () => {
  const root = note('root', 'Root question');
  const previous = answer('previous-answer', 'Earlier question', 'root');
  rows['project-a'] = [root, previous];
  const first = deferred<ArtifactInfo>(), retry = deferred<ArtifactInfo>();
  const clarify = vi.spyOn(api, 'askReaderFoundation').mockReturnValueOnce(first.promise).mockReturnValueOnce(retry.promise);
  const generate = vi.spyOn(api, 'generateReaderFoundation');
  await mount();
  act(() => select().props.onChange({ target: { value: 'root' } })); await flush();
  act(() => button('Ask a follow-up').props.onClick());
  act(() => renderer!.root.findByType('textarea').props.onChange({ target: { value: 'Why can this operation be repeated?' } }));
  act(() => button('Ask question').props.onClick()); await flush();
  await act(async () => { first.reject(new TypeError('Failed to fetch')); await vi.advanceTimersByTimeAsync(25); });
  expect(pageText()).toContain('The result of this request is unconfirmed.');
  act(() => renderer!.unmount()); client.clear();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
  await mount();
  expect(clarify).toHaveBeenCalledTimes(1);
  expect(button('Ask about these foundations').props.disabled).toBe(true);
  act(() => button('Retry this same request').props.onClick()); await flush();
  expect(clarify.mock.calls[1].slice(0, 3)).toEqual(clarify.mock.calls[0].slice(0, 3));
  expect(clarify.mock.calls[1][1]).toBe('previous-answer');
  const saved = answer(firstId, 'Why can this operation be repeated?', 'previous-answer', 'root');
  rows['project-a'] = [root, previous, saved];
  await act(async () => { retry.resolve(saved); await vi.advanceTimersByTimeAsync(25); });
  expect(select().props.value).toBe('root');
  expect(renderer!.root.findAllByProps({ 'data-reading-question-id': firstId })).toHaveLength(1);
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
  expect(generate).not.toHaveBeenCalled();
  expect(api.messageStream).not.toHaveBeenCalled();
});

it('edits a confirmed failed clarification without turning it into another root foundation', async () => {
  const root = note('root', 'Root question');
  const failed = answer('failed-answer', 'Please explain the missing operation.', 'root', 'root', 'failed');
  rows['project-a'] = [root, failed];
  const pending = deferred<ArtifactInfo>();
  const clarify = vi.spyOn(api, 'askReaderFoundation').mockReturnValue(pending.promise);
  const generate = vi.spyOn(api, 'generateReaderFoundation');
  await mount();
  act(() => select().props.onChange({ target: { value: 'root' } })); await flush();
  act(() => button('Edit this question').props.onClick());
  expect(renderer!.root.findByType('textarea').props.value).toBe(failed.reader_foundation!.question);
  act(() => button('Ask question').props.onClick()); await flush();
  expect(clarify.mock.calls[0].slice(0, 3)).toEqual(['project-a', 'root', {
    request_id: firstId, question: failed.reader_foundation!.question, locale: 'en-US',
  }]);
  const saved = answer(firstId, failed.reader_foundation!.question, 'root'); rows['project-a'] = [root, failed, saved];
  await act(async () => { pending.resolve(saved); await vi.advanceTimersByTimeAsync(25); });
  expect(select().props.value).toBe('root');
  expect(generate).not.toHaveBeenCalled();
  expect(api.messageStream).not.toHaveBeenCalled();
});

it('opens the saved source of an answer through the existing authenticated artifact reader', async () => {
  const root = note('root', 'Root question');
  const saved = answer('answer', 'What did the source mean?', 'root');
  saved.reader_foundation!.sources = [{ id: 'root', path: root.path, title: 'Root question' }];
  saved.preview = `# A saved answer\n\nThe explanation refers to [the saved source](${root.path}).`;
  rows['project-a'] = [root, saved];
  const read = vi.spyOn(api, 'artifact').mockImplementation(async (_sid, path) => path === root.path ? root : saved);
  const clarify = vi.spyOn(api, 'askReaderFoundation');
  const generate = vi.spyOn(api, 'generateReaderFoundation');
  await mount(true);
  act(() => select().props.onChange({ target: { value: 'root' } })); await flush();
  act(() => button('Read answer').props.onClick()); await flush();
  const sourceLink = renderer!.root.findByProps({ 'data-artifact-path': root.path });
  const preventDefault = vi.fn();
  act(() => sourceLink.props.onClick({ preventDefault })); await flush();
  expect(preventDefault).toHaveBeenCalledTimes(1);
  expect(read.mock.calls.map(call => call.slice(0, 2))).toEqual([['project-a', saved.path], ['project-a', root.path]]);
  expect(renderer!.root.findAllByType(MarkdownContent).map(node => node.props.children)).toContain(root.preview);
  expect(clarify).not.toHaveBeenCalled();
  expect(generate).not.toHaveBeenCalled();
  expect(api.messageStream).not.toHaveBeenCalled();
});

it('recovers a confirmed unaccepted source rejection after reload and explicitly chooses a new source', async () => {
  const lost = note('lost', 'Previously available source'), other = note('other', 'Available source');
  rows['project-a'] = [lost, other];
  const initial = deferred<ArtifactInfo>(), later = deferred<ArtifactInfo>();
  const clarify = vi.spyOn(api, 'askReaderFoundation').mockReturnValueOnce(initial.promise).mockReturnValueOnce(later.promise);
  const generate = vi.spyOn(api, 'generateReaderFoundation');
  await mount();
  act(() => select().props.onChange({ target: { value: 'lost' } })); await flush();
  act(() => button('Ask about these foundations').props.onClick());
  act(() => renderer!.root.findByType('textarea').props.onChange({ target: { value: 'Explain the operation used in this reading.' } }));
  act(() => button('Ask question').props.onClick()); await flush();
  rows['project-a'] = [{ ...lost, exists: false }, other];
  await act(async () => { initial.reject(new ApiError('Source unavailable', 422, 'POST', '/reading/question', 'reader_source_unavailable')); await vi.advanceTimersByTimeAsync(25); });
  expect(pageText()).toContain('this question was not started');
  expect(pageText()).not.toContain('The result of this request is unconfirmed.');
  expect(button('Retry this same request')).toBeUndefined();
  act(() => renderer!.unmount()); client.clear();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
  await mount();
  expect(pageText()).toContain('this question was not started');
  expect(clarify).toHaveBeenCalledTimes(1);
  act(() => button('Edit question and source').props.onClick()); await flush();
  expect(renderer!.root.findByType('textarea').props.value).toBe('Explain the operation used in this reading.');
  expect(button('Ask question').props.disabled).toBe(true);
  act(() => renderer!.root.findByProps({ 'aria-label': 'Reading source' }).props.onChange({ target: { value: 'other' } }));
  await flush();
  expect(select().props.value).toBe('other');
  act(() => button('Ask question').props.onClick()); await flush();
  expect(clarify.mock.calls[1].slice(0, 3)).toEqual(['project-a', 'other', {
    request_id: nextId, question: 'Explain the operation used in this reading.', locale: 'en-US',
  }]);
  const saved = answer(nextId, 'Explain the operation used in this reading.', 'other');
  rows['project-a'] = [{ ...lost, exists: false }, other, saved];
  await act(async () => { later.resolve(saved); await vi.advanceTimersByTimeAsync(25); });
  expect(button('Read answer')).toBeDefined();
  expect(generate).not.toHaveBeenCalled();
  expect(api.messageStream).not.toHaveBeenCalled();
});

it('does not treat an ordinary 422 generation error without a rejection code as an unaccepted request', async () => {
  rows['project-a'] = [note('root', 'Root question')];
  vi.spyOn(api, 'askReaderFoundation').mockRejectedValue(new ApiError('Generation content invalid', 422, 'POST', '/reading/question'));
  await mount();
  act(() => select().props.onChange({ target: { value: 'root' } })); await flush();
  act(() => button('Ask about these foundations').props.onClick());
  act(() => renderer!.root.findByType('textarea').props.onChange({ target: { value: 'Explain the operation.' } }));
  act(() => button('Ask question').props.onClick()); await flush();
  expect(pageText()).toContain('The result of this request is unconfirmed.');
  expect(button('Retry this same request')).toBeDefined();
  expect(button('Edit question and source')).toBeUndefined();
});

it('retains the original rejected question when editing is cancelled or refreshed before another explicit submission', async () => {
  rows['project-a'] = [note('root', 'Root question')];
  const clarify = vi.spyOn(api, 'askReaderFoundation').mockRejectedValue(new ApiError('Source unavailable', 422, 'POST', '/reading/question', 'reader_source_unavailable'));
  await mount();
  act(() => select().props.onChange({ target: { value: 'root' } })); await flush();
  act(() => button('Ask about these foundations').props.onClick());
  act(() => renderer!.root.findByType('textarea').props.onChange({ target: { value: 'Keep my submitted question about the operation.' } }));
  act(() => button('Ask question').props.onClick()); await flush();
  act(() => button('Edit question and source').props.onClick()); await flush();
  act(() => renderer!.root.findByType(Modal).props.onClose()); await flush();
  expect(client.getQueryData(['reader-foundation-request', 'project-a', 'en-US'])).toMatchObject({
    id: firstId, rejected: 'reader_source_unavailable', draft: { parentId: 'root', question: 'Keep my submitted question about the operation.' },
  });
  act(() => button('Edit question and source').props.onClick()); await flush();
  act(() => renderer!.unmount()); client.clear();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
  await mount();
  act(() => button('Edit question and source').props.onClick()); await flush();
  expect(renderer!.root.findByType('textarea').props.value).toBe('Keep my submitted question about the operation.');
  expect(renderer!.root.findByProps({ 'aria-label': 'Reading source' }).props.value).toBe('root');
  expect(clarify).toHaveBeenCalledTimes(1);
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
});

it.each(['project', 'locale'] as const)('keeps a late source rejection in its original %s request scope', async dimension => {
  rows['project-a'] = [note('root', 'Root question')];
  const pending = deferred<ArtifactInfo>();
  vi.spyOn(api, 'askReaderFoundation').mockReturnValue(pending.promise);
  await mount();
  act(() => select().props.onChange({ target: { value: 'root' } })); await flush();
  act(() => button('Ask about these foundations').props.onClick());
  act(() => renderer!.root.findByType('textarea').props.onChange({ target: { value: 'Explain the operation.' } }));
  act(() => button('Ask question').props.onClick()); await flush();
  const destinationSid = dimension === 'project' ? 'project-b' : 'project-a';
  const destinationLanguage = dimension === 'locale' ? 'zh-CN' : 'en-US';
  if (dimension === 'project') { props = { ...props, sid: 'project-b' }; act(() => renderer!.update(tree())); }
  else act(() => localeControl.setLocale('zh-CN'));
  await flush();
  await act(async () => { pending.reject(new ApiError('Source unavailable', 422, 'POST', '/reading/question', 'reader_source_unavailable')); await vi.advanceTimersByTimeAsync(25); });
  expect(client.getQueryData(['reader-foundation-request', 'project-a', 'en-US'])).toMatchObject({ id: firstId, rejected: 'reader_source_unavailable' });
  expect(client.getQueryData(['reader-foundation-request', destinationSid, destinationLanguage])).toBeNull();
  expect(pageText()).not.toContain('this question was not started');
  expect(pageText()).not.toContain('这次追问未发起');
});

it.each(['project', 'locale'] as const)('closes an unsent reading question when its %s changes', async dimension => {
  rows['project-a'] = [note('root', 'Root question')];
  const clarify = vi.spyOn(api, 'askReaderFoundation');
  await mount();
  act(() => select().props.onChange({ target: { value: 'root' } })); await flush();
  act(() => button('Ask about these foundations').props.onClick());
  if (dimension === 'project') { props = { ...props, sid: 'project-b' }; act(() => renderer!.update(tree())); }
  else act(() => localeControl.setLocale('zh-CN'));
  await flush();
  expect(renderer!.root.findAllByType('textarea')).toHaveLength(0);
  expect(clarify).not.toHaveBeenCalled();
  expect(api.messageStream).not.toHaveBeenCalled();
});

it('generates only on submit, retains draft source through task changes, and shares pending/completed work across remounts', async () => {
  let finish!: (value: ArtifactInfo) => void;
  const generate = vi.spyOn(api, 'generateReaderFoundation').mockReturnValue(new Promise(resolve => { finish = resolve; }));
  await mount();
  edit('  Explain the original comparison from the beginning.  ');
  expect(generate).not.toHaveBeenCalled();
  expect(crypto.randomUUID).not.toHaveBeenCalled();
  props = { ...props, objective: 'A later objective', taskId: 'bbbbbbbbbbbb', taskTitle: 'Later task' };
  act(() => renderer!.update(tree()));
  expect(renderer!.root.findByType('textarea').props.value).toBe('  Explain the original comparison from the beginning.  ');
  expect(JSON.stringify(renderer!.toJSON())).toContain('Original source task');
  act(() => button('Generate and save foundations').props.onClick());
  await flush();
  expect(generate).toHaveBeenCalledTimes(1);
  expect(generate.mock.calls[0][1]).toEqual({ request_id: firstId, question: 'Explain the original comparison from the beginning.', locale: 'en-US', source_task_id: 'aaaaaaaaaaaa' });
  await act(async () => { generate.mock.calls[0][2]?.('writing'); await vi.advanceTimersByTimeAsync(25); });
  act(() => renderer!.unmount());
  await mount();
  expect(button('Understand the foundations').props.disabled).toBe(true);
  expect(renderer!.root.findByProps({ 'data-explanation-phase': 'writing' })).toBeTruthy();
  await flush(10_000);
  expect(generate).toHaveBeenCalledTimes(1);
  const saved = note(firstId, 'Explain the original comparison from the beginning.');
  rows['project-a'] = [saved];
  await act(async () => { finish(saved); await vi.advanceTimersByTimeAsync(25); });
  expect(select().props.value).toBe(firstId);
  expect(renderer!.root.findAll(node => node.props['data-explanation-phase'] !== undefined)).toHaveLength(0);
  act(() => renderer!.unmount());
  await mount();
  expect(select().props.value).toBe(firstId);
  expect(generate).toHaveBeenCalledTimes(1);
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
});

it('recovers a server-retained pending record after a fresh page cache without submitting another generation', async () => {
  rows['project-a'] = [note(firstId, 'A question already being prepared', 'en-US', 'generating')];
  const generate = vi.spyOn(api, 'generateReaderFoundation');
  await mount();
  expect(button('Understand the foundations').props.disabled).toBe(true);
  expect(JSON.stringify(renderer!.toJSON())).toContain('An explanation is still being prepared.');
  act(() => renderer!.unmount()); client.clear();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  await mount();
  expect(button('Understand the foundations').props.disabled).toBe(true);
  rows['project-a'] = [note(firstId, 'A question already being prepared')];
  await act(async () => { await client.refetchQueries({ queryKey: foundationListKey('project-a') }); });
  await flush();
  expect(select().findAllByType('option').find(node => node.props.value === firstId)?.props.disabled).toBe(false);
  expect(button('Understand the foundations').props.disabled).toBe(false);
  expect(generate).not.toHaveBeenCalled();
  expect(crypto.randomUUID).not.toHaveBeenCalled();
});

it('does not replace an explicitly selected question B when an earlier creation A completes', async () => {
  const existing = note('question-b', 'A different saved question');
  rows['project-a'] = [existing];
  let finish!: (value: ArtifactInfo) => void;
  const generate = vi.spyOn(api, 'generateReaderFoundation').mockReturnValue(new Promise(resolve => { finish = resolve; }));
  await mount(); edit('A new question A');
  act(() => button('Generate and save foundations').props.onClick()); await flush();
  act(() => select().props.onChange({ target: { value: 'question-b' } })); await flush();
  const created = note(firstId, 'A new question A'); rows['project-a'] = [existing, created];
  await act(async () => { finish(created); await vi.advanceTimersByTimeAsync(25); });
  expect(select().props.value).toBe('question-b');
  expect(select().findAllByType('option').map(node => node.props.value)).toContain(firstId);
  act(() => button('Read foundations').props.onClick());
  expect(opened).toHaveBeenLastCalledWith(existing.path);
  expect(generate).toHaveBeenCalledTimes(1);
});

it('retains the reader’s choice B after a page reload and an explicit retry completes the earlier request A', async () => {
  const existing = note('question-b', 'The question the reader chose while A was pending');
  rows['project-a'] = [existing];
  const initial = deferred<ArtifactInfo>(), retry = deferred<ArtifactInfo>();
  const generate = vi.spyOn(api, 'generateReaderFoundation')
    .mockReturnValueOnce(initial.promise).mockReturnValueOnce(retry.promise);
  await mount(); edit('A new question A');
  act(() => button('Generate and save foundations').props.onClick()); await flush();
  act(() => select().props.onChange({ target: { value: 'question-b' } })); await flush();
  await act(async () => { initial.reject(new TypeError('Failed to fetch')); await vi.advanceTimersByTimeAsync(25); });
  expect(pageText()).toContain('The result of this request is unconfirmed.');
  expect(select().props.value).toBe('question-b');
  act(() => renderer!.unmount()); client.clear();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  await mount();
  expect(select().props.value).toBe('question-b');
  expect(generate).toHaveBeenCalledTimes(1);
  act(() => button('Retry this same request').props.onClick()); await flush();
  const saved = note(firstId, 'A new question A'); rows['project-a'] = [existing, saved];
  await act(async () => { retry.resolve(saved); await vi.advanceTimersByTimeAsync(25); });
  expect(generate.mock.calls[1][1]).toEqual(generate.mock.calls[0][1]);
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
  expect(select().props.value).toBe('question-b');
  act(() => button('Read foundations').props.onClick());
  expect(opened).toHaveBeenLastCalledWith(existing.path);
});

it.each(['project', 'locale'] as const)('completion stays with its original %s when the visible context changes', async dimension => {
  const destinationSid = dimension === 'project' ? 'project-b' : 'project-a';
  const destinationLocale = dimension === 'locale' ? 'zh-CN' : 'en-US';
  const destination = note('destination', 'Already selected in another context', destinationLocale);
  rows[destinationSid] = [destination];
  selectFoundation(client, destinationSid, destinationLocale, 'destination');
  let finish!: (value: ArtifactInfo) => void;
  const generate = vi.spyOn(api, 'generateReaderFoundation').mockReturnValue(new Promise(resolve => { finish = resolve; }));
  await mount(); edit('Original English question');
  act(() => button('Generate and save foundations').props.onClick()); await flush();
  if (dimension === 'project') { props = { ...props, sid: 'project-b' }; act(() => renderer!.update(tree())); }
  else act(() => localeControl.setLocale('zh-CN'));
  await flush();
  expect(select().props.value).toBe('destination');
  const created = note(firstId, 'Original English question');
  rows['project-a'] = [...rows['project-a'], created];
  await act(async () => { finish(created); await vi.advanceTimersByTimeAsync(25); });
  expect(select().props.value).toBe('destination');
  expect(foundationChoice(client, 'project-a', 'en-US').id).toBe(firstId);
  expect(foundationChoice(client, destinationSid, destinationLocale).id).toBe('destination');
  expect(generate.mock.calls[0][0]).toBe('project-a');
  expect(generate.mock.calls[0][1].locale).toBe('en-US');
  expect(generate).toHaveBeenCalledTimes(1);
});

it('retains failed records and creates a new UUID only after an explicit edited submission', async () => {
  let fail!: (error: Error) => void, finish!: (value: ArtifactInfo) => void;
  const generate = vi.spyOn(api, 'generateReaderFoundation')
    .mockReturnValueOnce(new Promise((_resolve, reject) => { fail = reject; }))
    .mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  await mount(); edit('A question that still needs an explanation');
  act(() => button('Generate and save foundations').props.onClick()); await flush();
  const failed = note(firstId, 'A question that still needs an explanation', 'en-US', 'failed');
  rows['project-a'] = [failed];
  await act(async () => { fail(new Error('The bounded request timed out')); await vi.advanceTimersByTimeAsync(25); });
  expect(JSON.stringify(renderer!.toJSON())).toContain('The failed request is retained');
  props = { ...props, taskId: 'bbbbbbbbbbbb', taskTitle: 'Different current task' };
  act(() => renderer!.unmount()); await mount(); await flush(10_000);
  expect(client.getQueryData<ArtifactInfo[]>(foundationListKey('project-a'))?.[0].reader_foundation?.state).toBe('failed');
  expect(select().findAllByType('option').find(node => node.props.value === firstId)?.props.disabled).toBe(true);
  expect(generate).toHaveBeenCalledTimes(1);
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
  act(() => button('Edit this question').props.onClick());
  expect(renderer!.root.findByType('textarea').props.value).toBe(failed.reader_foundation!.question);
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
  act(() => button('Generate and save foundations').props.onClick()); await flush();
  expect(generate).toHaveBeenCalledTimes(2);
  expect(generate.mock.calls[1][1]).toEqual({ request_id: nextId, question: failed.reader_foundation!.question, locale: 'en-US', source_task_id: 'aaaaaaaaaaaa' });
  const saved = note(nextId, failed.reader_foundation!.question); rows['project-a'] = [failed, saved];
  await act(async () => { finish(saved); await vi.advanceTimersByTimeAsync(25); });
  expect(select().props.value).toBe(nextId);
  expect(client.getQueryData<ArtifactInfo[]>(foundationListKey('project-a'))?.map(item => item.reader_foundation?.id)).toEqual([firstId, nextId]);
  expect(crypto.randomUUID).toHaveBeenCalledTimes(2);
});

it('checks the saved result after transport loss and keeps a matching generating record pending', async () => {
  const submission = deferred<ArtifactInfo>(), refresh = deferred<ArtifactInfo[]>();
  const generate = vi.spyOn(api, 'generateReaderFoundation').mockReturnValueOnce(submission.promise);
  await mount(); edit('Explain the comparison and its prerequisites.');
  act(() => button('Generate and save foundations').props.onClick()); await flush();
  await act(async () => { generate.mock.calls[0][2]?.('writing'); await vi.advanceTimersByTimeAsync(25); });
  vi.mocked(api.artifacts).mockReturnValueOnce(refresh.promise);
  await act(async () => { submission.reject(new TypeError('Failed to fetch')); await vi.advanceTimersByTimeAsync(25); });
  expect(pageText()).toContain('Checking the saved result');
  expect(pageText()).not.toContain('The failed request is retained');
  expect(renderer!.root.findAll(node => node.props['data-explanation-phase'] !== undefined)).toHaveLength(0);
  expect(button('Understand the foundations').props.disabled).toBe(true);
  expect(button('Retry this same request')).toBeUndefined();
  const pending = note(firstId, 'Explain the comparison and its prerequisites.', 'en-US', 'generating');
  await act(async () => { refresh.resolve([pending]); await vi.advanceTimersByTimeAsync(25); });
  expect(pageText()).toContain('An explanation is still being prepared.');
  expect(pageText()).not.toContain('Checking the saved result');
  expect(pageText()).not.toContain('The failed request is retained');
  expect(pageText()).not.toContain('The result of this request is unconfirmed.');
  expect(button('Understand the foundations').props.disabled).toBe(true);
  expect(button('Retry this same request')).toBeUndefined();
  expect(select().findAllByType('option').find(node => node.props.value === firstId)?.props.disabled).toBe(true);
  expect(generate).toHaveBeenCalledTimes(1);
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
});

it.each(['empty', 'unrelated-failed'] as const)('does not infer this request failed from a stale %s list when refresh also fails, and reload only reads records', async cached => {
  if (cached === 'unrelated-failed') rows['project-a'] = [note('older-failure', 'An older unrelated question', 'en-US', 'failed')];
  const submission = deferred<ArtifactInfo>();
  const generate = vi.spyOn(api, 'generateReaderFoundation').mockReturnValueOnce(submission.promise);
  await mount(); edit('The new question whose result is not yet known');
  act(() => button('Generate and save foundations').props.onClick()); await flush();
  const reads = vi.mocked(api.artifacts);
  reads.mockRejectedValueOnce(new Error('Saved records temporarily unavailable'));
  await act(async () => { submission.reject(new TypeError('Failed to fetch')); await vi.advanceTimersByTimeAsync(25); });
  expect(pageText()).toContain('Saved explanations could not be loaded.');
  expect(pageText()).toContain('The result of this request is unconfirmed.');
  expect(pageText()).not.toContain('The failed request is retained');
  expect(button('Understand the foundations').props.disabled).toBe(true);
  if (cached === 'unrelated-failed') expect(button('Edit this question').props.disabled).toBe(true);
  const beforeRead = reads.mock.calls.length;
  rows['project-a'] = [note(firstId, 'The new question whose result is not yet known', 'en-US', 'generating')];
  act(() => button('Reload records').props.onClick()); await flush();
  expect(reads).toHaveBeenCalledTimes(beforeRead + 1);
  expect(pageText()).not.toContain('Saved explanations could not be loaded.');
  expect(pageText()).toContain('An explanation is still being prepared.');
  expect(button('Understand the foundations').props.disabled).toBe(true);
  expect(generate).toHaveBeenCalledTimes(1);
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
});

it.each(['generating', 'failed'] as const)('reload retains the original request and an explicit same-ID retry accepts a duplicate %s result', async duplicateState => {
  const question = 'What are the objects, the comparison, and what would establish equality?';
  const duplicate = note(firstId, question, 'en-US', duplicateState);
  const failed = note(firstId, question, 'en-US', 'failed');
  const saved = note(nextId, question);
  const generate = vi.spyOn(api, 'generateReaderFoundation')
    .mockRejectedValueOnce(new TypeError('Failed to fetch'))
    .mockResolvedValueOnce(duplicate).mockResolvedValueOnce(saved);
  await mount(); edit(`  ${question}  `);
  act(() => button('Generate and save foundations').props.onClick()); await flush();
  expect(pageText()).toContain('The result of this request is unconfirmed.');
  expect(pageText()).not.toContain('The failed request is retained');
  expect(button('Understand the foundations').props.disabled).toBe(true);
  props = { ...props, objective: 'A different objective', taskId: 'bbbbbbbbbbbb', taskTitle: 'A different task after reload' };
  act(() => renderer!.unmount()); client.clear();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  await mount(); await flush(10_000);
  expect(pageText()).toContain('The result of this request is unconfirmed.');
  expect(button('Understand the foundations').props.disabled).toBe(true);
  expect(generate).toHaveBeenCalledTimes(1);
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
  rows['project-a'] = [duplicate];
  act(() => button('Retry this same request').props.onClick()); await flush();
  expect(generate).toHaveBeenCalledTimes(2);
  expect(generate.mock.calls[1][0]).toBe('project-a');
  expect(generate.mock.calls[1][1]).toEqual(generate.mock.calls[0][1]);
  expect(generate.mock.calls[1][1]).toEqual({ request_id: firstId, question, locale: 'en-US', source_task_id: 'aaaaaaaaaaaa' });
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
  expect(button('Read foundations').props.disabled).toBe(true);
  if (duplicateState === 'generating') {
    expect(pageText()).toContain('An explanation is still being prepared.');
    expect(pageText()).not.toContain('The failed request is retained');
    expect(button('Understand the foundations').props.disabled).toBe(true);
    rows['project-a'] = [failed];
    await act(async () => { await client.refetchQueries({ queryKey: foundationListKey('project-a') }); });
    await flush(); await flush();
  }
  expect(pageText()).toContain('The failed request is retained');
  expect(button('Retry this same request')).toBeUndefined();
  expect(button('Understand the foundations').props.disabled).toBe(false);
  act(() => button('Edit this question').props.onClick());
  expect(renderer!.root.findByType('textarea').props.value).toBe(question);
  expect(generate).toHaveBeenCalledTimes(2);
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
  rows['project-a'] = [failed, saved];
  act(() => button('Generate and save foundations').props.onClick()); await flush();
  expect(generate).toHaveBeenCalledTimes(3);
  expect(generate.mock.calls[2][1]).toEqual({ request_id: nextId, question, locale: 'en-US', source_task_id: 'aaaaaaaaaaaa' });
  expect(crypto.randomUUID).toHaveBeenCalledTimes(2);
  expect(select().props.value).toBe(nextId);
});

it.each(['complete', 'failed'] as const)('does not let a duplicate generating response overwrite a newer %s manifest read before the mutation settles', async finalState => {
  const question = 'Explain why this comparison is the research target.';
  const duplicate = deferred<ArtifactInfo>(), refresh = deferred<ArtifactInfo[]>();
  const generate = vi.spyOn(api, 'generateReaderFoundation')
    .mockRejectedValueOnce(new TypeError('Failed to fetch')).mockReturnValueOnce(duplicate.promise);
  await mount(); edit(question);
  act(() => button('Generate and save foundations').props.onClick()); await flush();
  expect(pageText()).toContain('The result of this request is unconfirmed.');
  act(() => button('Retry this same request').props.onClick()); await flush();
  vi.mocked(api.artifacts).mockReturnValueOnce(refresh.promise);
  await act(async () => { duplicate.resolve(note(firstId, question, 'en-US', 'generating')); await vi.advanceTimersByTimeAsync(25); });
  expect(pageText()).toContain('Checking the saved result');
  const terminal = note(firstId, question, 'en-US', finalState);
  await act(async () => { refresh.resolve([terminal]); await vi.advanceTimersByTimeAsync(25); });
  expect(client.getQueryData<ArtifactInfo[]>(foundationListKey('project-a'))).toEqual([terminal]);
  expect(select().findAllByType('option').find(node => node.props.value === firstId)?.props.disabled).toBe(finalState !== 'complete');
  expect(pageText()).not.toContain('An explanation is still being prepared.');
  expect(pageText()).not.toContain('The result of this request is unconfirmed.');
  expect(button('Understand the foundations').props.disabled).toBe(false);
  expect(button('Retry this same request')).toBeUndefined();
  expect(generate).toHaveBeenCalledTimes(2);
  expect(generate.mock.calls[1][1]).toEqual(generate.mock.calls[0][1]);
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
});

it.each(['project', 'locale'] as const)('recovering one unconfirmed submission preserves the separate request in another %s', async dimension => {
  const generate = vi.spyOn(api, 'generateReaderFoundation').mockRejectedValue(new TypeError('Failed to fetch'));
  const changeContext = async (destination: boolean) => {
    if (dimension === 'project') {
      props = { ...props, sid: destination ? 'project-b' : 'project-a' };
      act(() => renderer!.update(tree()));
    } else act(() => localeControl.setLocale(destination ? 'zh-CN' : 'en'));
    await flush();
  };
  await mount(); edit('Original English question');
  act(() => button('Generate and save foundations').props.onClick()); await flush();
  expect(pageText()).toContain('The result of this request is unconfirmed.');
  await changeContext(true);
  const understand = dimension === 'locale' ? '从基础理解' : 'Understand the foundations';
  const submit = dimension === 'locale' ? '生成并保存基础说明' : 'Generate and save foundations';
  expect(button(understand).props.disabled).toBe(false);
  expect(pageText()).not.toContain('The result of this request is unconfirmed.');
  expect(pageText()).not.toContain('这次请求的结果尚未确认');
  act(() => button(understand).props.onClick());
  act(() => renderer!.root.findByType('textarea').props.onChange({ target: { value: 'A separate question in the other context' } }));
  act(() => button(submit).props.onClick()); await flush();
  expect(generate).toHaveBeenCalledTimes(2);
  expect(generate.mock.calls[1][0]).toBe(dimension === 'project' ? 'project-b' : 'project-a');
  expect(generate.mock.calls[1][1]).toMatchObject({ request_id: nextId, locale: dimension === 'locale' ? 'zh-CN' : 'en-US' });
  await changeContext(false);
  act(() => renderer!.unmount()); client.clear();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  await mount();
  expect(generate).toHaveBeenCalledTimes(2);
  expect(pageText()).toContain('The result of this request is unconfirmed.');
  const original = note(firstId, 'Original English question');
  rows['project-a'] = [original];
  generate.mockResolvedValueOnce(original);
  act(() => button('Retry this same request').props.onClick()); await flush();
  expect(generate.mock.calls[2][0]).toBe('project-a');
  expect(generate.mock.calls[2][1]).toEqual(generate.mock.calls[0][1]);
  expect(select().props.value).toBe(firstId);
  expect(pageText()).not.toContain('The result of this request is unconfirmed.');
  await changeContext(true);
  expect(pageText()).toContain(dimension === 'locale' ? '这次请求的结果尚未确认' : 'The result of this request is unconfirmed.');
  expect(button(understand).props.disabled).toBe(true);
  expect(!!button(dimension === 'locale' ? '重试同一请求' : 'Retry this same request')).toBe(true);
  expect(generate).toHaveBeenCalledTimes(3);
  expect(crypto.randomUUID).toHaveBeenCalledTimes(2);
});
