import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type SkillLibraryItem, type WikiLibraryItem } from '../api';
import { SkillLibrary } from '../components/SkillLibrary';
import { WikiLibrary } from '../components/WikiLibrary';
import { LearningLibrary, learningEntries } from '../components/LearningLibrary';
import { knowledgePurpose } from '../lib/libraryPresentation';
const state = vi.hoisted(() => ({ locale: 'zh-CN' }));
vi.mock('../i18n', async importOriginal => {
  const actual = await importOriginal<typeof import('../i18n')>();
  return { ...actual, useI18n: () => ({ locale: state.locale, t: (key: string) => actual.translate(key, {}, state.locale as 'zh-CN' | 'en') }) };
});
vi.mock('../api', () => ({ api: { skillLibrary: vi.fn(), skillDocument: vi.fn(), wikiLibrary: vi.fn(), wikiDocument: vi.fn(), knowledgeFeed: vi.fn(), learningStatus: vi.fn() } }));
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
  vi.mocked(api.learningStatus).mockResolvedValue({ jobs: [], pending: 0, revision: 0 });
  vi.mocked(api.wikiDocument).mockResolvedValue({ ...wiki, content: '# ORIGINAL KNOWLEDGE\n\nNo automatic translation.\n', markdown: '', truncated: false });
});

it('unifies navigation into four categories while keeping private records, indexes and original scope', async () => {
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" initialScope="global" /></QueryClientProvider>); }); await settle();
  const nav = view!.root.findByType('nav');
  expect(nav.findAllByType('button').map(content)).toEqual(['通用资料', '领域资料', '项目资料', '学习动态']);
  expect(content(view!.root)).toContain('个人记录 · 原私有范围，不共享');
  act(() => view!.root.findAllByType('button').find(node => content(node).includes(wiki.title))!.props.onClick()); await settle();
  expect(api.wikiDocument).toHaveBeenCalledWith('s-A', 'private', '', wiki.path, expect.any(AbortSignal));
  expect(content(view!.root)).toContain('No automatic translation.');
  act(() => view!.root.findAllByType('button').find(node => content(node).includes('个人概况（原私有范围）'))!.props.onClick()); await settle();
  expect(content(view!.root)).toContain('Original profile stays intact');
  act(() => view!.root.findAllByType('button').find(node => content(node).startsWith('索引'))!.props.onClick()); await settle();
  expect(content(view!.root)).toContain('# ORIGINAL INDEX');
});

it('reads skills in the unified browser and resets selection on a project switch', async () => {
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" initialScope="global" /></QueryClientProvider>); }); await settle();
  act(() => view!.root.findAllByType('button').find(node => content(node).includes('分段阅读 PDF'))!.props.onClick()); await settle();
  expect(api.skillDocument).toHaveBeenCalledWith('s-A', builtin.library, builtin.path, expect.any(AbortSignal));
  expect(content(view!.root.findByProps({ 'data-skill-reading-guide': true }))).toContain('不会运行工具或发起模型任务');
  await act(async () => view!.update(<QueryClientProvider client={client}><LearningLibrary sid="s-B" initialScope="global"
    initialSelection={{ kind: 'skill', item: builtin }} /></QueryClientProvider>)); await settle();
  expect(view!.root.findAllByProps({ 'data-skill-reading-guide': true })).toHaveLength(0);
  expect(api.skillDocument).toHaveBeenCalledTimes(1);
});

it('adds only an explicitly selected, loaded document with its original scope to the draft', async () => {
  const onUse = vi.fn();
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" onUse={onUse} /></QueryClientProvider>); }); await settle();
  expect(onUse).not.toHaveBeenCalled();
  act(() => view!.root.findAllByType('button').find(node => content(node).includes(wiki.title))!.props.onClick()); await settle();
  expect(button('用于当前对话').props.disabled).toBe(false);
  act(() => button('用于当前对话').props.onClick());
  expect(onUse).toHaveBeenCalledWith(wiki.title, '# ORIGINAL KNOWLEDGE\n\nNo automatic translation.\n', expect.stringContaining('原私有范围'));
  expect(onUse.mock.calls[0][2]).toContain(wiki.path);
  expect(content(view!.root)).toContain('不会自动执行技能');
});

it('does not offer an unreadable document as usable context', async () => {
  vi.mocked(api.wikiDocument).mockRejectedValue(new Error('unavailable'));
  const onUse = vi.fn();
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" onUse={onUse} /></QueryClientProvider>); }); await settle();
  act(() => view!.root.findAllByType('button').find(node => content(node).includes(wiki.title))!.props.onClick()); await settle();
  expect(button('用于当前对话').props.disabled).toBe(true);
  expect(onUse).not.toHaveBeenCalled();
});

it('separates real learning records from file updates and reports source failures explicitly', async () => {
  vi.mocked(api.wikiLibrary).mockRejectedValue(new Error('synthetic failure'));
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" initialScope="feed" /></QueryClientProvider>); }); await settle();
  expect(content(view!.root)).toContain('学习记录与文件更新分开展示');
  expect(content(view!.root)).toContain('知识无法加载');
  expect(content(view!.root)).toContain(custom.name);
  expect(content(view!.root)).not.toContain('当前分类或筛选条件下没有资料');
  expect(content(view!.root)).not.toContain('分段阅读 PDF');
});

it('opens on reusable material by default and loads learning history only when selected', async () => {
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" /></QueryClientProvider>); }); await settle();
  expect(content(view!.root)).toContain('分段阅读 PDF');
  expect(api.knowledgeFeed).not.toHaveBeenCalled();
  act(() => button('学习动态').props.onClick()); await settle();
  expect(api.knowledgeFeed).toHaveBeenCalled();
  expect(content(view!.root)).toContain('学习记录与文件更新分开展示');
});

it('assigns lessons, principles, profiles and indexes to their original scopes in the four-category browser', async () => {
  const wikiCatalog = await api.wikiLibrary(null);
  const skillCatalog = await api.skillLibrary(null);
  const catalog = { ...wikiCatalog, items: [{ ...wiki, scope: 'vertical' as const, vertical: 'research', kind: 'lesson' }],
    libraries: [...wikiCatalog.libraries, { scope: 'vertical' as const, vertical: 'research', root: '/synthetic/research',
      pages: [], index_markdown: '# index', principles: 'Keep the evidence' }] };
  const entries = learningEntries(catalog, skillCatalog, 'vertical', 'zh-CN');
  expect(entries.map(entry => entry.kind).sort()).toEqual(['index', 'knowledge', 'principles']);
  expect(entries.every(entry => entry.scope === 'vertical' && entry.vertical === 'research')).toBe(true);
  expect(learningEntries(catalog, skillCatalog, 'project', 'en').some(entry => entry.scope === 'private')).toBe(false);
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
