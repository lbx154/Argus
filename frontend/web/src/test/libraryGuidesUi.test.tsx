import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type SkillContribution, type SkillLibraryItem } from '../api';
import { ApiError } from '../../../core/src/http';
import { LearningLibrary } from '../components/LearningLibrary';
const state = vi.hoisted(() => ({ locale: 'zh-CN' }));
vi.mock('../i18n', async importOriginal => {
  const actual = await importOriginal<typeof import('../i18n')>();
  return { ...actual, useI18n: () => ({ locale: state.locale, t: (key: string) => actual.translate(key, {}, state.locale as 'zh-CN' | 'en') }) };
});
vi.mock('../api', () => ({ api: { skillLibrary: vi.fn(), skillDocument: vi.fn(), knowledgeFeed: vi.fn(), skillPacks: vi.fn(), addSkillPack: vi.fn(), removeSkillPack: vi.fn(), contributeSkill: vi.fn() } }));
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
  { locale: 'zh-CN', labels: ['学习动态', '全局项目', '技能商店'], skillName: 'software' },
  { locale: 'en', labels: ['Learning feed', 'Across projects', 'Skill store'], skillName: 'software' },
])('offers the learning feed, by default, and everything the library holds in $locale', async ({ locale, labels, skillName }) => {
  state.locale = locale;
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" /></QueryClientProvider>); }); await settle();
  const tabs = view!.root.findByType('nav').findAllByType('button');
  expect(tabs.map(content)).toEqual(labels);
  expect(tabs.map(node => node.props['aria-pressed'])).toEqual([true, false, false]);
  expect(api.knowledgeFeed).toHaveBeenCalled();
  expect(content(view!.root)).not.toContain(skillName);
  act(() => button(labels[1]).props.onClick()); await settle();
  expect(content(view!.root)).toContain(skillName);
  expect(view!.root.findByType('nav').findAllByType('button').map(node => node.props['aria-pressed'])).toEqual([false, true, false]);
});

it('renders the skill body without front matter and does not repeat the global scope per row', async () => {
  vi.mocked(api.skillDocument).mockResolvedValue({ content: '---\r\nname: original\r\ndescription: "Metadata only"\r\n---\r\n# Body\n\nKeep this method.\n' });
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" initialScope="global" /></QueryClientProvider>); }); await settle();
  const rows = view!.root.findByType('aside');
  expect(content(rows)).not.toContain('全局项目');
  act(() => view!.root.findAllByType('button').find(node => content(node).includes(custom.name))!.props.onClick()); await settle();
  expect(content(view!.root.findByProps({ 'data-original': true }))).toBe('# Body\n\nKeep this method.\n');
});

afterEach(() => { if (view) act(() => view!.unmount()); view = undefined; client.clear(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it('opens every feed path directly in the team library, including TSV and unlisted documents', async () => {
  vi.mocked(api.knowledgeFeed).mockResolvedValue({ events: [
    { ts: 3, path: 'runs/p-one.tsv', title: 'runs/p-one.tsv' },
    { ts: 2, path: 'notes/plain.txt', title: 'Plain document' },
  ] });
  vi.mocked(api.skillDocument).mockImplementation(async (_sid, _library, path) => ({ content: path.endsWith('.tsv')
    ? 'run\tchange\nr1\tSay "hello"\n' : 'Exact plain document' }));
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" /></QueryClientProvider>); }); await settle();
  act(() => view!.root.findAllByProps({ 'data-feed-title': true }).find(node => content(node) === 'runs/p-one.tsv')!.props.onClick()); await settle();
  expect(api.skillDocument).toHaveBeenLastCalledWith('s-A', 'shared', 'runs/p-one.tsv', expect.any(AbortSignal));
  expect(view!.root.findAllByType('table')).toHaveLength(1);
  expect(view!.root.findAllByType('td').map(content)).toEqual(['r1', 'Say "hello"']);
  expect(view!.root.findByType('nav').findAllByType('button')[0].props['aria-pressed']).toBe(true);
  act(() => view!.root.findAllByProps({ 'data-feed-title': true }).find(node => content(node) === 'Plain document')!.props.onClick()); await settle();
  expect(api.skillDocument).toHaveBeenLastCalledWith('s-A', 'shared', 'notes/plain.txt', expect.any(AbortSignal));
  expect(content(view!.root.findByProps({ 'data-original': true }))).toBe('Exact plain document');
});

it('shows each source label and the original English description in Chinese', async () => {
  vi.mocked(api.skillLibrary).mockResolvedValue({ items: [builtin, custom, { ...builtin, name: 'installed', library: 'store', source: 'store' }] });
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" initialScope="global" /></QueryClientProvider>); }); await settle();
  const text = content(view!.root.findByType('aside'));
  for (const label of ['Argus 自带', '商店', '团队', 'Original English description']) expect(text).toContain(label);
});

it('installs and uninstalls through store buttons, waiting and refreshing the installed state', async () => {
  let installed = false;
  vi.mocked(api.skillPacks).mockImplementation(async () => ({ packs: [{ name: 'materials', version: '1', description: 'Check material evidence', description_zh: '检查材料证据', needs: [], requires: [], installed_version: installed ? '1' : null }] }));
  let finish: () => void = () => {};
  vi.mocked(api.addSkillPack).mockImplementation(() => new Promise(resolve => { finish = () => { installed = true; resolve({ ok: true }); }; }));
  vi.mocked(api.removeSkillPack).mockImplementation(async () => { installed = false; return { ok: true }; });
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" initialScope="store" /></QueryClientProvider>); }); await settle();
  expect(view!.root.findAllByType('section').map(content).join('')).toContain('检查材料证据版本 1 · 未安装');
  act(() => button('安装').props.onClick()); await settle();
  expect(api.addSkillPack).toHaveBeenCalledWith('materials');
  expect(button('安装').props.disabled).toBe(true);
  await act(async () => finish()); await settle();
  expect(button('卸载')).toBeTruthy();
  act(() => button('卸载').props.onClick()); await settle();
  expect(api.removeSkillPack).toHaveBeenCalledWith('materials');
  expect(button('安装')).toBeTruthy();
  expect(api.skillPacks).toHaveBeenCalledTimes(3);
});

it('updates an installed pack the store has moved past, by installing it again', async () => {
  let installed = '1';
  vi.mocked(api.skillPacks).mockImplementation(async () => ({ packs: [{ name: 'materials', version: '2', description: 'Check evidence', needs: [], requires: [], installed_version: installed }] }));
  vi.mocked(api.addSkillPack).mockImplementation(async () => { installed = '2'; return { ok: true }; });
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid={null} initialScope="store" /></QueryClientProvider>); }); await settle();
  act(() => button('更新到 2').props.onClick()); await settle();
  expect(api.addSkillPack).toHaveBeenCalledWith('materials');
  expect(view!.root.findAllByType('button').map(content)).not.toContain('更新到 2');
  expect(button('卸载')).toBeTruthy();
});

it('shows operation errors without changing installed status', async () => {
  vi.mocked(api.skillPacks).mockResolvedValue({ packs: [{ name: 'materials', version: '1', description: 'Check evidence', needs: [], requires: [], installed_version: null }] });
  vi.mocked(api.addSkillPack).mockRejectedValue(new Error('Download failed: HTTP 503'));
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid={null} initialScope="store" /></QueryClientProvider>); }); await settle();
  act(() => button('安装').props.onClick()); await settle();
  expect(content(view!.root.findByProps({ role: 'alert' }))).toContain('Download failed: HTTP 503');
  expect(button('安装').props.disabled).toBe(false);
});

const offered = (contribution: SkillContribution | null) => ({ ...custom, contribution });
const openTeamSkill = async () => {
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" initialScope="global" /></QueryClientProvider>); }); await settle();
  act(() => view!.root.findAllByType('button').find(node => content(node).includes(custom.name))!.props.onClick()); await settle();
};

it('offers a team skill to the store only once the operator confirms, then shows it waiting', async () => {
  const confirm = vi.fn(() => false);
  vi.stubGlobal('window', { confirm });
  let contribution: SkillContribution | null = null;
  vi.mocked(api.skillLibrary).mockImplementation(async () => ({ items: [builtin, offered(contribution)] }));
  vi.mocked(api.contributeSkill).mockImplementation(async () => { contribution = { name: custom.name, version: '0.1.0', requested_at: 1 }; return { ok: true }; });
  await act(async () => { view = create(<QueryClientProvider client={client}><LearningLibrary sid="s-A" initialScope="global" /></QueryClientProvider>); }); await settle();
  // Argus's own skills are not the team's to offer.
  act(() => view!.root.findAllByType('button').find(node => content(node).includes('software'))!.props.onClick()); await settle();
  expect(view!.root.findAllByType('button').map(content)).not.toContain('贡献到技能商店');
  act(() => view!.root.findAllByType('button').find(node => content(node).includes(custom.name))!.props.onClick()); await settle();
  act(() => button('贡献到技能商店').props.onClick()); await settle();
  expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/PR 公开提交到 GitHub 上的 argus-verticals 仓库.*所有用户都能在技能商店里安装/));
  expect(api.contributeSkill).not.toHaveBeenCalled();
  confirm.mockReturnValue(true);
  act(() => button('贡献到技能商店').props.onClick()); await settle();
  expect(api.contributeSkill).toHaveBeenCalledWith(custom.name);
  expect(content(view!.root.findByType('article'))).toContain('已记下，等待提交');
  expect(view!.root.findAllByType('button').map(content)).not.toContain('贡献到技能商店');
});

it.each([
  { locale: 'zh-CN', labels: ['贡献到技能商店', '已记下，等待提交', '已提交 PR', '，维护者审核合并后进入商店', '重新提交', '重试'] },
  { locale: 'en', labels: ['Contribute to the skill store', 'Recorded, waiting to be submitted', 'Pull request submitted', 'joins the store once the maintainers review and merge it', 'Submit again', 'Retry'] },
])('shows where a team skill offered to the store stands, as the host answered, in $locale', async ({ locale, labels: [offer, waiting, submitted, review, again, retry] }) => {
  state.locale = locale;
  const asked = { name: custom.name, version: '0.1.0', requested_at: 1 };
  const url = 'https://github.com/Argus-AiTeam/argus-verticals/pull/17';
  for (const [contribution, shown, action] of [
    [null, [], offer], [asked, [waiting], null],
    [{ ...asked, url, submitted_at: 2 }, [submitted, url, review], again],
    [{ ...asked, error: '商店里已有同名的包', submitted_at: 2 }, ['商店里已有同名的包'], retry],
  ] as const) {
    vi.mocked(api.skillLibrary).mockResolvedValue({ items: [offered(contribution)] });
    await openTeamSkill();
    const text = content(view!.root.findByType('article'));
    for (const words of shown) expect(text).toContain(words);
    const buttons = view!.root.findAllByType('button').map(content);
    for (const label of [offer, again, retry]) expect(buttons.includes(label)).toBe(label === action);
    if (contribution && 'url' in contribution) expect(view!.root.findByType('a').props.href).toBe(url);
    act(() => view!.unmount()); view = undefined; client.clear();
  }
});

it("says why the store would not take a team skill, and words any other error itself", async () => {
  vi.stubGlobal('window', { confirm: () => true });
  vi.mocked(api.skillLibrary).mockResolvedValue({ items: [offered(null)] });
  const refused = (detail: string, code: string) => new ApiError(`POST /api/skill-library/contribute → 400: ${detail}`, 400, 'POST', '/api/skill-library/contribute', code, detail);
  const failure = "ENOENT: no such file or directory, rename '/home/argus/contributions/materials.json.tmp'";
  vi.mocked(api.contributeSkill).mockRejectedValue(refused(failure, 'request_failed'));
  await openTeamSkill();
  act(() => button('贡献到技能商店').props.onClick()); await settle();
  expect(content(view!.root.findByProps({ role: 'alert' }))).toMatch(/^当前状态还不能执行这个操作。.*错误详情/);
  const reason = 'A pack holds at most 8 MiB; this one holds 9.0 MiB';
  vi.mocked(api.contributeSkill).mockRejectedValue(refused(reason, 'not_for_store'));
  act(() => button('贡献到技能商店').props.onClick()); await settle();
  expect(content(view!.root.findByProps({ role: 'alert' }))).toBe(reason);
  expect(button('贡献到技能商店').props.disabled).toBe(false);
});
