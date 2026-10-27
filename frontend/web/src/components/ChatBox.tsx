import { useEffect, useLayoutEffect, useRef, useState, type DragEvent, type KeyboardEvent, type ReactNode } from 'react';
import type { MessageRouteOverride } from '../api';
import { isImeComposing } from '../lib/ime';
import { spinnerFrame } from '../lib/soul';
import { completionMenu, completionToken, ghostFor, GHOST_PAUSE_MS, wantsPrediction, type PromptSources } from '../lib/promptCompletion';
import { referenceText, splitDraft } from '../map/presentation';
import { isPromptRewriteShortcut } from '../../../core/src/shortcuts';
import { clampSlashCompletionSelection, slashCompletionOptionId, SlashCompletionMenu, SLASH_COMPLETION_LISTBOX_ID } from './SlashCompletionMenu';
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

/** The focused textarea's caret as of the visible text it was read from; blur or any
 *  other text (a restored draft, a rewrite, another tab) leaves no caret, so nothing pops
 *  up uninvited. An `@`/`#` menu opens only where typing, not a caret move, left it. */
interface Caret { text: string; at: number; fits: boolean; typed: boolean }

/** A long project title is clipped so the placeholder still reads as one line. */
function clipProjectName(name: string): string {
  const text = name.replace(/\s+/g, ' ').trim();
  return text.length > 24 ? `${text.slice(0, 23)}…` : text;
}

export function ChatBox({
  value, onChange, onSend, onCancel, disabled, pending, focusSignal,
  attachments, onAttachmentsChange, onRewrite, rewriting = false,
  slashSelection, onSlashSelectionChange, routeOverride = 'auto', onRouteOverrideChange, footer, pendingNote, projectName,
  settings, completion,
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
  /** The project a message goes to, named in the placeholder so it is never in doubt. */
  projectName?: string;
  /** Replaces the reply hint while the send waits on something else, such as uploads. */
  pendingNote?: string;
  settings?: ReactNode;
  /** What `@`, `#` and the gray text complete from; only the project conversation has it. */
  completion?: PromptSources;
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
  const [cursor, setCursor] = useState<Caret | null>(null);
  const [navigated, setNavigated] = useState(false);
  const [composing, setComposing] = useState(false);
  const [dismissed, setDismissed] = useState('');
  const caretAfter = useRef<number | null>(null);
  const [suggestion, setSuggestion] = useState<{ base: string; tail: string } | null>(null);
  const ghostTimer = useRef<ReturnType<typeof setTimeout>>();
  const ghostRequest = useRef<AbortController>();
  const cancelGhost = () => { clearTimeout(ghostTimer.current); ghostRequest.current?.abort(); };
  // A reply starting, a rewrite or the project switching (a remount) drops the pending guess.
  useEffect(() => cancelGhost, [pending, rewriting, disabled]);
  useEffect(() => { if (!pending) setQueuedNotice(false); }, [pending]);
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
  const bounded = completionOpen ? clampSlashCompletionSelection(slashSelection, visibleCompletions.length) : 0;
  // An @/# row is only "current" once the arrows picked it; until then Enter sends.
  const activeCompletion = completionOpen && (menu?.kind === 'command' || navigated) ? visibleCompletions[bounded] : undefined;
  const offered = completion && !disabled && !pending && !rewriting && !composing && !completionOpen
    && at === text.length && cursor?.fits ? ghostFor(text, completion.history, suggestion) : '';
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
  /** After a pause at the end of a mid-sentence draft that nothing else completes, ask the model. */
  const requestGhost = (caret: Caret) => {
    if (!completion || disabled || pending || rewriting || caret.at !== caret.text.length || !caret.fits || !wantsPrediction(caret.text)
      || completionToken(caret.text, caret.at) || ghostFor(caret.text, completion.history, suggestion)) return;
    // The global setTimeout: tests stub a window without one.
    ghostTimer.current = setTimeout(() => {
      const request = ghostRequest.current = new AbortController();
      completion.continuePrompt(caret.text, request.signal)
        .then((tail) => { if (!request.signal.aborted && tail) setSuggestion({ base: caret.text, tail }); }, () => undefined);
    }, GHOST_PAUSE_MS);
  };
  const typed = (next: string) => {
    onChange(next);
    onSlashSelectionChange(0);
    setMenuDismissed(false);
    setNavigated(false);
    const nextText = splitDraft(next).text;
    // A removed reference chip leaves the words, the caret and the guess pending for them as they were.
    if (nextText === text) return;
    cancelGhost();
    const caret = readCursor(nextText, true);
    if (!composing) requestGhost(caret);
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
    onSlashSelectionChange(0);
    taRef.current?.focus();
  };
  const acceptGhost = () => {
    onChange(refs.map(referenceText).join('') + whole);
    caretAfter.current = whole.length;
  };
  const submit = async () => {
    if (!value.trim() || disabled || rewriting || submitting.current) return;
    cancelGhost();
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
        setNavigated(false);
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
    // Shift+Tab and modified Tabs always keep moving focus.
    const plainTab = event.key === 'Tab' && !event.shiftKey && !event.altKey && !event.ctrlKey && !event.metaKey;
    if (completionOpen) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        setNavigated(true);
        onSlashSelectionChange(clampSlashCompletionSelection(bounded + (event.key === 'ArrowDown' ? 1 : -1), visibleCompletions.length));
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
    else if (event.key === 'Escape' && pending) { event.preventDefault(); onCancel(); }
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
    {completionOpen ? <SlashCompletionMenu items={visibleCompletions} selected={activeCompletion ? bounded : -1} onSelect={applySelected}
      label={t(menu?.kind === '@' ? 'completion.files' : menu?.kind === '#' ? 'completion.tasks' : 'slash.suggestions')} /> : null}
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
    <ComposerSurface value={value} ghost={ghost} onAcceptGhost={acceptGhost}
      onChange={typed}
      inputRef={taRef} fileInputRef={fileInputRef} onFiles={(event) => { addFiles(Array.from(event.target.files ?? [])); event.target.value = ''; }}
      onSend={() => void submit()} onCancel={onCancel} pending={pending} disabled={disabled}
      sendLabel={busyUploads ? (zh ? '附件传完后发送' : 'Send when uploads finish') : undefined}
      inputProps={{
        onPaste: (event) => { const files = extractFilesFromDataTransfer(event.clipboardData); if (files.length) { event.preventDefault(); addFiles(files); } },
        onKeyDown: onKey, 'aria-label': t('chat.messageArgus'), 'aria-keyshortcuts': 'Control+R Meta+R',
        onSelect: (event) => readCursor(event.currentTarget.value, false),
        onBlur: () => setCursor(null),
        onCompositionStart: () => { setComposing(true); cancelGhost(); },
        onCompositionEnd: (event) => { setComposing(false); requestGhost(readCursor(event.currentTarget.value, true)); },
        'aria-autocomplete': completion ? 'both' : 'list',
        'aria-controls': completionOpen ? SLASH_COMPLETION_LISTBOX_ID : undefined, 'aria-expanded': completionOpen,
        'aria-activedescendant': activeCompletion ? slashCompletionOptionId(activeCompletion.id) : undefined,
        placeholder: disabled ? t('chat.selectSession') : projectName?.trim() ? t('chat.placeholderProject', { project: clipProjectName(projectName) }) : t('chat.placeholder'),
      }} />
    {completion ? <span className="sr-only" role="status">{whole && t('completion.ghostHint', { text: whole })}</span> : null}
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
