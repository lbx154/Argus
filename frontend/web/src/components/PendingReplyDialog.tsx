import { useEffect, useState, type KeyboardEvent } from 'react';
import type { OperatorDecisionCard } from '../../../core/src/decisions';
import { Modal, ModalHeader } from './Modal';
import { isImeComposing } from '../lib/ime';
import { useI18n } from '../i18n';
import { PendingDecisionContext } from './PendingDecisionContext';

export type PendingReply = OperatorDecisionCard;

export function PendingReplyDialog({
  reply,
  open,
  busy,
  onClose,
  onSubmit,
  error = '',
}: {
  reply: PendingReply | null;
  open: boolean;
  busy: boolean;
  onClose: () => void;
  onSubmit: (optionId: string, note: string) => void;
  error?: string;
}) {
  const { t, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const [note, setNote] = useState('');
  const [validationError, setValidationError] = useState('');
  useEffect(() => {
    if (!open) return;
    setNote('');
    setValidationError('');
  }, [open, reply?.id]);
  if (!reply) return null;

  const intake = reply.kind === 'domain_intake';
  // One click on an offered option answers with it; what is typed goes with
  // it as a note. The written answer is the card's own `custom` choice.
  const choices = reply.options.filter((option) => option.id !== 'custom');
  const submit = (optionId: string) => {
    if (busy) return;
    if (optionId === 'custom' && !note.trim()) {
      setValidationError(t('decision.noteRequired'));
      return;
    }
    setValidationError('');
    onSubmit(optionId, note.trim());
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (isImeComposing(event)) return;
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      submit('custom');
    }
  };

  return (
    <Modal open={open} onClose={busy ? () => undefined : onClose} label={t('decision.operator')} width="max-w-2xl">
      <ModalHeader title={intake ? reply.title : t('decision.required')} sub={!intake && reply.title !== reply.task_title ? reply.title : undefined} />
      <div className="space-y-4 px-5 py-4">
        <PendingDecisionContext card={reply} />
        {reply.reason ? (
          <section className="rounded-md border border-gold/30 bg-gold/5 p-3">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-gold">{t('decision.whyBlocked')}</div>
            <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-ink">{reply.reason}</p>
          </section>
        ) : null}

        {reply.evidence.length ? (
          <section>
            <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">{t('decision.evidence')}</div>
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

        {choices.length ? (
          <div className="space-y-2">
            {choices.map((option) => (
            <button
              key={option.id}
              type="button"
              onClick={() => submit(option.id)}
              disabled={busy}
              className="w-full rounded-md border border-line bg-bg/30 p-3 text-left hover:border-blue hover:bg-blue/5 disabled:opacity-60"
            >
              <div className="text-sm font-medium text-ink">{option.label}</div>
              {option.description ? <div className="mt-1 text-xs leading-relaxed text-ink-dim">{option.description}</div> : null}
            </button>
            ))}
          </div>
        ) : null}

        <textarea
          data-autofocus
          value={note}
          onChange={(event) => {
            setNote(event.target.value);
            setValidationError('');
          }}
          onKeyDown={onKeyDown}
          rows={3}
          disabled={busy}
          aria-label={intake ? (zh ? '你的回答或补充说明' : 'Your answer or additional details') : t('decision.notePlaceholder')}
          placeholder={intake ? (zh ? '补充说明（可选）' : 'Additional details (optional)') : t('decision.notePlaceholder')}
          className="w-full resize-y rounded-lg border border-line bg-bg px-3 py-2 text-sm leading-relaxed text-ink outline-none focus:border-blue disabled:opacity-60"
        />
        {validationError || error ? (
          <p role="alert" className="text-xs text-err">{validationError || error}</p>
        ) : null}

        <div className="flex items-center justify-between gap-3">
          <span className="text-xs text-ink-faint">{intake ? (zh ? '确认后继续这次任务。' : 'Confirm to continue this request.') : t('decision.resumeHint')}</span>
          <div className="flex gap-2">
            {/* The answer is delivered synchronously and the Manager may work
                for a while before it returns; the dialog can be put away
                meanwhile and the work continues in the map. */}
            <button type="button" onClick={onClose} className="rounded-md px-3 py-2 text-xs text-ink-dim hover:bg-bg">{busy ? t('decision.closeContinue') : t('decision.later')}</button>
            <button type="button" onClick={() => submit('custom')} disabled={busy} className="rounded-md border border-blue/35 bg-blue/8 px-3 py-2 text-xs font-medium text-blue hover:border-blue-deep hover:bg-blue-deep hover:text-on-accent disabled:opacity-50">
              {busy ? t('decision.applying') : t('decision.sendAnswer')}
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
