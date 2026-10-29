import before from '../../../../packages/core/test/fixtures/conversation-fixes/before.json';
import after from '../../../../packages/core/test/fixtures/conversation-fixes/after.json';
import { EventStream } from './serverSnapshot';
import type { UploadedAttachment } from '../api';
import { renderToStaticMarkup } from 'react-dom/server';
import { act, create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { ComposerAttachmentChip } from '../components/ComposerAttachmentChip';
import {
  splitMessageAttachments,
  addComposerFiles,
  attachmentMime,
  dataTransferHasFiles,
  extractFilesFromDataTransfer,
} from '../lib/attachments';

const limits = { max_count: 5, max_bytes_per_file: 16, max_total_bytes: 32, chunk_bytes: 4 };
describe('composer attachment helpers', () => {
  it('accepts supported files and deduplicates exact re-adds', () => {
    const note = new File(['# note\n'], 'notes.md', { type: 'text/markdown' });

    const first = addComposerFiles([], [note]);
    const second = addComposerFiles([note], [note]);

    expect(first.accepted).toEqual([note]);
    expect(first.issues).toEqual([]);
    expect(second.accepted).toEqual([]);
  });

  it('accepts any suffix and rejects files exceeding server limits with stable codes', () => {
    const unsupported = new File(['hello'], 'brief.exe', { type: 'application/octet-stream' });
    const tooLarge = new File(
      [new Uint8Array(limits.max_bytes_per_file + 1)],
      'big.pdf',
      { type: 'application/pdf' },
    );
    const crowd = Array.from({ length: limits.max_count + 1 }, (_, index) => (
      new File([`${index}`], `note-${index}.txt`, { type: 'text/plain' })
    ));

    expect(addComposerFiles([], [unsupported], limits).accepted).toEqual([unsupported]);
    expect(addComposerFiles([], [tooLarge], limits).issues[0]).toMatchObject({
      code: 'too-large',
      fileName: 'big.pdf',
    });
    expect(addComposerFiles([], crowd, limits).issues.at(-1)).toEqual({
      code: 'too-many',
      limitCount: limits.max_count,
    });
  });

  it('extracts pasted or dropped files from clipboard-like payloads', () => {
    const image = new File([new Uint8Array([1, 2, 3])], 'paste.png', { type: 'image/png' });
    const transfer = {
      types: ['Files'],
      items: [{ kind: 'file', getAsFile: () => image }],
    };

    expect(dataTransferHasFiles(transfer)).toBe(true);
    expect(extractFilesFromDataTransfer(transfer)).toEqual([image]);
  });
});

describe('composer attachment chip', () => {
  it('renders the file name, size, and canonical MIME label', () => {
    const file = new File(['alpha,beta\n1,2\n'], 'table.csv', { type: 'text/csv' });
    const html = renderToStaticMarkup(
      <ComposerAttachmentChip
        file={file}
        removeLabel="remove attachment table.csv"
        onRemove={() => undefined}
      />,
    );

    expect(attachmentMime(file)).toBe('text/csv');
    expect(html).toContain('table.csv');
    expect(html).toContain('15 B');
    expect(html).toContain('text/csv');
    expect(html).toContain('remove attachment table.csv');
  });
});

describe('browser record: operator attachments', () => {
  const old = before.events.find(event => event.type === 'message' && event.text?.includes('Attached, in the workdir:'))!;
  const sent = after.requests[2];
  const files = sent.attachments as UploadedAttachment[];
  it('restores only the old attachment tail to the existing file block', () => {
    const message = splitMessageAttachments(old.text!);
    expect(message.text).toBe(before.requests[2].text);
    expect(message.attachments).toMatchObject([{ original_name: '问题材料.csv', relative_path: expect.stringContaining('.argus/attachments/') }]);
    const html = renderToStaticMarkup(<EventStream events={[{ type: 'ui.operator', text: old.text, ts: 1 }]} connected showReasoning={false} />);
    expect(html).toContain('data-attachment-id=');
    expect(html).toContain('问题材料.csv');
    expect(html).not.toContain('Attached, in the workdir:');
    expect(html).not.toContain('0 B');
    expect(splitMessageAttachments('Attached, in the workdir: is a quoted heading')).toEqual({ text: 'Attached, in the workdir: is a quoted heading', attachments: [] });
    expect(splitMessageAttachments(old.text! + '\nThis is still the operator’s text.').text).toBe(old.text! + '\nThis is still the operator’s text.');
  });
  it('keeps new words exactly as sent and shows one non-removable uploaded file block', () => {
    expect(splitMessageAttachments(sent.text, files)).toEqual({ text: sent.text, attachments: files });
    const html = renderToStaticMarkup(<EventStream events={[{ type: 'ui.operator', text: sent.text, attachments: files, ts: 1 }]} connected showReasoning={false} />);
    expect(html.match(/data-attachment-id=/g)).toHaveLength(1);
    expect(html).toContain('8 B');
    expect(html).toContain('问题材料.csv');
    expect(html).not.toContain('Attached, in the workdir:');
    expect(html).not.toContain('aria-label="remove');
    expect(html).not.toMatch(/✓|Uploaded|已上传/);
  });
});

it('opens a sent attachment through the existing file reader and labels the message action as editing', () => {
  vi.stubGlobal('window', { requestAnimationFrame: () => 1, cancelAnimationFrame: vi.fn() });
  const sent = after.requests[2];
  const files = sent.attachments as UploadedAttachment[];
  const open = vi.fn();
  const edit = vi.fn();
  let view!: ReturnType<typeof create>;
  act(() => { view = create(<EventStream events={[{ type: 'ui.operator', text: sent.text, attachments: files, ts: 1 }]}
    connected showReasoning={false} onOpenArtifact={open} onConversationAction={edit} />); });
  try {
    const file = view.root.findAllByType('button').find(node => node.children.includes(files[0].original_name))!;
    act(() => file.props.onClick());
    expect(open).toHaveBeenCalledExactlyOnceWith(files[0].relative_path);
    const button = view.root.findByProps({ 'aria-label': 'Edit and resend' });
    act(() => button.props.onClick());
    expect(edit).toHaveBeenCalledExactlyOnceWith(sent.text);
  } finally { act(() => view.unmount()); vi.unstubAllGlobals(); }
});

it('omits text/plain from sent attachment blocks', () => {
  const file: UploadedAttachment = { attachment_id: 'att-000000000001', relative_path: '.argus/attachments/att-000000000001/trace.jsonl', original_name: 'trace.jsonl', stored_name: 'trace.jsonl', mime: 'text/plain', size_bytes: 12 };
  const html = renderToStaticMarkup(<ComposerAttachmentChip uploaded={file} sent />);
  expect(html).toContain('trace.jsonl');
  expect(html).not.toContain('text/plain');
});
