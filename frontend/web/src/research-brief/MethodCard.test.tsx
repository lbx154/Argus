import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, type ResearchMethod } from '../api';
import { MethodCard, methodDate, methodQueryKey, methodStatusTone, summarizeTests } from './MethodCard';
import { inputs } from './testFixtures';

type Present = Extract<ResearchMethod, { exists: true }>;

const method: Present = {
  exists: true, path: 'METHOD.md', updated_at: Math.floor(Date.now() / 1000) - 120, truncated: false,
  title: 'Contrastive pretraining with a queue',
  statement: 'The encoder feeds a queue of negatives.',
  markdown: '# Contrastive pretraining with a queue\n\nThe encoder feeds a queue of negatives.\n\n## Protocol\n\nFive seeds.\n',
  components: [
    { component: 'Encoder', prescribes: 'Backbone as in the paper', notes: '', status: 'proven', tests: [
      { id: 'tests/spec/test_knockouts.py::test_encoder_removed', kind: 'knockout', outcome: 'PASSED' },
      { id: 'tests/spec/test_invariants.py::test_encoder_shape', kind: 'invariant', outcome: 'PASSED' },
    ] },
    { component: 'Queue', prescribes: '65536 negatives', notes: 'simplified to 4096 on one card', status: 'contradicted', tests: [
      { id: 'tests/spec/test_differential.py::test_queue_matches_reference', kind: 'differential', outcome: 'FAILED' },
    ] },
    { component: 'Projection head', prescribes: 'two layers', notes: '', status: 'partial', tests: [
      { id: 'tests/spec/test_invariants.py::test_head_shape', kind: 'invariant', outcome: 'PASSED' },
    ] },
    { component: 'Momentum update', prescribes: 'm=0.999', notes: '', status: 'untested', tests: [] },
    { component: 'Temperature', prescribes: 'tau=0.07', notes: '', status: 'unchecked', tests: [
      { id: 'tests/spec/test_claim_shape.py::test_temperature', kind: 'claim', outcome: null },
    ] },
  ],
  unlisted_components: ['Data augmentation'],
  protocol: 'Five seeds.',
  falsifiers: 'Removing the queue leaves accuracy unchanged.',
  reused_code: [
    { name: 'encoder-lib', kind: 'third_party', revision_or_version: 'abc1234', remote: 'https://example.invalid/encoder-lib.git', modules: ['encoder_lib.backbone', 'encoder_lib.heads'], imported_from: ['src/model.py', 'src/train.py'] },
    { name: 'numpy', kind: 'package', revision_or_version: '2.1.0', remote: '', modules: ['numpy'], imported_from: ['src/queue.py'] },
  ],
  hyperparameters: [
    { key: 'queue.size', value: '65536', file: 'configs/pretrain.yaml', why: 'matches the route', changed: true, previous: '32768' },
    { key: 'momentum', value: '0.999', file: 'configs/pretrain.yaml', why: '', changed: false, previous: null },
  ],
  change_log: [
    { when: '2026-09-16', summary: 'Add the momentum update', files: ['src/model.py'] },
    { when: '2026-09-15', summary: 'Card written from the selected route', files: ['METHOD.md'] },
  ],
  checks: { round_index: 3, ran_at: Math.floor(Date.now() / 1000) - 60, exit_code: 1, counts: { PASSED: 4, FAILED: 1 } },
};

let renderer: ReactTestRenderer | undefined;
let client: QueryClient | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; client?.clear(); vi.restoreAllMocks(); vi.useRealTimers(); });

function seeded(data: ResearchMethod, sid = inputs().sid) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  client.setQueryData(methodQueryKey(sid), data);
  return client;
}

function mount(data: ResearchMethod) {
  const { sid } = inputs();
  act(() => { renderer = create(<QueryClientProvider client={seeded(data)}><MethodCard sid={sid} active={false} /></QueryClientProvider>); });
  return renderer!.root.findByProps({ 'data-testid': 'method-card' });
}

function markupOf(data: ResearchMethod, compact = false) {
  const { sid } = inputs();
  return renderToStaticMarkup(<QueryClientProvider client={seeded(data)}><MethodCard sid={sid} active={false} compact={compact} /></QueryClientProvider>);
}

const textOf = (node: { children: unknown[] }) => node.children.map(child => typeof child === 'string' ? child : '').join('');

describe('MethodCard', () => {
  it('renders nothing when the project has no METHOD.md', () => {
    expect(markupOf({ exists: false })).toBe('');
  });

  it('renders nothing when the host could not derive the card', () => {
    expect(markupOf({ exists: false, error: 'RuntimeError: config parser fell over' })).toBe('');
  });

  it('renders nothing before the document has been read', () => {
    const { sid } = inputs();
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const markup = renderToStaticMarkup(<QueryClientProvider client={client}><MethodCard sid={sid} active={false} /></QueryClientProvider>);
    expect(markup).toBe('');
  });

  it('shows the title, the statement and one derived status tone per component', () => {
    const card = mount(method);
    expect(card.props['aria-label']).toBe('Method card');
    expect(card.findByType('header').findByType('h2').children).toContain('Method card');
    expect(card.findAllByType('span').some(node => node.children.includes(method.title))).toBe(true);
    expect(card.findByProps({ 'data-testid': 'method-statement' }).children).toContain('The encoder feeds a queue of negatives.');
    const rows = card.findByProps({ 'data-testid': 'method-components' }).findAllByType('tr').slice(1);
    expect(rows.map(row => row.props['data-method-component'])).toEqual(['Encoder', 'Queue', 'Projection head', 'Momentum update', 'Temperature']);
    const statusCells = rows.map(row => row.findAll(node => node.props['data-method-status'] !== undefined)[0]);
    expect(statusCells.map(cell => cell.props['data-method-status'])).toEqual(['proven', 'contradicted', 'partial', 'untested', 'unchecked']);
    expect(statusCells.map(cell => cell.props['data-method-status-tone'])).toEqual(['ok', 'warning', 'caution', 'muted', 'pending']);
    expect(card.findByProps({ 'data-method-status-tone': 'warning' }).children).toContain('contradicted');
    expect(card.findByProps({ 'data-method-status-tone': 'pending' }).props.className).toContain('italic');
    const headers = card.findByProps({ 'data-testid': 'method-components' }).findAllByType('th').map(textOf);
    expect(headers).toEqual(['Component', 'Prescribes', 'Notes', 'Status', 'Tests']);
    expect(card.findAllByProps({ 'data-testid': 'method-card-truncated' })).toHaveLength(0);
  });

  it('summarises the tests per component and names the failing ones on hover', () => {
    const card = mount(method);
    const cells = card.findByProps({ 'data-testid': 'method-components' }).findAllByProps({ 'data-method-tests': 2 });
    expect(cells).toHaveLength(1);
    expect(textOf(cells[0])).toBe('2 tests · knockout ×1, invariant ×1');
    expect(cells[0].props['data-method-failing']).toBe(0);
    const failing = card.findByProps({ 'data-method-failing': 1 });
    expect(failing.props.title).toContain('tests/spec/test_differential.py::test_queue_matches_reference');
    expect(failing.props.title).toMatch(/^Failing/);
    expect(textOf(card.findByProps({ 'data-method-tests': 0 }))).toBe('—');
  });

  it('points at components that carry test markers but are missing from the card', () => {
    const markup = markupOf(method);
    expect(markup).toContain('data-testid="method-unlisted-components"');
    expect(markup).toContain('Data augmentation');
    expect(markupOf({ ...method, unlisted_components: [] })).not.toContain('method-unlisted-components');
    expect(markupOf({ ...method, unlisted_components: undefined })).not.toContain('method-unlisted-components');
  });

  it('shows the reused code the import scan found', () => {
    const card = mount(method);
    const reused = card.findByProps({ 'data-testid': 'method-reused-code' });
    const rows = reused.findAllByType('tr').slice(1);
    expect(rows.map(row => row.props['data-method-row'])).toEqual(['encoder-lib', 'numpy']);
    const cells = rows[0].findAllByType('td').map(textOf);
    expect(cells).toEqual(['encoder-lib', 'third_party clone', 'abc1234', 'encoder_lib.backbone, encoder_lib.heads', '2 files']);
    expect(rows[0].findAllByType('td')[0].props.title).toBe('https://example.invalid/encoder-lib.git');
    expect(rows[1].findAllByType('td').map(textOf)).toEqual(['numpy', 'package', '2.1.0', 'numpy', '1 file']);
  });

  it('shows the hyperparameters with a changed badge and the previous value', () => {
    const card = mount(method);
    const hyper = card.findByProps({ 'data-testid': 'method-hyperparameters' });
    const rows = hyper.findAllByType('tr').slice(1);
    expect(rows.map(row => row.props['data-method-row'])).toEqual(['queue.size', 'momentum']);
    expect(hyper.findAllByType('th').map(textOf)).toEqual(['Key', 'Value', 'Why']);
    const badges = hyper.findAllByProps({ 'data-testid': 'method-hyperparameter-changed' });
    expect(badges).toHaveLength(1);
    expect(rows[0].props['data-method-changed']).toBe('true');
    expect(rows[1].props['data-method-changed']).toBeUndefined();
    const badgeMarkup = markupOf(method);
    expect(badgeMarkup).toContain('changed');
    expect(badgeMarkup).toContain('32768');
    expect(badgeMarkup).toContain('configs/pretrain.yaml');
    expect(badgeMarkup).toContain('matches the route');
  });

  it('lists the change log from git and the latest host-run checks', () => {
    const card = mount(method);
    const log = card.findByProps({ 'data-testid': 'method-change-log' });
    const items = log.findAllByType('li');
    expect(items.map(item => item.findAllByType('span').map(textOf))).toEqual([
      ['2026-09-16', 'Add the momentum update'],
      ['2026-09-15', 'Card written from the selected route'],
    ]);
    expect(items[0].props.title).toBe('src/model.py');
    const checks = textOf(card.findByProps({ 'data-testid': 'method-checks' }));
    expect(checks).toContain('round 3');
    expect(checks).toContain('exit 1');
    expect(checks).toContain('PASSED 4, FAILED 1');
  });

  it('omits the derived sections that have nothing to show', () => {
    const markup = markupOf({ ...method, reused_code: [], hyperparameters: [], change_log: [], checks: null });
    expect(markup).not.toContain('method-reused-code');
    expect(markup).not.toContain('method-hyperparameters');
    expect(markup).not.toContain('method-change-log');
    expect(markup).not.toContain('method-checks');
    expect(markup).toContain('data-testid="method-components"');
  });

  it('shows the full markdown, the file name and a relative update time', () => {
    const markup = markupOf(method);
    expect(markup).toContain('data-testid="method-card-markdown"');
    expect(markup).toContain('Five seeds.');
    expect(markup).toContain('METHOD.md');
    expect(markup).toContain('2m ago');
  });

  it('accepts the update time as an ISO string and hides it when unknown', () => {
    const iso = new Date(Date.now() - 120_000).toISOString();
    expect(markupOf({ ...method, updated_at: iso })).toContain('2m ago');
    const unknown = markupOf({ ...method, updated_at: null });
    expect(unknown).toContain('METHOD.md');
    expect(unknown).not.toContain('updated');
    expect(methodDate('not a date')).toBeNull();
    expect(methodDate(1_700_000_000)?.getTime()).toBe(1_700_000_000_000);
    expect(methodDate(1_700_000_000_000)?.getTime()).toBe(1_700_000_000_000);
  });

  it('collapses and re-expands the body without dropping the header', () => {
    mount(method);
    const toggle = renderer!.root.findByProps({ 'aria-expanded': true });
    act(() => toggle.props.onClick());
    expect(renderer!.root.findAllByProps({ 'data-testid': 'method-card-body' })).toHaveLength(0);
    expect(renderer!.root.findByType('header').findByType('h2').children).toContain('Method card');
    act(() => renderer!.root.findByProps({ 'aria-expanded': false }).props.onClick());
    expect(renderer!.root.findAllByProps({ 'data-testid': 'method-card-body' })).toHaveLength(1);
  });

  it('starts collapsed in compact mode', () => {
    const markup = markupOf(method, true);
    expect(markup).toContain('data-compact="true"');
    expect(markup).not.toContain('data-testid="method-card-body"');
  });

  it('tells the reader when only the first 64 KiB is shown', () => {
    const markup = markupOf({ ...method, truncated: true });
    expect(markup).toContain('data-testid="method-card-truncated"');
    expect(markup).toContain('Showing only the first 64 KiB.');
  });

  it('explains a document without a component table and still shows the text', () => {
    const markup = markupOf({ ...method, components: [] });
    expect(markup).not.toContain('data-testid="method-components"');
    expect(markup).toContain('no component table');
    expect(markup).toContain('Five seeds.');
  });

  it('reads the document only for the active project', async () => {
    vi.useFakeTimers();
    const { sid } = inputs();
    const read = vi.spyOn(api, 'researchMethod').mockResolvedValue(method);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const flush = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(25); }); };
    await act(async () => { renderer = create(<QueryClientProvider client={client!}><MethodCard sid={sid} active={false} /></QueryClientProvider>); });
    await flush();
    expect(read).not.toHaveBeenCalled();
    expect(renderer!.root.findAllByProps({ 'data-testid': 'method-card' })).toHaveLength(0);
    await act(async () => { renderer!.update(<QueryClientProvider client={client!}><MethodCard sid={sid} active /></QueryClientProvider>); });
    await flush();
    expect(read).toHaveBeenCalledTimes(1);
    expect(read.mock.calls[0][0]).toBe(sid);
    expect(renderer!.root.findAllByProps({ 'data-testid': 'method-card' })).toHaveLength(1);
  });
});

describe('methodStatusTone', () => {
  it.each([
    ['proven', 'ok'], ['contradicted', 'warning'], ['partial', 'caution'], ['untested', 'muted'], ['unchecked', 'pending'],
    ['', 'unknown'], ['something else', 'unknown'],
  ])('maps the derived status %j to the %s tone', (status, tone) => {
    expect(methodStatusTone(status)).toBe(tone);
  });
});

describe('summarizeTests', () => {
  const en = (_zh: string, english: string) => english;
  it('counts tests by kind and lists failing ids in the title', () => {
    const summary = summarizeTests([
      { id: 'a::t1', kind: 'knockout', outcome: 'PASSED' },
      { id: 'a::t2', kind: 'knockout', outcome: 'FAILED' },
      { id: 'b::t3', kind: 'parity', outcome: 'ERROR' },
    ], en);
    expect(summary.label).toBe('3 tests · knockout ×2, parity ×1');
    expect(summary.failing).toBe(2);
    expect(summary.title).toBe('Failing: a::t2\nb::t3');
  });
  it('lists every test with its outcome when nothing fails, and treats a missing kind as invariant', () => {
    const summary = summarizeTests([{ id: 'a::t1', kind: '', outcome: null }], en);
    expect(summary.label).toBe('1 test · invariant ×1');
    expect(summary.failing).toBe(0);
    expect(summary.title).toBe('a::t1 — not run');
    expect(summarizeTests([], en)).toEqual({ label: '—', title: 'No test carries this component marker', failing: 0 });
  });
});
