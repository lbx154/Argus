import { act, create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import type { AttachmentUploadOptions, AttachmentUploadResponse, UploadedAttachment } from '../api';
import { ComposerAttachmentChip } from '../components/ComposerAttachmentChip';
import { DraftUploadContext, DraftUploads, type DraftUploadStatus, type DraftUploadView } from '../lib/draftUploads';

const LIMITS = { max_count: 5, max_bytes_per_file: 100, max_total_bytes: 500 };
const receipt = (file: File, id: string): UploadedAttachment => ({
  attachment_id: id, relative_path: `.argus/attachments/s-A/${id}/${file.name}`, original_name: file.name,
  stored_name: file.name, mime: 'text/plain', size_bytes: file.size,
});

function harness() {
  const calls: Array<{ file: File; signal: AbortSignal; options: AttachmentUploadOptions; resolve: (id: string) => void; reject: (error: unknown) => void }> = [];
  const upload = vi.fn((_sid: string, files: File[], signal: AbortSignal, options: AttachmentUploadOptions) =>
    new Promise<AttachmentUploadResponse>((resolve, reject) => {
      calls.push({ file: files[0], signal, options, reject, resolve: (id) => resolve({ attachments: [receipt(files[0], id)], limits: LIMITS }) });
    }));
  const discard = vi.fn(async () => ({}));
  const uploaded = vi.fn();
  return { calls, upload, discard, uploaded, store: new DraftUploads(upload, discard, uploaded) };
}
const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

describe('files upload as soon as they are chosen', () => {
  it('uploads one file at a time, reports progress, and records each arrival', async () => {
    const { calls, store, uploaded } = harness();
    const first = new File(['aaaa'], 'a.txt');
    const second = new File(['bb'], 'b.txt');
    store.ensure('s-A', [first, second]);
    await flush();
    expect(calls.map((call) => call.file)).toEqual([first]);
    expect(store.status(second)?.phase).toBe('queued');
    expect(store.busy([first, second])).toBe(2);

    calls[0].options.onProgress?.({ name: 'a.txt', index: 1, count: 1, loaded: 2, total: 4, phase: 'uploading' });
    expect(store.status(first)).toMatchObject({ phase: 'uploading', loaded: 2, total: 4 });
    calls[0].resolve('att-000000000001');
    await flush();
    expect(store.status(first)?.phase).toBe('ready');
    expect(uploaded).toHaveBeenCalledWith('s-A', first, expect.objectContaining({ attachment_id: 'att-000000000001' }));
    expect(calls.map((call) => call.file)).toEqual([first, second]);
  });

  it('cancels an upload in flight and deletes a file that already arrived', async () => {
    const { calls, store, discard } = harness();
    const moving = new File(['x'], 'moving.txt');
    const done = new File(['y'], 'done.txt');
    store.ensure('s-A', [done]);
    await flush();
    calls[0].resolve('att-000000000002');
    await flush();
    store.ensure('s-A', [moving]);
    await flush();
    store.cancel(moving);
    expect(calls[1].signal.aborted).toBe(true);
    expect(store.status(moving)).toBeUndefined();
    store.cancel(done);
    expect(discard).toHaveBeenCalledWith('s-A', 'att-000000000002');
  });

  it('lets a send wait for files in flight without uploading them twice, and keeps them if the draft changes', async () => {
    const { calls, store, upload } = harness();
    const file = new File(['abc'], 'report.txt');
    store.ensure('s-A', [file]);
    await flush();
    const controller = new AbortController();
    const settled = store.settle('s-A', [file], new Map(), controller.signal);
    store.cancel(file);
    expect(calls[0].signal.aborted).toBe(false);
    calls[0].resolve('att-000000000003');
    const result = await settled;
    expect(result.get(file)?.attachment_id).toBe('att-000000000003');
    expect(upload).toHaveBeenCalledTimes(1);
  });

  it('stopping the wait leaves the upload running; a failure blocks the send until retried', async () => {
    const { calls, store } = harness();
    const file = new File(['abc'], 'report.txt');
    store.ensure('s-A', [file]);
    await flush();
    const controller = new AbortController();
    const waiting = store.settle('s-A', [file], new Map(), controller.signal);
    controller.abort();
    await expect(waiting).rejects.toThrow(/Aborted/);
    expect(calls[0].signal.aborted).toBe(false);
    calls[0].reject(new Error('disk full'));
    await flush();
    expect(store.status(file)).toMatchObject({ phase: 'failed' });
    store.retry(file);
    await flush();
    expect(calls).toHaveLength(2);
    expect(store.status(file)?.phase).toBe('queued');
  });
});

describe('the attachment chip shows its own upload', () => {
  const view = (status?: DraftUploadStatus): DraftUploadView => ({
    status: () => status, retry: vi.fn(), ready: [], removeReady: vi.fn(), busy: status && status.phase !== 'ready' ? 1 : 0,
  });
  const render = (status?: DraftUploadStatus, file = new File(['0123456789'], 'data.csv')) => {
    let tree!: ReturnType<typeof create>;
    act(() => {
      tree = create(<DraftUploadContext.Provider value={view(status)}>
        <ComposerAttachmentChip file={file} removeLabel="Remove data.csv" onRemove={vi.fn()} />
      </DraftUploadContext.Provider>);
    });
    return tree;
  };
  const text = (tree: ReturnType<typeof create>) => JSON.stringify(tree.toJSON());

  it('draws a progress bar with the percentage while uploading', () => {
    const tree = render({ phase: 'uploading', loaded: 5, total: 10 });
    const bar = tree.root.find((node) => node.props.role === 'progressbar');
    expect(bar.props['aria-valuenow']).toBe(50);
    expect(text(tree)).toContain('Uploading 50%');
  });

  it('says when the file arrived, and offers a retry when it failed', () => {
    const ready = render({ phase: 'ready', loaded: 10, total: 10 });
    expect(text(ready)).toContain('✓ Uploaded');
    expect(ready.root.findAll((node) => node.props.role === 'progressbar')).toHaveLength(0);
    const failed = render({ phase: 'failed', loaded: 3, total: 10, error: new Error('disk full') });
    expect(text(failed)).toContain('Upload failed');
    expect(text(failed)).toContain('disk full');
    expect(failed.root.findAll((node) => node.type === 'button' && node.children.includes('Retry'))).toHaveLength(1);
  });

  it('shows a file uploaded before a reload as already uploaded', () => {
    let tree!: ReturnType<typeof create>;
    act(() => {
      tree = create(<DraftUploadContext.Provider value={view()}>
        <ComposerAttachmentChip uploaded={receipt(new File(['abc'], 'old.md'), 'att-000000000004')} removeLabel="Remove old.md" onRemove={vi.fn()} />
      </DraftUploadContext.Provider>);
    });
    expect(text(tree)).toContain('old.md');
    expect(text(tree)).toContain('✓ Uploaded');
  });
});
