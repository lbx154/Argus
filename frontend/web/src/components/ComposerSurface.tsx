import { useEffect, type ChangeEventHandler, type ReactNode, type RefObject, type TextareaHTMLAttributes } from 'react';
import { ArrowUp, Square, X } from 'lucide-react';
import { ArgusMark } from './Wordmark';
import { useI18n } from '../i18n';
import { MESSAGE_ATTACHMENT_ACCEPT } from '../lib/attachments';
import { isImeComposing } from '../lib/ime';
import { referenceText, splitDraft } from '../map/presentation';
import './composer.css';

/** One visual/input surface for the ordinary conversation and research map. */
export function ComposerSurface({
  value, onChange, inputRef, fileInputRef, onFiles, inputProps, onSend, onCancel,
  pending, disabled = false, controls,
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
}) {
  const { t, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const { refs, text } = splitDraft(value);
  useEffect(() => {
    const element = inputRef.current;
    if (!element) return;
    const resize = () => {
      if (!element.getClientRects().length) return;
      element.style.height = '0px';
      element.style.height = `${Math.min(132, Math.max(28, element.scrollHeight))}px`;
    };
    resize();
    let width = element.clientWidth;
    const observer = new ResizeObserver(() => {
      if (element.clientWidth === width) return;
      width = element.clientWidth;
      resize();
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [inputRef, text]);
  return (
    <div className="composer-surface" data-pending={pending}>
      {refs.length > 0 && <div className="map-reference-chips">
        {refs.map((reference, index) => <span key={`${reference.task_id}:${reference.step_id}:${index}`}>
          <span>{zh ? '引用' : 'Reference'} · {reference.step_title || reference.task_title}</span>
          <button type="button" aria-label={zh ? '移除引用' : 'Remove reference'} onClick={() => onChange(refs.filter((_, i) => i !== index).map(referenceText).join('') + text)}><X size={12} /></button>
        </span>)}
      </div>}
      <form className="map-composer" onSubmit={(event) => { event.preventDefault(); if (!pending && !disabled) onSend(); }}>
        <input ref={fileInputRef} type="file" multiple accept={MESSAGE_ATTACHMENT_ACCEPT} hidden disabled={disabled || pending} onChange={onFiles} />
        <button type="button" className="map-composer-brand map-attach" aria-label={t('chat.attach')} title={t('chat.attach')} disabled={disabled || pending} onClick={() => fileInputRef.current?.click()}>
          <ArgusMark size={24} />
        </button>
        <textarea {...inputProps} ref={inputRef} rows={1} value={text} disabled={disabled}
          onChange={(event) => onChange(refs.map(referenceText).join('') + event.target.value)}
          onKeyDown={(event) => {
            inputProps.onKeyDown?.(event);
            if (event.defaultPrevented || isImeComposing(event)) return;
            if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); if (!pending && !disabled) onSend(); }
          }} />
        <button type={pending ? 'button' : 'submit'} onClick={pending ? onCancel : undefined}
          disabled={disabled || (!pending && !text.trim())}
          aria-label={pending ? (zh ? '停止等待' : 'Stop waiting') : (zh ? '发送消息' : 'Send message')}
          className={`map-send ${pending ? 'is-pending' : ''}`}>
          {pending ? <Square size={15} /> : <ArrowUp size={20} />}
        </button>
      </form>
      {controls ? <div className="composer-controls">{controls}</div> : null}
    </div>
  );
}
