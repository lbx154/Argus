import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import type { ArtifactInfo, DeliveryReceipt } from '../../../core/src/types';
import { ArtifactModal } from '../components/ArtifactModal';
import { Modal } from '../components/Modal';
import { PdfPreview } from '../components/PdfPreview';
import { api } from '../api';
import { useArtifact } from '../hooks';
import { loadPdfEngine } from '../lib/pdfEngine';
import deliveredRecord from '../../../../packages/core/test/fixtures/delivery-versions/record.json';

const artifact = vi.hoisted(() => ({ data: undefined as ArtifactInfo | undefined, isError: false, error: undefined as unknown }));
vi.mock('../hooks', () => ({ useArtifact: vi.fn(() => ({ ...artifact, isLoading: false })) }));
vi.mock('../components/HtmlPreview', () => ({ HtmlPreview: () => null }));
vi.mock('../lib/pdfEngine', () => ({ loadPdfEngine: vi.fn(), resetPdfEngine: vi.fn() }));

let tree: ReactTestRenderer | undefined;
afterEach(() => { act(() => tree?.unmount()); tree = undefined; artifact.data = undefined; artifact.isError = false; artifact.error = undefined; vi.restoreAllMocks(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

it('previews a project file by its name and size, edits it, and opens registered Markdown links', () => {
  artifact.data = {
    path: 'report.md', name: 'report.md',
    kind: 'markdown', mime: 'text/markdown', size: 48, mtime: 1,
    preview: '# Report\n\nSee [the table](results/table.csv).',
  };
  const onEdit = vi.fn();
  const onSelectPath = vi.fn();
  act(() => { tree = create(<ArtifactModal sid="s" path="report.md" onEdit={onEdit} onSelectPath={onSelectPath}
    artifacts={[{ path: 'results/table.csv' }]} onClose={() => {}} />); });
  const heading = tree!.root.findByType('h2');
  expect(heading.children).toEqual(['report.md']);
  expect(heading.props.title).toBe('report.md');
  expect(tree!.root.findAllByType('p').some(node => node.children.includes('48 B'))).toBe(true);
  act(() => tree!.root.find(node => node.type === 'button' && node.children.includes('Edit & versions')).props.onClick());
  expect(onEdit).toHaveBeenCalledExactlyOnceWith('report.md');
  act(() => tree!.root.findByProps({ 'data-artifact-path': 'results/table.csv' }).props.onClick({ preventDefault() {} }));
  expect(onSelectPath).toHaveBeenCalledExactlyOnceWith('results/table.csv');
});

it('reads a delivery summary as Markdown whose links open the delivered files', () => {
  const onSelectPath = vi.fn();
  const delivery = {
    schema_version: 1, delivery_id: 'd1', kind: 'task_completed', item_id: '', title: 'Report', status: 'done', review_status: '',
    delivered_at: 1, summary: '# Report\n\nSee [the table](results/table.csv) and [the paper](https://example.org/paper).',
    targets: [{ path: 'results/table.csv', label: 'table.csv', source: 'manager', }], primary_target: null,
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
    targets: [{ path: main, label: main.split('/').at(-1), source: 'manager', }], primary_target: { path: main, label: main.split('/').at(-1), source: 'manager', },
  } as unknown as DeliveryReceipt;
  act(() => { tree = create(<ArtifactModal sid="s" path={null} delivery={delivery} onClose={() => {}} />); });
  expect(tree!.root.findAllByType('h2').map(node => node.children.join(''))).toContain(heading);
});

it('takes a full-screen preview back to the dialog on Esc, and closes the dialog on the next', () => {
  const onClose = vi.fn();
  const delivery = {
    schema_version: 1, delivery_id: 'd1', kind: 'task_completed', item_id: '', title: 'Page', status: 'done', review_status: '', delivered_at: 1, summary: '',
    targets: [{ path: 'index.html', label: 'index.html', source: 'manager', }], primary_target: null,
  } as unknown as DeliveryReceipt;
  act(() => { tree = create(<ArtifactModal sid="s" path="index.html" delivery={delivery} onClose={onClose} />); });
  act(() => tree!.root.findByProps({ 'aria-label': 'Full screen preview' }).props.onClick());
  const escape = () => act(() => tree!.root.findByType(Modal).props.onClose());
  escape();
  expect(onClose).not.toHaveBeenCalled();
  expect(tree!.root.findAllByProps({ 'aria-label': 'Full screen preview' })).toHaveLength(1);
  escape();
  expect(onClose).toHaveBeenCalledOnce();
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


it('selects delivery-specific queries, distinguishes repeated names, and offers only one zip button for one HTML file', async () => {
  const file = { path: 'index.html', label: 'index.html', source: 'manager', delivery_number: 1 };
  const first: DeliveryReceipt = { schema_version: 1, delivery_id: 'd1', title: 'Same title', kind: 'result', item_id: '', summary: '',
    status: 'done', review_status: '', delivered_at: 1, targets: [file], primary_target: file };
  const second = { ...first, delivery_id: 'd2', delivered_at: 2, targets: [{ ...file, delivery_number: 2 }], primary_target: { ...file, delivery_number: 2 } };
  const onSelectDelivery = vi.fn();
  // Keep the preview data absent so no browser iframe is needed to test the controls.
  act(() => { tree = create(<ArtifactModal sid="s" path="index.html" delivery={first} deliveries={[second, first]}
    onSelectDelivery={onSelectDelivery} onClose={() => {}} />); });
  expect(useArtifact).toHaveBeenLastCalledWith('s', 'index.html', null, 'd1');
  expect(tree!.root.findAllByType('option').map(option => option.children.join(''))).toEqual([`Delivery 2 · ${new Date(2000).toLocaleString('en')}`, `Delivery 1 · ${new Date(1000).toLocaleString('en')}`]);
  expect(JSON.stringify(tree!.toJSON()).match(/Delivery 2/g)).toHaveLength(1);
  expect(tree!.root.findAll(node => node.type === 'button' && node.children.some(child => typeof child === 'string' && child.includes('(zip)')))).toHaveLength(0);
  const select = tree!.root.findByType('select');
  act(() => select.props.onChange({ target: { value: 'd2' } }));
  expect(onSelectDelivery).toHaveBeenCalledWith(second, 'index.html');
  // A single HTML file uses its existing page-and-assets zip; it has no Download all button.
  artifact.data = { path: file.path, name: file.path, kind: 'html', mime: 'text/html', size: 10, mtime: 1 };
  act(() => tree!.update(<ArtifactModal sid="s" path="index.html" delivery={second} onClose={() => {}} />));
  expect(useArtifact).toHaveBeenLastCalledWith('s', 'index.html', null, 'd2');
  expect(tree!.root.findAll(node => node.type === 'button' && node.children.includes('Download page (zip)'))).toHaveLength(1);
  expect(tree!.root.findAll(node => node.type === 'button' && node.children.includes('Download all (zip)'))).toHaveLength(0);
});

it('lists only the selected file versions with that file’s recorded count and keeps its path on selection', () => {
  const report = { path: 'report.md', label: 'report.md', source: 'manager', delivery_number: 3 };
  const data = { path: 'data.csv', label: 'data.csv', source: 'manager', delivery_number: 2 };
  const latest: DeliveryReceipt = { schema_version: 1, delivery_id: 'd3', title: 'Report and data', kind: 'result', item_id: '', summary: '',
    status: 'done', review_status: '', delivered_at: 3, targets: [report, data], primary_target: report };
  const previous = { ...latest, delivery_id: 'd2', delivered_at: 2, targets: [{ ...report, delivery_number: 2 }, { ...data, delivery_number: 1 }] };
  const reportOnly = { ...latest, delivery_id: 'd1', delivered_at: 1, targets: [{ ...report, delivery_number: 1 }], primary_target: { ...report, delivery_number: 1 } };
  const onSelectDelivery = vi.fn();
  act(() => { tree = create(<ArtifactModal sid="s" path="data.csv" delivery={latest} deliveries={[latest, previous, reportOnly]}
    onSelectDelivery={onSelectDelivery} onClose={() => {}} />); });
  const select = tree!.root.findByProps({ 'aria-label': 'File versions' });
  expect(select.findAllByType('option').map(option => [option.props.value, option.children.join('')])).toEqual([
    ['d3', `Delivery 2 · ${new Date(3000).toLocaleString('en')}`],
    ['d2', `Delivery 1 · ${new Date(2000).toLocaleString('en')}`],
  ]);
  act(() => select.props.onChange({ target: { value: 'd2' } }));
  expect(onSelectDelivery).toHaveBeenCalledWith(previous, 'data.csv');
  act(() => tree!.update(<ArtifactModal sid="s" path="data.csv" delivery={previous} deliveries={[previous, reportOnly]} onClose={() => {}} />));
  expect(tree!.root.findAllByType('select')).toHaveLength(0);
});

it('keeps the selected delivery in file downloads and archive downloads', async () => {
  const file = { path: 'report.md', label: 'report.md', source: 'manager', delivery_number: 2 };
  const delivery: DeliveryReceipt = { schema_version: 1, delivery_id: 'd2', title: 'Report', kind: 'result', item_id: '', summary: 'Explanation',
    status: 'done', review_status: '', delivered_at: 2, targets: [file, { ...file, path: 'data.csv' }], primary_target: file };
  artifact.data = { path: file.path, name: file.path, kind: 'markdown', mime: 'text/markdown', size: 10, mtime: 1, preview: 'Saved content' };
  const blob = vi.spyOn(api, 'artifactBlob').mockRejectedValue(new Error('stop before browser download'));
  const bundle = vi.spyOn(api, 'artifactsBundle').mockRejectedValue(new Error('stop before browser download'));
  act(() => { tree = create(<ArtifactModal sid="s" path="report.md" delivery={delivery} onClose={() => {}} />); });
  await act(async () => tree!.root.find(node => node.type === 'button' && node.children.includes('Download')).props.onClick());
  expect(blob).toHaveBeenCalledWith('s', 'report.md', true, undefined, 'd2');
  await act(async () => tree!.root.find(node => node.type === 'button' && node.children.includes('Download all (zip)')).props.onClick());
  expect(bundle).toHaveBeenCalledWith('s', ['report.md', 'data.csv'], 'Report', '# Report\n\nExplanation', undefined, 'd2');
});

it('offers Revise only for the latest delivery of each file in the recorded repeated deliveries', () => {
  const deliveries: DeliveryReceipt[] = deliveredRecord.text.events.flatMap((event, line) => event.type === 'handoff' && 'open' in event ? [{
    schema_version: 1, delivery_id: `d${line}`, kind: 'result', item_id: '', title: event.title!, summary: event.letter!,
    status: 'done', review_status: '', delivered_at: Date.parse(event.ts) / 1000, primary_target: null,
    targets: event.open!.map(path => ({ path, label: path, source: 'manager' })), snapshots: event.snapshots,
  }] : []);
  const revise = vi.fn();
  artifact.data = { path: 'report.md', name: 'report.md', kind: 'text', mime: 'text/plain', size: 37, mtime: 1 };
  const show = (delivery?: DeliveryReceipt, path = 'report.md') => act(() => {
    const modal = <ArtifactModal sid="s" path={path} delivery={delivery} deliveries={deliveries} onRevise={revise} onClose={() => {}} />;
    if (tree) tree.update(modal); else tree = create(modal);
  });
  const buttons = () => tree!.root.findAll(node => node.type === 'button' && node.children.includes('Revise'));
  show(deliveries[0]); expect(buttons()).toHaveLength(0);
  // The repeated second version is still an older delivery occurrence.
  show(deliveries[1]); expect(buttons()).toHaveLength(0);
  show(deliveries[2]); expect(buttons()).toHaveLength(1);
  act(() => buttons()[0].props.onClick()); expect(revise).toHaveBeenLastCalledWith('report.md');
  // A later delivery of a different path leaves this file's newest edition editable.
  deliveries.push({ ...deliveries[2], delivery_id: 'other', delivered_at: 100, primary_target: null, targets: [{ path: 'other.md', label: 'other.md', source: 'manager' }] });
  show(deliveries[2]); expect(buttons()).toHaveLength(1);
  show(); expect(buttons()).toHaveLength(1);
});
