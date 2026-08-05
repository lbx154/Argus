import { useEffect, useMemo, useState, type KeyboardEvent } from 'react';
import type { OperatorDecisionCard } from '../../../core/src/decisions';
import { Modal, ModalHeader } from './Modal';

export type PendingReply = OperatorDecisionCard;

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
  onSubmit: (optionId: string, note: string) => void;
}) {
  const defaultOption = useMemo(
    () => reply?.options.find((option) => option.id === 'recommended')?.id
      ?? reply?.options[0]?.id
      ?? 'custom',
    [reply],
  );
  const [optionId, setOptionId] = useState(defaultOption);
  const [note, setNote] = useState('');
  useEffect(() => {
    if (!open) return;
    setOptionId(defaultOption);
    setNote('');
  }, [defaultOption, open, reply?.id]);
  if (!reply) return null;

  const selected = reply.options.find((option) => option.id === optionId);
  const canSubmit = Boolean(selected && (!selected.requires_note || note.trim()));
  const submit = () => {
    if (!busy && canSubmit) onSubmit(optionId, note.trim());
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <Modal open={open} onClose={busy ? () => undefined : onClose} label="Operator decision" width="max-w-2xl">
      <ModalHeader title="Decision required" sub={reply.title} />
      <div className="space-y-4 px-5 py-4">
        {reply.reason ? (
          <section className="rounded-md border border-gold/30 bg-gold/5 p-3">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-gold">Why work is blocked</div>
            <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-ink">{reply.reason}</p>
          </section>
        ) : null}

        {reply.evidence.length ? (
          <section>
            <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">Evidence</div>
            <div className="space-y-2">
              {reply.evidence.map((row, index) => (
                <div key={`${row.path}:${index}`} className="rounded border border-line/70 bg-bg/40 p-2.5">
                  <div className="text-xs font-medium text-ink">{row.label}</div>
                  {row.summary ? <div className="mt-1 text-xs text-ink-dim">{row.summary}</div> : null}
                  {row.path ? <div className="mt-1 break-all font-mono text-[10px] text-blue-sky">{row.path}</div> : null}
                </div>
              ))}
            </div>
          </section>
        ) : null}

        <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink">{reply.question}</p>

        <div className="space-y-2">
          {reply.options.map((option) => (
            <button
              key={option.id}
              type="button"
              onClick={() => setOptionId(option.id)}
              disabled={busy}
              className={`w-full rounded-md border p-3 text-left ${
                optionId === option.id ? 'border-blue bg-blue/5' : 'border-line bg-bg/30'
              }`}
            >
              <div className="text-sm font-medium text-ink">{option.label}</div>
              <div className="mt-1 text-xs leading-relaxed text-ink-dim">{option.description}</div>
            </button>
          ))}
        </div>

        {selected?.requires_note || note ? (
          <textarea
            data-autofocus
            value={note}
            onChange={(event) => setNote(event.target.value)}
            onKeyDown={onKeyDown}
            rows={3}
            disabled={busy}
            placeholder="Add the guidance the Manager should apply…"
            className="w-full resize-y rounded-lg border border-line bg-bg px-3 py-2 text-sm leading-relaxed text-ink outline-none focus:border-blue disabled:opacity-60"
          />
        ) : null}

        <div className="flex items-center justify-between gap-3">
          <span className="text-xs text-ink-faint">The Manager applies your choice before work resumes.</span>
          <div className="flex gap-2">
            <button type="button" onClick={onClose} disabled={busy} className="rounded-md px-3 py-2 text-xs text-ink-dim hover:bg-bg disabled:opacity-50">Later</button>
            <button type="button" onClick={submit} disabled={busy || !canSubmit} className="rounded-md bg-blue-deep px-3 py-2 text-xs font-medium text-white hover:bg-blue-deep/85 disabled:opacity-50">
              {busy ? 'Applying…' : optionId === 'stop' ? 'Stop campaign' : 'Use this option'}
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
