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
  MESSAGE_ATTACHMENT_MAX_COUNT,
  MESSAGE_ATTACHMENT_MAX_BYTES,
} from '../lib/attachments';

describe('composer attachment helpers', () => {
  it('accepts supported files and deduplicates exact re-adds', () => {
    const note = new File(['# note\n'], 'notes.md', { type: 'text/markdown' });

    const first = addComposerFiles([], [note]);
    const second = addComposerFiles([note], [note]);

    expect(first.accepted).toEqual([note]);
    expect(first.issues).toEqual([]);
    expect(second.accepted).toEqual([]);
  });

  it('rejects unsupported, oversized, and excess files with stable codes', () => {
    const unsupported = new File(['hello'], 'brief.exe', { type: 'application/octet-stream' });
    const tooLarge = new File(
      [new Uint8Array(MESSAGE_ATTACHMENT_MAX_BYTES + 1)],
      'big.pdf',
      { type: 'application/pdf' },
    );
    const crowd = Array.from({ length: MESSAGE_ATTACHMENT_MAX_COUNT + 1 }, (_, index) => (
      new File([`${index}`], `note-${index}.txt`, { type: 'text/plain' })
    ));

    expect(addComposerFiles([], [unsupported]).issues[0]).toEqual({
      code: 'unsupported',
      fileName: 'brief.exe',
    });
    expect(addComposerFiles([], [tooLarge]).issues[0]).toMatchObject({
      code: 'too-large',
      fileName: 'big.pdf',
    });
    expect(addComposerFiles([], crowd).issues.at(-1)).toEqual({
      code: 'too-many',
      limitCount: MESSAGE_ATTACHMENT_MAX_COUNT,
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
