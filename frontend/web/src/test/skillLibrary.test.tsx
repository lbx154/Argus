import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer, type ReactTestInstance } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type SkillCatalog, type SkillLibraryItem, type SkillScope } from '../api';
import { SkillLibrary, SkillLibraryEntry } from '../components/SkillLibrary';

vi.mock('../i18n', () => ({ useI18n: () => ({ locale: 'en-US', t: (key: string) => key }) }));
vi.mock('../api', () => ({ api: { skillLibrary: vi.fn(), skillDocument: vi.fn() } }));
vi.mock('../components/MarkdownContent', () => ({ MarkdownContent: ({ children }: { children: string }) => <div data-markdown>{children}</div> }));

const item = (scope: SkillScope, name: string, updated_at: number | null = null): SkillLibraryItem => ({
  scope, name, updated_at, library: `${scope}:saved`, path: `engineer/${name}.md`, description: `When to use ${name}`,
  vertical: scope === 'vertical' ? 'software' : '', source: scope === 'project' ? 'project' : 'shared', role: 'engineer', is_default: updated_at == null,
});
const fixture: SkillCatalog = { scopes: ['global', 'vertical', 'project'], verticals: ['software'], active_vertical: 'software', errors: [],
  items: [item('global', 'Default'), item('global', 'Shared learning', 20), item('vertical', 'Vertical learning', 30), item('project', 'Project learning', 40)] };
let client: QueryClient;
let renderer: ReactTestRenderer;
const content = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : content(child)).join('');
const button = (name: string) => renderer.root.findAllByType('button').find(node => content(node).startsWith(name))!;
const tree = (sid: string | null = 'one') => <QueryClientProvider client={client}><SkillLibrary sid={sid} projectName="Project One" /></QueryClientProvider>;
const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(5); }); };
async function mount(sid: string | null = 'one') { await act(async () => { renderer = create(tree(sid)); }); await settle(); }

beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  vi.mocked(api.skillLibrary).mockResolvedValue(structuredClone(fixture));
  vi.mocked(api.skillDocument).mockImplementation(async (_sid, _library, path) => ({ name: path, description: '', content: 'Full instructions. '.repeat(100), markdown: '', path, source: 'project', scope: 'project', role: 'engineer', vertical: '' }));
});
afterEach(() => { act(() => renderer?.unmount()); client.clear(); vi.useRealTimers(); });

it('opens on recent work from all classes, excluding defaults and putting newest first', async () => {
  await mount();
  const categories = renderer.root.findByProps({ 'aria-label': 'Skill categories' }).findAllByType('button').map(content);
  expect(categories).toEqual(['Recent updates', 'Global2', 'Vertical1', 'Project1']);
  const rows = renderer.root.findByProps({ 'aria-label': 'Skills' }).findAllByType('button').map(content);
  expect(rows).toHaveLength(3);
  expect(rows[0]).toContain('Project learning');
  expect(rows[1]).toContain('Vertical learning');
  expect(rows[2]).toContain('Shared learning');
  expect(rows.join('')).not.toContain('Default');
});

it('reads the complete skill and searches its description and path', async () => {
  await mount();
  act(() => button('Project learning').props.onClick()); await settle();
  expect(api.skillDocument).toHaveBeenCalledWith('one', 'project:saved', 'engineer/Project learning.md', expect.any(AbortSignal));
  expect(content(renderer.root.findByProps({ 'data-markdown': true })).length).toBeGreaterThan(1000);
  act(() => button('Global').props.onClick());
  act(() => renderer.root.findByType('input').props.onChange({ target: { value: 'engineer/Default' } }));
  expect(renderer.root.findByProps({ 'aria-label': 'Skills' }).findAllByType('button').map(content)).toHaveLength(1);
  expect(button('Default')).toBeDefined();
  act(() => renderer.root.findByType('input').props.onChange({ target: { value: 'missing' } }));
  expect(content(renderer.root)).toContain('No matching skills.');
});

it('does not retain the previous project document when switching projects', async () => {
  await mount();
  act(() => button('Project learning').props.onClick()); await settle();
  expect(renderer.root.findAllByProps({ 'data-markdown': true })).toHaveLength(1);
  await act(async () => renderer.update(tree('two'))); await settle();
  expect(renderer.root.findAllByProps({ 'data-markdown': true })).toHaveLength(0);
  expect(api.skillDocument).toHaveBeenCalledTimes(1);
});

it('supports browsing without a project and explains empty project and recent lists', async () => {
  vi.mocked(api.skillLibrary).mockResolvedValue({ ...fixture, items: [fixture.items[0]] });
  await mount(null);
  expect(api.skillLibrary).toHaveBeenCalledWith(null, expect.any(AbortSignal));
  expect(content(renderer.root)).toContain('No skill updates yet.');
  act(() => button('Project').props.onClick());
  expect(content(renderer.root)).toContain('Select a project');
  act(() => button('Global').props.onClick());
  expect(button('Default')).toBeDefined();
});

it('shows a new saved skill directly in the sidebar and opens that document', async () => {
  const onOpen = vi.fn();
  await act(async () => { renderer = create(<QueryClientProvider client={client}><SkillLibraryEntry sid="one" onOpen={onOpen} /></QueryClientProvider>); });
  await settle();
  expect(content(renderer.root)).toContain('Project learning');
  expect(content(renderer.root)).not.toContain('Default');
  act(() => button('Project learning').props.onClick());
  expect(onOpen).toHaveBeenCalledWith(fixture.items[3]);
  const next = item('global', 'Just produced', 50);
  vi.mocked(api.skillLibrary).mockResolvedValue({ ...fixture, items: [...fixture.items, next] });
  await act(async () => { await client.invalidateQueries({ queryKey: ['skill-library', 'one'] }); }); await settle();
  const names = renderer.root.findAllByType('button').map(content);
  expect(names[1]).toContain('Just produced');
  act(() => button('Just produced').props.onClick());
  expect(onOpen).toHaveBeenLastCalledWith(next);
});

it('does not apply an inactive vertical filter to the project empty state', async () => {
  vi.mocked(api.skillLibrary).mockResolvedValue({ ...fixture, items: fixture.items.filter(row => row.scope !== 'project') });
  await mount();
  act(() => button('Vertical').props.onClick());
  act(() => renderer.root.findByType('select').props.onChange({ target: { value: 'software' } }));
  act(() => button('Project').props.onClick());
  expect(content(renderer.root)).toContain('This project has no saved skills yet.');
  expect(content(renderer.root)).not.toContain('No matching skills.');
});

it('opens a sidebar selection immediately and supports returning to the mobile list', async () => {
  await act(async () => { renderer = create(<QueryClientProvider client={client}><SkillLibrary sid="one" initialSelection={fixture.items[2]} /></QueryClientProvider>); });
  await settle(); await settle();
  expect(api.skillDocument).toHaveBeenCalledWith('one', 'vertical:saved', 'engineer/Vertical learning.md', expect.any(AbortSignal));
  act(() => button('← Back to skills').props.onClick());
  expect(renderer.root.findAllByProps({ 'data-markdown': true })).toHaveLength(0);
});

it('shows request failures and offers a retry without pretending the library is empty', async () => {
  vi.mocked(api.skillLibrary).mockRejectedValue(new Error('Offline'));
  await mount();
  expect(content(renderer.root)).toContain('Could not load the skill library.');
  expect(content(renderer.root)).not.toContain('No skill updates yet.');
  vi.mocked(api.skillLibrary).mockResolvedValue(fixture);
  act(() => button('Retry').props.onClick()); await settle();
  expect(button('Project learning')).toBeDefined();
});
