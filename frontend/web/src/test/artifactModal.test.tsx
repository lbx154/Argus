import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import type { DeliveryReceipt } from '../../../core/src/types';
import { ArtifactModal } from '../components/ArtifactModal';
import { PdfPreview } from '../components/PdfPreview';
import { loadPdfEngine } from '../lib/pdfEngine';

vi.mock('../hooks', () => ({ useArtifact: () => ({ data: undefined, isLoading: false, isError: false }) }));
vi.mock('../lib/pdfEngine', () => ({ loadPdfEngine: vi.fn(), resetPdfEngine: vi.fn() }));

let tree: ReactTestRenderer | undefined;
afterEach(() => { act(() => tree?.unmount()); tree = undefined; vi.unstubAllGlobals(); });

it('reads a delivery summary as Markdown whose links open the delivered files', () => {
  const onSelectPath = vi.fn();
  const delivery = {
    schema_version: 1, delivery_id: 'd1', kind: 'task_completed', item_id: '', title: 'Report', status: 'done', review_status: '',
    delivered_at: 1, summary: '# Report\n\nSee [the table](results/table.csv) and [the paper](https://example.org/paper).',
    targets: [{ path: 'results/table.csv', label: 'table.csv', source: 'manager', why: '' }], primary_target: null,
  } as unknown as DeliveryReceipt;
  act(() => { tree = create(<ArtifactModal sid="s" path={null} delivery={delivery} onSelectPath={onSelectPath} onClose={() => {}} />); });
  expect(tree!.root.findAllByType('h1').map(node => node.children.join(''))).toContain('Report');
  const local = tree!.root.find(node => node.props['data-artifact-path'] === 'results/table.csv');
  act(() => local.props.onClick({ preventDefault() {} }));
  expect(onSelectPath).toHaveBeenCalledWith('results/table.csv');
  expect(tree!.root.find(node => node.type === 'a' && node.props.href === 'https://example.org/paper').props.target).toBe('_blank');
});

it.each([
  ['Primes and Fibonacci', 'out/report.md', 'Primes and Fibonacci'],
  ['', 'out/table.csv', 'table.csv'],
])('names a result as the server named it, else by its main file (%#)', (title, main, heading) => {
  const delivery = {
    schema_version: 1, delivery_id: 'd1', kind: 'task_completed', item_id: '', title, status: 'done', review_status: '', delivered_at: 1, summary: '',
    targets: [{ path: main, label: main.split('/').at(-1), source: 'manager', why: '' }], primary_target: { path: main, label: main.split('/').at(-1), source: 'manager', why: '' },
  } as unknown as DeliveryReceipt;
  act(() => { tree = create(<ArtifactModal sid="s" path={null} delivery={delivery} onClose={() => {}} />); });
  expect(tree!.root.findAllByType('h2').map(node => node.children.join(''))).toContain(heading);
});

it('loads a PDF again when its preview is retried', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.mocked(loadPdfEngine).mockRejectedValue(new Error('worker failed'));
  await act(async () => { tree = create(<PdfPreview src="/api/projects/s/artifact/raw?path=a.pdf" name="a.pdf" />); });
  expect(loadPdfEngine).toHaveBeenCalledTimes(1);
  const retry = tree!.root.find(node => node.type === 'button' && node.children.includes('Retry preview'));
  await act(async () => retry.props.onClick());
  expect(loadPdfEngine).toHaveBeenCalledTimes(2);
});
