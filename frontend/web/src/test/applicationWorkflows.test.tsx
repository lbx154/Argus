import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import type { WorkflowSnapshot } from '../../../core/src/applicationWorkflows';
import WorkflowWorkbench from '../components/WorkflowWorkbench';

const mock = vi.hoisted(() => ({ action: vi.fn(), invalidate: vi.fn(), work: {} as WorkflowSnapshot, locale: 'en' }));
vi.mock('../api', () => ({ api: { workflows: { action: mock.action } } }));
vi.mock('../i18n', () => ({ useI18n: () => ({ locale: mock.locale, setLocale: vi.fn() }) }));
vi.mock('../useWorkbenchTheme', () => ({ useWorkbenchTheme: () => ({ cycleTheme: vi.fn() }) }));
vi.mock('../components/Wordmark', () => ({ Wordmark: () => <span>Example workbench</span> }));
vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: mock.invalidate }),
  useQuery: ({ queryKey }: { queryKey: string[] }) => ({
    data: queryKey[0] === 'workflow-catalog'
      ? { domains: {}, projects: [], roles: ['coordinator', 'designer', 'verifier', 'reviewer'] }
      : mock.work,
  }),
}));
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); mock.locale = 'en'; });

function mount(status: string, active = false) {
  vi.stubGlobal('window', { location: { search: '?project=work-0001' } });
  mock.work = {
    id: 'work-0001', project: '/work/counter', objective: 'Design counter', mode: 'design',
    status, phase: status, independently_accepted: false, feedback: '', resumes: [], repairs: 0,
    plan: { roadmap: ['Verify'], spec: 'An admitted specification', checks: [{ id: 'provider', provider_stage: 'test' }] },
    messages: [], calls: [{
      role: 'reviewer', operation: 'plan', status: 'failed', detail: 'host rejected contract mutation',
      folder: 'record', started_at: 1, duration_s: 2, live: false,
      decision: { status: 'done', reason: 'model approval alone is not host acceptance' },
    }],
    jobs: active ? [{ id: 'job-0001', action: 'run', status: 'running', started_at: 1, detail: '', log: '' }] : [],
  };
  mock.action.mockResolvedValue(mock.work);
  let renderer!: ReactTestRenderer;
  act(() => { renderer = create(<WorkflowWorkbench />); });
  return renderer;
}

it('keeps Coordinator dialogue available while an engineering worker runs', async () => {
  const renderer = mount('designer', true);
  const buttons = renderer.root.findAllByType('button');
  expect(buttons.find(button => button.children.includes('Run with models'))?.props.disabled).toBe(true);
  const send = buttons.find(button => button.children.includes('Send'))!;
  expect(send.props.disabled).toBe(false);
  act(() => renderer.root.findByProps({ 'aria-label': 'Ask Coordinator' }).props.onChange({
    target: { value: 'What is running?' },
  }));
  await act(async () => { await send.parent!.props.onSubmit({ preventDefault() {} }); });
  expect(mock.action).toHaveBeenCalledWith('work-0001', {
    action: 'chat', allow_model: true, message: 'What is running?',
  });
  act(() => renderer.unmount());
});

it('allows a settled between-phase continuation and preserves host and reviewer outcomes', () => {
  const renderer = mount('preflight');
  expect(renderer.root.findAllByType('button').find(button => button.children.includes('Run with models'))?.props.disabled).toBe(false);
  const output = JSON.stringify(renderer.toJSON());
  expect(output).toContain('failed');
  expect(output).toContain('host rejected contract mutation');
  expect(output).toContain('model approval alone is not host acceptance');
  expect(output).toContain('test');
  act(() => renderer.unmount());
});

it.each([['en', 'Tool execution'], ['zh-CN', '工具执行']])(
  'presents legacy execution records as tools rather than another role in %s',
  (locale, label) => {
    mock.locale = locale;
    const renderer = mount('checks');
    mock.work.calls = [{
      role: 'host', operation: 'check:behavior', status: 'passed', detail: '',
      folder: 'tool-record', started_at: 1, duration_s: 1, live: false, decision: null,
    }];
    act(() => renderer.update(<WorkflowWorkbench />));
    const output = JSON.stringify(renderer.toJSON());
    expect(output).toContain(label);
    expect(output).not.toContain('"host"');
    expect(mock.work.calls[0].role).toBe('host');
    expect(renderer.root.findAllByType('button').some(button =>
      button.children.includes(locale === 'en' ? 'Role response' : '角色回复'))).toBe(false);
    act(() => renderer.unmount());
  },
);
