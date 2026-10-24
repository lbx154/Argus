import { type ChangeEventHandler, type ReactNode, type RefObject, type TextareaHTMLAttributes } from 'react';
import { ArrowUp, Paperclip, Square, X } from 'lucide-react';
import { useI18n } from '../i18n';
import { MESSAGE_ATTACHMENT_ACCEPT, MESSAGE_TEXT_MAX_CHARS } from '../lib/attachments';
import { isImeComposing } from '../lib/ime';
import { referenceText, splitDraft } from '../map/presentation';
import { useAutosizeTextarea } from '../useAutosizeTextarea';
import './composer.css';

/** One visual/input surface for the ordinary conversation and research map. */
export function ComposerSurface({
  value, onChange, inputRef, fileInputRef, onFiles, inputProps, onSend, onCancel,
  pending, disabled = false, controls, sendLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  inputRef: RefObject<HTMLTextAreaElement>;
  fileInputRef: RefObject<HTMLInputElement>;
  onFiles: ChangeEventHandler<HTMLInputElement>;
  inputProps: Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'value' | 'onChange' | 'ref'>;
  onSend: () => void;
  onCancel: () => void;
  pending: boolean;
  disabled?: boolean;
  controls?: ReactNode;
  /** Overrides the send button's name, e.g. when it will wait for uploads. */
  sendLabel?: string;
}) {
  const { t, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const { refs, text } = splitDraft(value);
  const tooLong = value.length > MESSAGE_TEXT_MAX_CHARS;
  useAutosizeTextarea(inputRef, text, 28, 132);
  return (
    <div className="composer-surface" data-pending={pending}>
      {refs.length > 0 && <div className="map-reference-chips">
        {refs.map((reference, index) => <span key={`${reference.task_id}:${reference.step_id}:${index}`}>
          <span>{zh ? '引用' : 'Reference'} · {reference.step_title || reference.task_title}</span>
          <button type="button" aria-label={zh ? '移除引用' : 'Remove reference'} onClick={() => onChange(refs.filter((_, i) => i !== index).map(referenceText).join('') + text)}><X size={12} /></button>
        </span>)}
      </div>}
      <form className="map-composer" onSubmit={(event) => { event.preventDefault(); if (!pending && !disabled && !tooLong) onSend(); }}>
        <input ref={fileInputRef} type="file" multiple accept={MESSAGE_ATTACHMENT_ACCEPT} hidden disabled={disabled || pending} onChange={onFiles} />
        <button type="button" className="map-composer-brand map-attach" aria-label={t('chat.attach')} title={t('chat.attach')} disabled={disabled || pending} onClick={() => fileInputRef.current?.click()}>
          <span aria-hidden="true"><Paperclip size={22} /></span>
        </button>
        <textarea {...inputProps} ref={inputRef} rows={1} value={text} disabled={disabled}
          onChange={(event) => onChange(refs.map(referenceText).join('') + event.target.value)}
          onKeyDown={(event) => {
            if (tooLong && event.key === 'Enter' && !event.shiftKey && !isImeComposing(event)) { event.preventDefault(); return; }
            inputProps.onKeyDown?.(event);
            if (event.defaultPrevented || isImeComposing(event)) return;
            if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); if (!pending && !disabled) onSend(); }
          }} />
        <button type={pending ? 'button' : 'submit'} onClick={pending ? onCancel : undefined}
          disabled={disabled || (!pending && (!text.trim() || tooLong))}
          aria-label={pending ? (zh ? '停止本次回复' : 'Stop this reply') : (sendLabel || (zh ? '发送消息' : 'Send message'))}
          title={!pending ? sendLabel : (zh ? '只停止 Manager 这一次回复；后台团队继续工作' : 'Stops only this Manager reply; the background team keeps working')}
          className={`map-send ${pending ? 'is-pending' : ''}`}>
          {pending ? <Square size={15} /> : <ArrowUp size={20} />}
        </button>
      </form>
      {tooLong && <p role="alert" className="text-xs text-err">{t('chat.messageTooLong')}</p>}
      {controls ? <div className="composer-controls">{controls}</div> : null}
    </div>
  );
}
