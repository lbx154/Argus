import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type SkillLibraryItem, type WikiLibraryItem } from '../api';
import { SkillLibrary } from '../components/SkillLibrary';
import { WikiLibrary } from '../components/WikiLibrary';
import { knowledgePurpose } from '../lib/libraryPresentation';
const state = vi.hoisted(() => ({ locale: 'zh-CN' }));
vi.mock('../i18n', async importOriginal => {
  const actual = await importOriginal<typeof import('../i18n')>();
  return { ...actual, useI18n: () => ({ locale: state.locale, t: (key: string) => actual.translate(key, {}, state.locale as 'zh-CN' | 'en') }) };
});
vi.mock('../api', () => ({ api: { skillLibrary: vi.fn(), skillDocument: vi.fn(), wikiLibrary: vi.fn(), wikiDocument: vi.fn(), knowledgeFeed: vi.fn() } }));
vi.mock('../components/MarkdownContent', () => ({ MarkdownContent: ({ children }: { children: string }) => <pre data-original>{children}</pre> }));
const builtin: SkillLibraryItem = { scope: 'global', vertical: '', source: 'bundled', library: 'global:bundled',
  path: 'engineer/pdf-chat.md', name: 'Progressive PDF Reading', description: 'Original English description',
  role: 'engineer', updated_at: null, is_default: true };
const custom = { ...builtin, library: 'project:learned', source: 'project', scope: 'project', is_default: false,
  name: 'Operator original title', description: 'Do not rewrite this description', updated_at: 2 } as SkillLibraryItem;
const wiki: WikiLibraryItem = { scope: 'private', vertical: '', root: '/synthetic/private-notes', path: 'notes/example.md',
  title: 'Original personal note', description: 'Original evidence summary', kind: 'note', source: 'synthetic conversation',
  created: '2026-09-18', updated_at: 3, reuse_count: 0 };
let client: QueryClient, view: ReactTestRenderer | undefined;
const content = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : content(child)).join('');
const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(5); }); };
const button = (name: string) => view!.root.findAllByType('button').find(node => content(node).startsWith(name))!;
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks(); state.locale = 'zh-CN';
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  vi.mocked(api.skillLibrary).mockResolvedValue({ scopes: ['global', 'vertical', 'project'], items: [builtin, custom], verticals: [], active_vertical: '', errors: [] });
  vi.mocked(api.skillDocument).mockImplementation(async (_sid, _library, path) => ({ ...builtin, path, content: '# ORIGINAL\n\nExact original text.\n', markdown: '' }));
  vi.mocked(api.wikiLibrary).mockResolvedValue({ scopes: ['private', 'global', 'vertical', 'project'], items: [wiki], verticals: [], active_vertical: '', errors: [],
    libraries: [{ scope: 'private', vertical: '', root: '/synthetic/private-notes', pages: [], index_markdown: '# ORIGINAL INDEX', profile: 'Original profile stays intact' }] });
  vi.mocked(api.knowledgeFeed).mockResolvedValue({ events: [] });
  vi.mocked(api.wikiDocument).mockResolvedValue({ ...wiki, content: '# ORIGINAL KNOWLEDGE\n\nNo automatic translation.\n', markdown: '', truncated: false });
});
afterEach(() => { if (view) act(() => view!.unmount()); view = undefined; client.clear(); vi.useRealTimers(); });
async function skills() {
  await act(async () => { view = create(<QueryClientProvider client={client}><SkillLibrary sid="s-A" initialScope="global" /></QueryClientProvider>); }); await settle();
}
it('shows and searches the Chinese built-in alias, while the document request retains its original identity', async () => {
  await skills();
  expect(content(view!.root)).toContain('怎么做');
  act(() => view!.root.findByType('input').props.onChange({ target: { value: '分段阅读' } }));
  act(() => button('分段阅读 PDF').props.onClick()); await settle();
  expect(api.skillDocument).toHaveBeenCalledExactlyOnceWith('s-A', 'global:bundled', 'engineer/pdf-chat.md', expect.any(AbortSignal));
  const guide = view!.root.findByProps({ 'data-skill-reading-guide': true });
  expect(content(guide)).toContain('什么时候参考');
  expect(content(guide)).toContain('不会运行工具或发起模型任务');
  expect(content(view!.root.findByProps({ 'data-original': true }))).toBe('# ORIGINAL\n\nExact original text.\n');
  expect(view!.root.findByProps({ 'data-skill-original': true }).props.open).toBeUndefined();
});
it('keeps English mode, and never aliases a learned document with a matching built-in filename', async () => {
  state.locale = 'en'; await skills();
  expect(content(view!.root)).toContain('Progressive PDF Reading');
  expect(content(view!.root)).not.toContain('分段阅读 PDF');
  act(() => button('Project').props.onClick());
  act(() => button('Operator original title').props.onClick()); await settle();
  expect(view!.root.findAllByProps({ 'data-skill-reading-guide': true })).toHaveLength(0);
  expect(content(view!.root)).toContain('Do not rewrite this description');
  expect(api.skillDocument).toHaveBeenCalledWith('s-A', 'project:learned', 'engineer/pdf-chat.md', expect.any(AbortSignal));
});
it('clears the guide and document selection when sid changes', async () => {
  await skills(); act(() => button('分段阅读 PDF').props.onClick()); await settle();
  expect(view!.root.findAllByProps({ 'data-skill-reading-guide': true })).toHaveLength(1);
  await act(async () => view!.update(<QueryClientProvider client={client}><SkillLibrary sid="s-B" /></QueryClientProvider>)); await settle();
  expect(view!.root.findAllByProps({ 'data-skill-reading-guide': true })).toHaveLength(0);
  expect(api.skillDocument).toHaveBeenCalledTimes(1);
});
it('explains knowledge kinds and private scope in Chinese without altering personal originals or profile', async () => {
  await act(async () => { view = create(<QueryClientProvider client={client}><WikiLibrary sid="s-A" initialScope="private" /></QueryClientProvider>); }); await settle();
  expect(content(view!.root)).toContain('知道什么、学到什么');
  expect(content(view!.root.findByProps({ 'data-wiki-profile': true }))).toContain('Original profile stays intact');
  act(() => view!.root.findAllByType('button').find(node => content(node).includes('Original personal note'))!.props.onClick()); await settle();
  const guide = view!.root.findByProps({ 'data-knowledge-reading-guide': true });
  expect(content(guide)).toContain(knowledgePurpose('note', 'zh-CN'));
  expect(content(guide)).toContain('不是这篇正文的翻译');
  expect(content(view!.root)).toContain('Original personal note');
  expect(content(view!.root)).toContain('Original evidence summary');
  expect(api.wikiDocument).toHaveBeenCalledExactlyOnceWith('s-A', 'private', '', 'notes/example.md', expect.any(AbortSignal));
  expect(view!.root.findAllByProps({ 'data-original': true }).map(content)).toContain('# ORIGINAL KNOWLEDGE\n\nNo automatic translation.\n');
});
