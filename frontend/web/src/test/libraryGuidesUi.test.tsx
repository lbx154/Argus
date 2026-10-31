import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type SkillLibraryItem } from '../api';
import { LearningLibrary } from '../components/LearningLibrary';
const state = vi.hoisted(() => ({ locale: 'zh-CN' }));
vi.mock('../i18n', async importOriginal => {
  const actual = await importOriginal<typeof import('../i18n')>();
  return { ...actual, useI18n: () => ({ locale: state.locale, t: (key: string) => actual.translate(key, {}, state.locale as 'zh-CN' | 'en') }) };
});
vi.mock('../api', () => ({ api: { skillLibrary: vi.fn(), skillDocument: vi.fn(), knowledgeFeed: vi.fn() } }));
vi.mock('../components/MarkdownContent', () => ({ MarkdownContent: ({ children }: { children: string }) => <pre data-original>{children}</pre> }));
const builtin: SkillLibraryItem = { source: 'bundled', library: 'bundled',
  path: 'software/SKILL.md', name: 'software', description: 'Original English description',
  updated_at: null };
const custom = { ...builtin, library: 'shared', source: 'shared',
  name: 'Operator original title', description: 'Do not rewrite this description', updated_at: 2 } as SkillLibraryItem;
let client: QueryClient, view: ReactTestRenderer | undefined;
const content = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : content(child)).join('');
const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(5); }); };
const button = (name: string) => view!.root.findAllByType('button').find(node => content(node).startsWith(name))!;
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks(); state.locale = 'zh-CN';
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  vi.mocked(api.skillLibrary).mockResolvedValue({ items: [builtin, custom] });
  vi.mocked(api.skillDocument).mockImplementation(async () => ({ content: '# ORIGINAL\n\nExact original text.\n' }));
  vi.mocked(api.knowledgeFeed).mockResolvedValue({ events: [] });
});

it('reads skills in the unified browser and resets selection on a project switch', async () => {
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" initialScope="global" /></QueryClientProvider>); }); await settle();
  act(() => view!.root.findAllByType('button').find(node => content(node).includes('software'))!.props.onClick()); await settle();
  expect(api.skillDocument).toHaveBeenCalledWith('s-A', builtin.library, builtin.path, expect.any(AbortSignal));
  expect(content(view!.root.findByProps({ 'data-original': true }))).toContain('Exact original text.');
  await act(async () => view!.update(<QueryClientProvider client={client}><LearningLibrary sid="s-B" initialScope="global" /></QueryClientProvider>)); await settle();
  expect(view!.root.findAllByProps({ 'data-original': true })).toHaveLength(0);
  expect(api.skillDocument).toHaveBeenCalledTimes(1);
});

it('adds only an explicitly selected, loaded document with its source to the draft', async () => {
  const onUse = vi.fn();
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" onUse={onUse} /></QueryClientProvider>); }); await settle();
  expect(onUse).not.toHaveBeenCalled();
  act(() => view!.root.findAllByType('button').find(node => content(node).includes(custom.name))!.props.onClick()); await settle();
  expect(button('用于当前对话').props.disabled).toBe(false);
  act(() => button('用于当前对话').props.onClick());
  expect(onUse).toHaveBeenCalledWith(custom.name, '# ORIGINAL\n\nExact original text.\n', expect.stringContaining('团队'));
  expect(onUse.mock.calls[0][2]).toContain(custom.path);
});

it('does not offer an unreadable document as usable context', async () => {
  vi.mocked(api.skillDocument).mockRejectedValue(new Error('unavailable'));
  const onUse = vi.fn();
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" onUse={onUse} /></QueryClientProvider>); }); await settle();
  act(() => view!.root.findAllByType('button').find(node => content(node).includes(custom.name))!.props.onClick()); await settle();
  expect(button('用于当前对话').props.disabled).toBe(true);
  expect(onUse).not.toHaveBeenCalled();
});

it('separates real learning records from file updates and reports source failures explicitly', async () => {
  vi.mocked(api.knowledgeFeed).mockRejectedValue(new Error('synthetic failure'));
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" initialScope="feed" /></QueryClientProvider>); }); await settle();
  expect(content(view!.root)).toContain('学习记录无法加载');
  expect(content(view!.root)).toContain(custom.name);
  expect(content(view!.root)).not.toContain('当前分类或筛选条件下没有资料');
  expect(content(view!.root)).not.toContain('software');
});

it.each([
  { locale: 'zh-CN', labels: ['学习动态', '全局项目'], skillName: 'software' },
  { locale: 'en', labels: ['Learning feed', 'Across projects'], skillName: 'software' },
])('offers the learning feed, by default, and everything the library holds in $locale', async ({ locale, labels, skillName }) => {
  state.locale = locale;
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" /></QueryClientProvider>); }); await settle();
  const tabs = view!.root.findByType('nav').findAllByType('button');
  expect(tabs.map(content)).toEqual(labels);
  expect(tabs.map(node => node.props['aria-pressed'])).toEqual([true, false]);
  expect(api.knowledgeFeed).toHaveBeenCalled();
  expect(content(view!.root)).not.toContain(skillName);
  act(() => button(labels[1]).props.onClick()); await settle();
  expect(content(view!.root)).toContain(skillName);
  expect(view!.root.findByType('nav').findAllByType('button').map(node => node.props['aria-pressed'])).toEqual([false, true]);
});

it('renders the skill body without front matter and does not repeat the global scope per row', async () => {
  vi.mocked(api.skillDocument).mockResolvedValue({ content: '---\r\nname: original\r\ndescription: "Metadata only"\r\n---\r\n# Body\n\nKeep this method.\n' });
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" initialScope="global" /></QueryClientProvider>); }); await settle();
  const rows = view!.root.findByType('aside');
  expect(content(rows)).not.toContain('全局项目');
  act(() => view!.root.findAllByType('button').find(node => content(node).includes(custom.name))!.props.onClick()); await settle();
  expect(content(view!.root.findByProps({ 'data-original': true }))).toBe('# Body\n\nKeep this method.\n');
});

afterEach(() => { if (view) act(() => view!.unmount()); view = undefined; client.clear(); vi.useRealTimers(); });
