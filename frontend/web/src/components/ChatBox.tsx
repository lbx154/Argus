import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { THINKING_LINES, rotateByTick, spinnerFrame } from '../lib/soul';

/**
 * The Manager front-door as a single conversational box. The operator just
 * talks to Argus; the Manager decides whether
 * to reply (chat) or dispatch a mission to the planner/engineer/reviewer team.
 * No task/nudge/note modes to think about.
 */
export function ChatBox({
  onSend,
  onCancel,
  disabled,
  pending,
  focusSignal,
  embedded = false,
  phase = '',
  startedAt = 0,
}: {
  onSend: (text: string) => void;
  onCancel: () => void;
  disabled: boolean;
  pending: boolean;
  focusSignal?: number;
  embedded?: boolean;
  phase?: string;
  startedAt?: number;
}) {
  const [text, setText] = useState('');
  const taRef = useRef<HTMLTextAreaElement>(null);
  const [thinkTick, setThinkTick] = useState(0);
  useEffect(() => {
    if (!pending) return;
    setThinkTick((t) => t + 1);
    const id = setInterval(() => setThinkTick((t) => t + 1), 120);
    return () => clearInterval(id);
  }, [pending]);
  const rawPhase = phase || `${rotateByTick(THINKING_LINES, thinkTick)}…`;
  const thinkingLine = rawPhase.includes('[SESSION HANDOFF')
    ? 'Manager context refreshed · working on your message…'
    : rawPhase.replace(/^Manager\s*·\s*/i, '').slice(0, 100);
  const elapsedS = startedAt ? Math.max(0, Math.floor((Date.now() - startedAt) / 1000)) : 0;

  useEffect(() => {
    if (focusSignal && !disabled) taRef.current?.focus();
  }, [focusSignal, disabled]);

  const submit = () => {
    const t = text.trim();
    if (!t || pending || disabled) return;
    onSend(t);
    setText('');
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Escape' && pending) {
      e.preventDefault();
      onCancel();
    } else if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className={`glass-card glass-panel--raised flex flex-col overflow-hidden rounded-2xl ${
      embedded ? 'shadow-[0_12px_36px_-22px_rgb(var(--spectral-violet)/0.7)] backdrop-blur-md' : ''
    }`}>
      {pending ? (
        <div className="border-b border-line/40 px-3 py-2">
          <div className="flex min-w-0 items-center gap-2 text-xs">
            <span className="font-mono text-manager">{spinnerFrame(thinkTick)}</span>
            <span className="shrink-0 font-semibold text-manager">Your message</span>
            <span className="min-w-0 flex-1 truncate text-blue" title={thinkingLine}>{thinkingLine}</span>
            <span className="shrink-0 font-mono tabular-nums text-ink-faint">{elapsedS}s</span>
          </div>
          <div className="mt-1 text-xs text-ink-faint">Esc stop waiting</div>
        </div>
      ) : null}
      <div className="flex items-end gap-2 px-3 py-2">
        <span className="pb-2 font-mono text-lg text-blue" title="message Argus">›</span>
        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          rows={1}
          disabled={disabled}
          placeholder={disabled ? 'Select a session…' : 'Ask a question or assign work'}
          className="max-h-48 min-h-[38px] min-w-0 flex-1 resize-none bg-transparent py-2 font-sans text-[15px] text-ink outline-none placeholder:text-ink-faint"
          style={{ fieldSizing: 'content' } as React.CSSProperties}
        />
        <button
          type="button"
          onClick={pending ? onCancel : submit}
          disabled={disabled || (!pending && !text.trim())}
          title={pending ? 'stop waiting for this reply; server-side work may continue' : undefined}
          aria-label={pending ? 'stop waiting' : 'send message'}
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
