import { isImeComposing } from '../lib/ime';
import type { MessageRouteOverride } from '../api';
import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
} from 'react';
import { spinnerFrame } from '../lib/soul';
import { slashCompletions, applyCompletion } from '../../../core/src/commands';
import { isPromptRewriteShortcut } from '../../../core/src/shortcuts';
import {
  formatStepSeconds,
  stepElapsedS,
  visibleTrail,
  type PhaseStep,
} from '../../../core/src/phaseTrail';
import {
  clampSlashCompletionSelection,
  slashCompletionOptionId,
  SlashCompletionMenu,
  SLASH_COMPLETION_LISTBOX_ID,
  SLASH_COMPLETION_VISIBLE_ROWS,
} from './SlashCompletionMenu';
import { useI18n } from '../i18n';
import {
  addComposerFiles,
  dataTransferHasFiles,
  extractFilesFromDataTransfer,
  MESSAGE_ATTACHMENT_ACCEPT,
  MESSAGE_ATTACHMENT_MAX_BYTES,
  MESSAGE_ATTACHMENT_MAX_COUNT,
  MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES,
} from '../lib/attachments';
import { formatBytes } from '../lib/format';
import { ComposerAttachmentChip } from './ComposerAttachmentChip';

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

export function handlePromptRewriteShortcut(
  event: RewriteShortcutEvent,
  state: RewriteShortcutState,
): boolean {
  if (!state.onRewrite || !isPromptRewriteShortcut(event.key, event.ctrlKey, event.metaKey)) {
    return false;
  }
  event.preventDefault();
  const draft = state.value.trim();
  if (draft && !state.disabled && !state.pending && !state.rewriting) {
    state.onRewrite(draft);
  }
  return true;
}

/**
 * One composer with an explicit category selector. Task is operator authority
 * and skips only the category classifier; Manager and Planner ownership remain
 * unchanged. Auto keeps the remote Manager front-door behavior, while Chat
 * guarantees that a question is not enqueued as work.
 *
 * The composer is controlled: draft state lives in the parent (App.tsx) so that
 * slash completions can be applied atomically without racing internal state.
 * `onSend` returns `boolean | Promise<boolean>` — false leaves the draft intact
 * (e.g. a missing-argument error), true clears it.
 */
export function ChatBox({
  value,
  onChange,
  onSend,
  onCancel,
  disabled,
  pending,
  focusSignal,
  embedded = false,
  steps = [],
  onRewrite,
  rewriting = false,
  slashSelection,
  onSlashSelectionChange,
  routeOverride = 'auto',
  onRouteOverrideChange,
}: {
  value: string;
  onChange: (text: string) => void;
  onSend: (text: string, attachments?: File[]) => boolean | Promise<boolean>;
  onCancel: () => void;
  disabled: boolean;
  pending: boolean;
  focusSignal?: number;
  embedded?: boolean;
  steps?: PhaseStep[];
  /** Ask the Manager to rewrite the current draft; result replaces the draft. */
  onRewrite?: (draft: string) => void;
  rewriting?: boolean;
  slashSelection: number;
  onSlashSelectionChange: (n: number) => void;
  routeOverride?: MessageRouteOverride;
  onRouteOverrideChange?: (value: MessageRouteOverride) => void;
}) {
  const { t } = useI18n();
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [thinkTick, setThinkTick] = useState(0);
  // Track whether the user explicitly dismissed the menu for the current value.
  const [menuDismissed, setMenuDismissed] = useState(false);
  const [attachments, setAttachments] = useState<Array<{ id: string; file: File }>>([]);
  const [attachmentNotice, setAttachmentNotice] = useState('');
  const [dragDepth, setDragDepth] = useState(0);

  useEffect(() => {
    if (!pending && !rewriting) return;
    setThinkTick((t) => t + 1);
    // Five frames per second keeps the textual spinner lively without forcing
    // the entire composer to reconcile at animation-frame cadence in WebView2.
    const id = setInterval(() => setThinkTick((t) => t + 1), 200);
    return () => clearInterval(id);
  }, [pending, rewriting]);
  const trailRows = visibleTrail(steps);
  const trailNow = Date.now() / 1000;

  useEffect(() => {
    if (focusSignal && !disabled) taRef.current?.focus();
  }, [focusSignal, disabled]);

  const completions = slashCompletions(value);
  const visibleCompletions = completions.slice(0, SLASH_COMPLETION_VISIBLE_ROWS);
  const completionOpen = visibleCompletions.length > 0 && !menuDismissed;
  const bounded = completionOpen ? clampSlashCompletionSelection(slashSelection, visibleCompletions.length) : 0;
  const activeCompletion = completionOpen ? visibleCompletions[bounded] : undefined;

  const applySelected = (index: number) => {
    const command = visibleCompletions[index];
    if (!command) return;
    const completed = applyCompletion(command);
    onChange(completed);
    // Dismiss for commands without arguments — value now ends with no trailing
    // space so applyCompletion already returned the full token; closing the menu
    // lets the next Enter submit rather than re-complete.
    if (command.argument === 'none') setMenuDismissed(true);
    onSlashSelectionChange(0);
    taRef.current?.focus();
  };

  const submit = async () => {
    const t = value.trim();
    if (!t || pending || disabled) return;
    const accepted = await onSend(t, attachments.map((entry) => entry.file));
    if (accepted) {
      onChange('');
      onSlashSelectionChange(0);
      setMenuDismissed(false);
      setAttachments([]);
      setAttachmentNotice('');
    }
  };

  const attachmentError = (code: string, payload: Record<string, string | number>) => {
    if (code === 'unsupported') return t('chat.attachUnsupported', payload);
    if (code === 'too-large') return t('chat.attachTooLarge', payload);
    if (code === 'too-many') return t('chat.attachTooMany', payload);
    return t('chat.attachTotalTooLarge', payload);
  };

  const addFiles = (incoming: File[]) => {
    if (!incoming.length || disabled || pending) return;
    const { accepted, issues } = addComposerFiles(
      attachments.map((entry) => entry.file),
      incoming,
    );
    if (accepted.length) {
      setAttachments((current) => [
        ...current,
        ...accepted.map((file) => ({
          id: globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`,
          file,
        })),
      ]);
    }
    setAttachmentNotice(
      issues.map((issue) => {
        if (issue.code === 'unsupported') {
          return attachmentError(issue.code, { name: issue.fileName });
        }
        if (issue.code === 'too-large') {
          return attachmentError(issue.code, {
            name: issue.fileName,
            size: formatBytes(issue.limitBytes),
          });
        }
        if (issue.code === 'too-many') {
          return attachmentError(issue.code, { count: issue.limitCount });
        }
        return attachmentError(issue.code, { size: formatBytes(issue.limitBytes) });
      }).join(' '),
    );
  };

  const onFilePick = (event: ChangeEvent<HTMLInputElement>) => {
    addFiles(Array.from(event.target.files ?? []));
    event.target.value = '';
  };

  const onPasteFiles = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = extractFilesFromDataTransfer(event.clipboardData);
    if (!files.length) return;
    event.preventDefault();
    addFiles(files);
  };

  const onDragEnterFiles = (event: DragEvent<HTMLDivElement>) => {
    if (!dataTransferHasFiles(event.dataTransfer)) return;
    event.preventDefault();
    setDragDepth((depth) => depth + 1);
  };

  const onDragOverFiles = (event: DragEvent<HTMLDivElement>) => {
    if (!dataTransferHasFiles(event.dataTransfer)) return;
    event.preventDefault();
  };

  const onDragLeaveFiles = (event: DragEvent<HTMLDivElement>) => {
    if (!dataTransferHasFiles(event.dataTransfer)) return;
    event.preventDefault();
    setDragDepth((depth) => Math.max(0, depth - 1));
  };

  const onDropFiles = (event: DragEvent<HTMLDivElement>) => {
    if (!dataTransferHasFiles(event.dataTransfer)) return;
    event.preventDefault();
    setDragDepth(0);
    addFiles(extractFilesFromDataTransfer(event.dataTransfer));
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // While an IME is composing, Enter confirms a candidate and the arrows page
    // through them. Acting here would send the message mid-word.
    if (isImeComposing(e)) return;
    if (handlePromptRewriteShortcut(e, {
      value,
      disabled,
      pending,
      rewriting,
      onRewrite,
    })) return;
    if (completionOpen) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        onSlashSelectionChange(clampSlashCompletionSelection(bounded + 1, visibleCompletions.length));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        onSlashSelectionChange(clampSlashCompletionSelection(bounded - 1, visibleCompletions.length));
      } else if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
        e.preventDefault();
        applySelected(bounded);
      } else if (e.key === 'Escape') {
        e.preventDefault();
        setMenuDismissed(true);
      }
    } else {
      if (e.key === 'Escape' && pending) {
        e.preventDefault();
        onCancel();
      } else if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        void submit();
      }
    }
  };

  return (
    <div
      onDragEnter={onDragEnterFiles}
      onDragOver={onDragOverFiles}
      onDragLeave={onDragLeaveFiles}
      onDrop={onDropFiles}
      className={`glass-card glass-panel--raised flex flex-col overflow-hidden rounded-2xl ${
        embedded ? 'shadow-[0_12px_36px_-22px_rgb(var(--spectral-violet)/0.7)] backdrop-blur-md' : ''
      } ${dragDepth > 0 ? 'ring-2 ring-manager/60 ring-offset-0' : ''}`}
    >
      {pending ? (
        <div className="border-b border-line/40 px-3 py-2">
          {trailRows.length ? (
            <ol className="mt-1.5 space-y-0.5">
              {trailRows.map((step, index) => {
                const active = index === trailRows.length - 1 && !step.endedTs;
                const seconds = formatStepSeconds(stepElapsedS(step, trailNow));
                return (
                  <li key={step.id} className="flex min-w-0 items-baseline gap-2 text-xs">
                    <span className={`shrink-0 font-mono ${active ? 'text-manager' : 'text-ok'}`}>
                      {active ? spinnerFrame(thinkTick) : '✓'}
                    </span>
                    <span
                      className={`min-w-0 flex-1 truncate font-mono ${active ? 'text-ink' : 'text-ink-faint'}`}
                      title={step.detail || step.label}
                    >
                      {step.label}
                    </span>
                    {seconds ? (
                      <span className="shrink-0 font-mono tabular-nums text-ink-faint">{seconds}</span>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          ) : null}
          <div className="mt-1 text-xs text-ink-faint">{t('chat.stopWaitingHint')}</div>
        </div>
      ) : null}
      {completionOpen ? (
        <SlashCompletionMenu
          query={value}
          selected={bounded}
          onSelect={applySelected}
        />
      ) : null}
      {(attachments.length || attachmentNotice || dragDepth > 0) ? (
        <div className="border-b border-line/30 px-3 py-2">
          {dragDepth > 0 ? (
            <div className="mb-2 text-xs font-medium text-manager">{t('chat.attachDrop')}</div>
          ) : null}
          {attachments.length ? (
            <div className="flex flex-wrap gap-2">
              {attachments.map((entry) => (
                <ComposerAttachmentChip
                  key={entry.id}
                  file={entry.file}
                  removeLabel={t('chat.attachRemove', { name: entry.file.name })}
                  onRemove={() => {
                    setAttachments((current) => current.filter((item) => item.id !== entry.id));
                    setAttachmentNotice('');
                  }}
                />
              ))}
            </div>
          ) : null}
          <div className={`mt-2 text-xs ${attachmentNotice ? 'text-err' : 'text-ink-faint'}`}>
            {attachmentNotice || t('chat.attachHint', {
              count: MESSAGE_ATTACHMENT_MAX_COUNT,
              perFile: formatBytes(MESSAGE_ATTACHMENT_MAX_BYTES),
              total: formatBytes(MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES),
            })}
          </div>
        </div>
      ) : null}
      <div className="flex items-end gap-2 px-3 py-2">
        <span className="pb-2 font-mono text-lg text-blue" title={t('chat.messageArgus')}>›</span>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={MESSAGE_ATTACHMENT_ACCEPT}
          onChange={onFilePick}
          className="hidden"
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={disabled || pending}
          title={`${t('chat.attach')} · ${t('chat.attachHint', {
            count: MESSAGE_ATTACHMENT_MAX_COUNT,
            perFile: formatBytes(MESSAGE_ATTACHMENT_MAX_BYTES),
            total: formatBytes(MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES),
          })}`}
          aria-label={t('chat.attach')}
          className="send-control h-9 w-9 shrink-0 rounded-full border-line/70 bg-panel/80 text-base text-ink-faint hover:border-blue/50 hover:bg-blue/10 hover:text-blue disabled:opacity-40"
        >
          📎
        </button>
        <textarea
          ref={taRef}
          value={value}
          onChange={(e) => {
            onChange(e.target.value);
            onSlashSelectionChange(0);
            setMenuDismissed(false);
          }}
          onPaste={onPasteFiles}
          onKeyDown={onKey}
          aria-keyshortcuts="Control+R Meta+R"
          rows={1}
          disabled={disabled}
          aria-controls={completionOpen ? SLASH_COMPLETION_LISTBOX_ID : undefined}
          aria-expanded={completionOpen}
          aria-activedescendant={activeCompletion ? slashCompletionOptionId(activeCompletion.id) : undefined}
          placeholder={disabled ? t('chat.selectSession') : t('chat.placeholder')}
          className="max-h-48 min-h-[38px] min-w-0 flex-1 resize-none bg-transparent py-2 font-sans text-[15px] text-ink outline-none placeholder:text-ink-faint"
          style={{ fieldSizing: 'content' } as React.CSSProperties}
        />
        {onRouteOverrideChange ? (
          <select
            value={routeOverride}
            onChange={(event) => onRouteOverrideChange(
              event.target.value as MessageRouteOverride,
            )}
            disabled={disabled || pending}
            title={t('chat.routeHint')}
            aria-label={t('chat.routeLabel')}
            className="send-control h-9 shrink-0 rounded-full border-line bg-transparent px-3 text-xs font-medium text-ink-faint hover:border-blue/70 hover:text-ink disabled:opacity-40"
          >
            <option value="task">{t('chat.routeTask')}</option>
            <option value="auto">{t('chat.routeAuto')}</option>
            <option value="chat">{t('chat.routeChat')}</option>
          </select>
        ) : null}
        {onRewrite ? (
          <button
            type="button"
            onClick={() => onRewrite(value.trim())}
            disabled={disabled || pending || rewriting || !value.trim()}
            title={`Ctrl/⌘+R · ${t('chat.rewriteHint')}`}
            aria-label={t('chat.rewriteLabel')}
            aria-keyshortcuts="Control+R Meta+R"
            className="send-control h-9 shrink-0 rounded-full border-manager/70 bg-manager/10 px-3 text-xs font-medium text-manager hover:border-manager hover:bg-manager/20 disabled:opacity-40"
          >
            {rewriting ? `${spinnerFrame(thinkTick)} ${t('chat.rewriting')}` : t('chat.rewrite')}
          </button>
        ) : null}
        <button
          type="button"
          onClick={pending ? onCancel : () => void submit()}
          disabled={disabled || (!pending && !value.trim())}
          title={pending ? t('chat.stopWaitingTitle') : undefined}
          aria-label={pending ? t('chat.stopWaiting') : t('chat.send')}
          className={`send-control h-9 w-9 shrink-0 rounded-full text-sm font-medium disabled:opacity-40 ${
            pending
              ? 'border-line text-warn hover:border-warn/60 hover:bg-warn/10'
              : 'border-blue/70 bg-blue/10 text-blue hover:border-blue hover:bg-blue/20'
          }`}
        >
          {pending ? '■' : '↑'}
        </button>
      </div>
    </div>
  );
}
