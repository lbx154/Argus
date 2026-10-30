import { useEffect, useLayoutEffect, useRef, useState, type DragEvent, type KeyboardEvent, type ReactNode } from 'react';
import { Sparkles, Undo2 } from 'lucide-react';
import type { MessageRouteOverride } from '../api';
import { isImeComposing } from '../lib/ime';
import { spinnerFrame } from '../lib/soul';
import { completionMenu, completionToken, ghostFor, type PromptSources } from '../lib/promptCompletion';
import { referenceText, splitDraft } from '../map/presentation';
import { clampPromptCompletionSelection, promptCompletionOptionId, PromptCompletionMenu, PROMPT_COMPLETION_LISTBOX_ID } from './PromptCompletionMenu';
import { useI18n } from '../i18n';
import { addComposerFiles, dataTransferHasFiles, extractFilesFromDataTransfer } from '../lib/attachments';
import { formatBytes } from '../lib/format';
import { ComposerAttachmentChip } from './ComposerAttachmentChip';
import { useDraftUploadView } from '../lib/draftUploads';
import { ComposerSurface } from './ComposerSurface';
import { RouteSegment } from './RouteSegment';
import { roleLabel } from '../lib/enumLabels';

/** The focused textarea's caret as of the visible text it was read from; blur or any
 *  other text (a restored draft, a rewrite, another tab) leaves no caret, so nothing pops
 *  up uninvited. An `@`/`#` menu opens only where typing, not a caret move, left it. */
interface Caret { text: string; at: number; fits: boolean; typed: boolean }

export function ChatBox({
  value, onChange, onSend, disabled, pending, focusSignal,
  attachments, onAttachmentsChange, onRewrite, onUndoRewrite, rewriting = false,
  completionSelection, onCompletionSelectionChange, routeOverride = 'auto', onRouteOverrideChange, footer, pendingNote,
  completion, answeringRole,
}: {
  value: string;
  onChange: (text: string) => void;
  onSend: (text: string, attachments?: File[], options?: { whileRunning?: boolean }) => boolean | Promise<boolean>;
  disabled: boolean;
  pending: boolean;
  focusSignal?: number;
  embedded?: boolean;
  attachments: File[];
  onAttachmentsChange: (files: File[]) => void;
  onRewrite?: (draft: string) => void;
  /** Puts the text before the last rewrite back; given while the draft is still that rewrite. */
  onUndoRewrite?: () => void;
  rewriting?: boolean;
  completionSelection: number;
  onCompletionSelectionChange: (n: number) => void;
  routeOverride?: MessageRouteOverride;
  onRouteOverrideChange?: (value: MessageRouteOverride) => void;
  /** Shown under the input, like the map composer's runtime line. */
  footer?: ReactNode;
  /** Said while the send waits on something else, such as uploads. */
  pendingNote?: string;
  /** What `@`, `#` and the gray text complete from; only the project conversation has it. */
  completion?: PromptSources;
  answeringRole?: string;
}) {
  const { t, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const uploads = useDraftUploadView();
  const ready = uploads?.ready ?? [];
  const busyUploads = uploads?.busy ?? 0;
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const submitting = useRef(false);
  const [thinkTick, setThinkTick] = useState(0);
  const [menuDismissed, setMenuDismissed] = useState(false);
  const [attachmentNotice, setAttachmentNotice] = useState('');
  const [dragDepth, setDragDepth] = useState(0);
  const [focused, setFocused] = useState(false);
  const [cursor, setCursor] = useState<Caret | null>(null);
  const [navigated, setNavigated] = useState(false);
  const [composing, setComposing] = useState(false);
  const [dismissed, setDismissed] = useState('');
  const caretAfter = useRef<number | null>(null);
  useEffect(() => {
    if (!pending && !rewriting) return;
    // CSS animates the eye; only elapsed durations need a once-per-second tick.
    const id = setInterval(() => setThinkTick((tick) => tick + 1), 1000);
    return () => clearInterval(id);
  }, [pending, rewriting]);
  useEffect(() => { if (focusSignal && !disabled) taRef.current?.focus(); }, [focusSignal, disabled]);
  const { refs, text } = splitDraft(value);
  const at = cursor?.text === text ? cursor.at : null;
  const token = completion && at != null && cursor?.typed ? completionToken(text, at) : null;
  useEffect(() => completion?.wantFiles(token?.trigger === '@'), [token?.trigger, completion]);
  const menu = completionMenu(value, token, completion, t, locale);
  const visibleCompletions = menu?.items ?? [];
  const completionOpen = visibleCompletions.length > 0 && !menuDismissed;
  const bounded = completionOpen ? clampPromptCompletionSelection(completionSelection, visibleCompletions.length) : 0;
  // An @/# row is only "current" once the arrows picked it; until then Enter sends.
  const activeCompletion = completionOpen && navigated ? visibleCompletions[bounded] : undefined;
  const offered = completion && !disabled && !pending && !rewriting && !composing && !completionOpen
    && at === text.length && cursor?.fits ? ghostFor(text, completion.history) : '';
  // Esc turns down the suggestion itself, not just the text it was shown after.
  const whole = offered === dismissed ? '' : offered;
  const ghost = whole.slice(text.length);
  /** Where the caret sits in the visible text, whether that text fits without a scrollbar,
   *  and whether typing left it there: a keystroke's own selection event finds it unmoved. */
  const readCursor = (draft: string, typing: boolean): Caret => {
    const ta = taRef.current;
    const start = ta?.selectionStart;
    const where = start == null ? draft.length : start === ta?.selectionEnd ? start : -1;
    const caret = { text: draft, at: where, fits: ta?.style.overflowY !== 'auto',
      typed: typing || (!!cursor?.typed && cursor.text === draft && cursor.at === where) };
    setCursor(caret);
    return caret;
  };
  const typed = (next: string) => {
    onChange(next);
    onCompletionSelectionChange(0);
    setMenuDismissed(false);
    setNavigated(false);
    const nextText = splitDraft(next).text;
    // A removed reference chip leaves the words and the caret as they were.
    if (nextText !== text) readCursor(nextText, true);
  };
  // Put the caret after what a completion inserted, once the new text is in the textarea.
  useLayoutEffect(() => {
    if (caretAfter.current == null) return;
    taRef.current?.setSelectionRange(caretAfter.current, caretAfter.current);
    caretAfter.current = null;
    readCursor(text, true);
  });
  const applySelected = (index: number) => {
    const item = visibleCompletions[index];
    if (!item) return;
    onChange(item.value);
    caretAfter.current = item.caret ?? null;
    if (item.close) setMenuDismissed(true);
    setNavigated(false);
    onCompletionSelectionChange(0);
    taRef.current?.focus();
  };
  const acceptGhost = () => {
    onChange(refs.map(referenceText).join('') + whole);
    caretAfter.current = whole.length;
  };
  const submit = async () => {
    if (!value.trim() || disabled || rewriting || submitting.current) return;
    // Typing during a running reply is a follow-up, not a lost keystroke.
    const whileRunning = pending;
    if (whileRunning && attachments.length) { setAttachmentNotice(t('chat.attachWhileRunning')); return; }
    submitting.current = true;
    try {
      // The shared App controller owns draft/attachment clearance, including
      // when the operator switches to the map during an upload.
      const accepted = whileRunning
        ? await onSend(value.trim(), [], { whileRunning: true })
        : await onSend(value.trim(), attachments);
      if (accepted) {
        onCompletionSelectionChange(0);
        setMenuDismissed(false);
        setNavigated(false);
        setAttachmentNotice('');
      }
    } finally { submitting.current = false; }
  };
  const addFiles = (incoming: File[]) => {
    if (!incoming.length || disabled || pending || submitting.current) return;
    const { accepted, issues } = addComposerFiles([...attachments, ...ready.map(file => ({ name: file.original_name, size: file.size_bytes, type: file.mime }))], incoming, uploads?.limits);
    if (accepted.length) onAttachmentsChange([...attachments, ...accepted]);
    setAttachmentNotice(issues.map((issue) => {
      if (issue.code === 'too-large') return t('chat.attachTooLarge', { name: issue.fileName, size: formatBytes(issue.limitBytes) });
      if (issue.code === 'too-many') return t('chat.attachTooMany', { count: issue.limitCount });
      return t('chat.attachTotalTooLarge', { size: formatBytes(issue.limitBytes) });
    }).join(' '));
  };
  const drag = (event: DragEvent<HTMLDivElement>, delta: number) => {
    if (!dataTransferHasFiles(event.dataTransfer)) return;
    event.preventDefault();
    setDragDepth((depth) => Math.max(0, depth + delta));
  };
  const onKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (isImeComposing(event)) return;
    // Shift+Tab and modified Tabs always keep moving focus.
    const plainTab = event.key === 'Tab' && !event.shiftKey && !event.altKey && !event.ctrlKey && !event.metaKey;
    if (completionOpen) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        setNavigated(true);
        onCompletionSelectionChange(clampPromptCompletionSelection(bounded + (event.key === 'ArrowDown' ? 1 : -1), visibleCompletions.length));
        return;
      }
      if (plainTab || (event.key === 'Enter' && !event.shiftKey && activeCompletion)) {
        event.preventDefault();
        applySelected(bounded);
        return;
      }
      if (event.key === 'Escape') { event.preventDefault(); setMenuDismissed(true); return; }
    }
    if (ghost && plainTab) { event.preventDefault(); acceptGhost(); }
    else if (ghost && event.key === 'Escape') { event.preventDefault(); setDismissed(whole); }
    else if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void submit(); }
  };

  return <div className={`conversation-composer flex flex-col ${dragDepth > 0 ? 'rounded-3xl ring-2 ring-manager/60' : ''}`}
    data-compact={!focused && !value.trim() && !attachments.length && !ready.length && !pending && !rewriting && !attachmentNotice && !dragDepth}
    onFocusCapture={() => setFocused(true)}
    onBlurCapture={(event) => setFocused(event.currentTarget.contains(event.relatedTarget as Node | null))}
    onDragEnter={(event) => drag(event, 1)} onDragOver={(event) => drag(event, 0)} onDragLeave={(event) => drag(event, -1)}
    onDrop={(event) => {
      if (!dataTransferHasFiles(event.dataTransfer)) return;
      event.preventDefault(); setDragDepth(0); addFiles(extractFilesFromDataTransfer(event.dataTransfer));
    }}>
    {pending && pendingNote ? <div className="px-3 py-2 text-xs text-ink-faint" role="status">{pendingNote}</div> : null}
    {completionOpen ? <PromptCompletionMenu items={visibleCompletions} selected={activeCompletion ? bounded : -1} onSelect={applySelected}
      label={t(menu?.kind === '@' ? 'completion.files' : 'completion.tasks')} /> : null}
    {(attachments.length || ready.length || attachmentNotice || dragDepth > 0) ? <div className="px-3 py-2">
      {dragDepth > 0 ? <div className="mb-2 text-xs text-manager">{t('chat.attachDrop')}</div> : null}
      <div className="map-attachment-tray">{ready.map((attachment) => <ComposerAttachmentChip key={attachment.attachment_id}
        uploaded={attachment} removeLabel={t('chat.attachRemove', { name: attachment.original_name })}
        disabled={pending} onRemove={() => uploads?.removeReady(attachment.attachment_id)} />)}
      {attachments.map((file, index) => <ComposerAttachmentChip key={`${file.name}:${file.lastModified}:${index}`}
        file={file} removeLabel={t('chat.attachRemove', { name: file.name })}
        disabled={pending}
        onRemove={() => { onAttachmentsChange(attachments.filter((item) => item !== file)); setAttachmentNotice(''); }} />)}</div>
      <div className={`text-xs ${attachmentNotice ? 'text-err' : 'text-ink-faint'}`}>
        {attachmentNotice || (busyUploads ? (zh ? `${busyUploads} 个附件上传中，可以继续输入；现在发送会等它们传完再发出。` : `${busyUploads} file${busyUploads > 1 ? 's' : ''} uploading. Keep typing; sending now waits for them.`) : '') || (uploads?.limits ? t('chat.attachHint', { count: uploads.limits.max_count, perFile: formatBytes(uploads.limits.max_bytes_per_file), total: formatBytes(uploads.limits.max_total_bytes) }) : '')}
      </div>
    </div> : null}
    <ComposerSurface value={value} ghost={ghost} onAcceptGhost={acceptGhost}
      onChange={typed}
      inputRef={taRef} fileInputRef={fileInputRef} onFiles={(event) => { addFiles(Array.from(event.target.files ?? [])); event.target.value = ''; }}
      onSend={() => void submit()} pending={pending} disabled={disabled || rewriting}
      sendLabel={busyUploads ? (zh ? '附件传完后发送' : 'Send when uploads finish') : undefined}
      inputProps={{
        onPaste: (event) => { const files = extractFilesFromDataTransfer(event.clipboardData); if (files.length) { event.preventDefault(); addFiles(files); } },
        onKeyDown: onKey, 'aria-label': t('chat.messageArgus'),
        onSelect: (event) => readCursor(event.currentTarget.value, false),
        onBlur: () => setCursor(null),
        onCompositionStart: () => setComposing(true),
        onCompositionEnd: (event) => { setComposing(false); readCursor(event.currentTarget.value, true); },
        'aria-autocomplete': completion ? 'both' : 'list',
        'aria-controls': completionOpen ? PROMPT_COMPLETION_LISTBOX_ID : undefined, 'aria-expanded': completionOpen,
        'aria-activedescendant': activeCompletion ? promptCompletionOptionId(activeCompletion.id) : undefined,
        placeholder: answeringRole ? (zh ? `回答${roleLabel(answeringRole, t)}…` : `Reply to the ${roleLabel(answeringRole, t)}…`)
          : disabled ? t('chat.selectSession') : t('chat.placeholder'),
      }} />
    {completion ? <span className="sr-only" role="status">{whole && t('completion.ghostHint', { text: whole })}</span> : null}
    <div className="composer-meta">
    {footer}
    {onRouteOverrideChange && <details className="composer-options">
      <summary>{zh ? '任务设置' : 'Task settings'}{routeOverride === 'chat' ? zh ? ' · 仅对话' : ' · Chat only' : routeOverride === 'task' ? zh ? ' · 研究任务' : ' · Research task' : ''}</summary>
      {onRouteOverrideChange ? <RouteSegment value={routeOverride} onChange={onRouteOverrideChange} disabled={disabled || pending} /> : null}
    </details>}
    {onUndoRewrite ? <button type="button" className="composer-rewrite" onClick={onUndoRewrite} disabled={disabled || pending}>
      <Undo2 size={13} aria-hidden="true" />{t('chat.undoRewrite')}
    </button> : onRewrite && value.trim() ? <button type="button" className="composer-rewrite" onClick={() => onRewrite(value.trim())} disabled={disabled || pending || rewriting}
      title={t('chat.rewriteHint')}>
      {rewriting ? spinnerFrame(thinkTick) : <Sparkles size={13} aria-hidden="true" />}{rewriting ? t('chat.rewriting') : t('chat.rewrite')}
    </button> : null}
    </div>
  </div>;
}
