import { useEffect, useState, type KeyboardEvent } from 'react';
import { Modal, ModalHeader } from './Modal';

export interface PendingReply {
  id: string;
  title: string;
  question: string;
}

export function PendingReplyDialog({
  reply,
  open,
  busy,
  onClose,
  onSubmit,
}: {
  reply: PendingReply | null;
  open: boolean;
  busy: boolean;
  onClose: () => void;
  onSubmit: (text: string) => void;
}) {
  const [text, setText] = useState('');
  useEffect(() => {
    if (open) setText('');
  }, [open, reply?.id]);
  if (!reply) return null;

  const submit = () => {
    const answer = text.trim();
    if (!answer || busy) return;
    onSubmit(answer);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <Modal open={open} onClose={busy ? () => undefined : onClose} label="Answer task question" width="max-w-lg">
      <ModalHeader title="Answer required" sub={reply.title || 'Blocked task'} />
      <div className="space-y-4 px-5 py-4">
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink">{reply.question}</p>
        <textarea
          data-autofocus
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
          rows={4}
          disabled={busy}
          placeholder="Type the answer that should go directly back to this task…"
          className="w-full resize-y rounded-lg border border-line bg-bg px-3 py-2 text-sm leading-relaxed text-ink outline-none focus:border-blue disabled:opacity-60"
        />
        <div className="flex items-center justify-between gap-3">
          <span className="text-xs text-ink-faint">Ctrl/⌘ Enter to send directly to the process</span>
          <div className="flex gap-2">
            <button type="button" onClick={onClose} disabled={busy} className="rounded-md px-3 py-2 text-xs text-ink-dim hover:bg-bg disabled:opacity-50">
              Later
            </button>
            <button type="button" onClick={submit} disabled={busy || !text.trim()} className="rounded-md bg-blue-deep px-3 py-2 text-xs font-medium text-white hover:bg-blue-deep/85 disabled:opacity-50">
              {busy ? 'Sending…' : 'Send answer'}
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
