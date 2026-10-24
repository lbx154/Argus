import { useEffect, useRef, useState, type DragEvent, type KeyboardEvent, type ReactNode } from 'react';
import type { MessageRouteOverride } from '../api';
import { isImeComposing } from '../lib/ime';
import { spinnerFrame } from '../lib/soul';
import { slashCompletions, applyCompletion } from '../../../core/src/commands';
import { isPromptRewriteShortcut } from '../../../core/src/shortcuts';
import { clampSlashCompletionSelection, slashCompletionOptionId, SlashCompletionMenu, SLASH_COMPLETION_LISTBOX_ID, SLASH_COMPLETION_VISIBLE_ROWS } from './SlashCompletionMenu';
import { useI18n } from '../i18n';
import { addComposerFiles, dataTransferHasFiles, extractFilesFromDataTransfer, MESSAGE_ATTACHMENT_MAX_BYTES, MESSAGE_ATTACHMENT_MAX_COUNT, MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES } from '../lib/attachments';
import { formatBytes } from '../lib/format';
import { ComposerAttachmentChip } from './ComposerAttachmentChip';
import { useDraftUploadView } from '../lib/draftUploads';
import { ComposerSurface } from './ComposerSurface';
import { RouteSegment } from './RouteSegment';

interface RewriteShortcutEvent {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  preventDefault: () => void;
}
interface RewriteShortcutState {
  value: string;
  disabled: boolean;
  pending: boolean;
  rewriting: boolean;
  onRewrite?: (draft: string) => void;
}
export function handlePromptRewriteShortcut(event: RewriteShortcutEvent, state: RewriteShortcutState): boolean {
  if (!state.onRewrite || !isPromptRewriteShortcut(event.key, event.ctrlKey, event.metaKey)) return false;
  event.preventDefault();
  const draft = state.value.trim();
  if (draft && !state.disabled && !state.pending && !state.rewriting) state.onRewrite(draft);
  return true;
}

/** Main-conversation actions around the same input surface used by the map. */
export function ChatBox({
  value, onChange, onSend, onCancel, disabled, pending, focusSignal,
  attachments, onAttachmentsChange, onRewrite, rewriting = false,
  slashSelection, onSlashSelectionChange, routeOverride = 'auto', onRouteOverrideChange, footer, pendingNote,
  settings,
}: {
  value: string;
  onChange: (text: string) => void;
  onSend: (text: string, attachments?: File[], observe?: undefined, options?: { whileRunning?: boolean }) => boolean | Promise<boolean>;
  onCancel: () => void;
  disabled: boolean;
  pending: boolean;
  focusSignal?: number;
  embedded?: boolean;
  attachments: File[];
  onAttachmentsChange: (files: File[]) => void;
  onRewrite?: (draft: string) => void;
  rewriting?: boolean;
  slashSelection: number;
  onSlashSelectionChange: (n: number) => void;
  routeOverride?: MessageRouteOverride;
  onRouteOverrideChange?: (value: MessageRouteOverride) => void;
  /** Shown under the input, like the map composer's runtime line. */
  footer?: ReactNode;
  /** Replaces the reply hint while the send waits on something else, such as uploads. */
  pendingNote?: string;
  settings?: ReactNode;
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
  const [queuedNotice, setQueuedNotice] = useState(false);
  useEffect(() => { if (!pending) setQueuedNotice(false); }, [pending]);
  useEffect(() => {
    if (!pending && !rewriting) return;
    // CSS animates the eye; only elapsed durations need a once-per-second tick.
    const id = setInterval(() => setThinkTick((tick) => tick + 1), 1000);
    return () => clearInterval(id);
  }, [pending, rewriting]);
  useEffect(() => { if (focusSignal && !disabled) taRef.current?.focus(); }, [focusSignal, disabled]);
  const visibleCompletions = slashCompletions(value).slice(0, SLASH_COMPLETION_VISIBLE_ROWS);
  const completionOpen = visibleCompletions.length > 0 && !menuDismissed;
  const bounded = completionOpen ? clampSlashCompletionSelection(slashSelection, visibleCompletions.length) : 0;
  const activeCompletion = completionOpen ? visibleCompletions[bounded] : undefined;
  const applySelected = (index: number) => {
    const command = visibleCompletions[index];
    if (!command) return;
    onChange(applyCompletion(command));
    if (command.argument === 'none') setMenuDismissed(true);
    onSlashSelectionChange(0);
    taRef.current?.focus();
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
        ? await onSend(value.trim(), [], undefined, { whileRunning: true })
        : await onSend(value.trim(), attachments);
      if (accepted) {
        onSlashSelectionChange(0);
        setMenuDismissed(false);
        setAttachmentNotice('');
        setQueuedNotice(whileRunning);
      }
    } finally { submitting.current = false; }
  };
  const addFiles = (incoming: File[]) => {
    if (!incoming.length || disabled || pending || submitting.current) return;
    const { accepted, issues } = addComposerFiles(attachments, incoming);
    if (accepted.length) onAttachmentsChange([...attachments, ...accepted]);
    setAttachmentNotice(issues.map((issue) => {
      if (issue.code === 'unsupported') return t('chat.attachUnsupported', { name: issue.fileName });
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
    if (handlePromptRewriteShortcut(event, { value, disabled, pending, rewriting, onRewrite })) return;
    if (completionOpen) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        onSlashSelectionChange(clampSlashCompletionSelection(bounded + (event.key === 'ArrowDown' ? 1 : -1), visibleCompletions.length));
      } else if (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey)) {
        event.preventDefault();
        applySelected(bounded);
      } else if (event.key === 'Escape') { event.preventDefault(); setMenuDismissed(true); }
    } else if (event.key === 'Escape' && pending) { event.preventDefault(); onCancel(); }
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
    {pending ? <div className="px-3 py-2 text-xs text-ink-faint" role="status">{pendingNote || (queuedNotice ? t('chat.queuedWhileRunning') : t('chat.stopWaitingHint'))}</div> : null}
    {completionOpen ? <SlashCompletionMenu query={value} selected={bounded} onSelect={applySelected} /> : null}
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
        {attachmentNotice || (busyUploads ? (zh ? `${busyUploads} 个附件上传中，可以继续输入；现在发送会等它们传完再发出。` : `${busyUploads} file${busyUploads > 1 ? 's' : ''} uploading. Keep typing; sending now waits for them.`) : '') || t('chat.attachHint', { count: MESSAGE_ATTACHMENT_MAX_COUNT, perFile: `${MESSAGE_ATTACHMENT_MAX_BYTES / 1024 ** 2} MiB`, total: `${MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES / 1024 ** 2} MiB` })}
      </div>
    </div> : null}
    <ComposerSurface value={value} onChange={(text) => { onChange(text); onSlashSelectionChange(0); setMenuDismissed(false); }}
      inputRef={taRef} fileInputRef={fileInputRef} onFiles={(event) => { addFiles(Array.from(event.target.files ?? [])); event.target.value = ''; }}
      onSend={() => void submit()} onCancel={onCancel} pending={pending} disabled={disabled}
      sendLabel={busyUploads ? (zh ? '附件传完后发送' : 'Send when uploads finish') : undefined}
      inputProps={{
        onPaste: (event) => { const files = extractFilesFromDataTransfer(event.clipboardData); if (files.length) { event.preventDefault(); addFiles(files); } },
        onKeyDown: onKey, 'aria-label': t('chat.messageArgus'), 'aria-keyshortcuts': 'Control+R Meta+R',
        'aria-controls': completionOpen ? SLASH_COMPLETION_LISTBOX_ID : undefined, 'aria-expanded': completionOpen,
        'aria-activedescendant': activeCompletion ? slashCompletionOptionId(activeCompletion.id) : undefined,
        placeholder: disabled ? t('chat.selectSession') : t('chat.placeholder'),
      }} />
    <div className="composer-meta">
    {(settings || onRouteOverrideChange || footer) && <details className="composer-options">
      <summary>{zh ? '任务设置与模型' : 'Task settings and model'}{routeOverride === 'chat' ? zh ? ' · 仅对话' : ' · Chat only' : routeOverride === 'task' ? zh ? ' · 研究任务' : ' · Research task' : ''}</summary>
      {settings}
      {onRouteOverrideChange ? <RouteSegment value={routeOverride} onChange={onRouteOverrideChange} disabled={disabled || pending} /> : null}
      {footer}
    </details>}
    {onRewrite && value.trim() ? <button type="button" className="composer-rewrite" onClick={() => onRewrite(value.trim())} disabled={disabled || pending || rewriting}
      title={`Ctrl/⌘+R · ${t('chat.rewriteHint')}`} aria-label={t('chat.rewriteLabel')} aria-keyshortcuts="Control+R Meta+R">
      {rewriting ? `${spinnerFrame(thinkTick)} ${t('chat.rewriting')}` : t('chat.rewrite')}
    </button> : null}
    </div>
  </div>;
}
