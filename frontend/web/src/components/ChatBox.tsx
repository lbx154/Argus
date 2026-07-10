import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Spinner } from './primitives';
import { THINKING_LINES } from '../lib/soul';

export interface ChatTurn {
  role: 'you' | 'argus' | 'system';
  text: string;
  pending?: boolean;
}

/**
 * The Manager front-door as a single conversational box — the web analogue of
 * the Python REPL. The operator just talks to Argus; the Manager decides whether
 * to reply (chat) or dispatch a mission to the planner/engineer/reviewer team.
 * No task/nudge/note modes to think about.
 */
export function ChatBox({
  turns,
  onSend,
  onCancel,
  disabled,
  pending,
  focusSignal,
}: {
  turns: ChatTurn[];
  onSend: (text: string) => void;
  onCancel: () => void;
  disabled: boolean;
  pending: boolean;
  focusSignal?: number;
}) {
  const [text, setText] = useState('');
  const taRef = useRef<HTMLTextAreaElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  // Rotate two literal status lines so a long model call reads as alive without
  // anthropomorphic filler.
  const [thinkTick, setThinkTick] = useState(0);
  useEffect(() => {
    if (!pending) return;
    setThinkTick((t) => t + 1);
    const id = setInterval(() => setThinkTick((t) => t + 1), 3800);
    return () => clearInterval(id);
  }, [pending]);
  const thinkingLine = THINKING_LINES[thinkTick % THINKING_LINES.length];

  useEffect(() => {
    if (focusSignal && !disabled) taRef.current?.focus();
  }, [focusSignal, disabled]);

  useEffect(() => {
    if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [turns.length, pending]);

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
    <div className="card flex flex-col">
      {(turns.length > 0 || pending) && (
        <div ref={threadRef} className="max-h-56 overflow-y-auto scroll-thin border-b border-line px-3 py-2.5">
          {turns.map((t, i) => <Bubble key={i} turn={t} />)}
          {pending && (
            <div className="flex items-center gap-2 border-l-2 border-line px-2 py-1.5 text-xs text-ink-faint">
              <Spinner /> <span>{thinkingLine}</span>
            </div>
          )}
        </div>
      )}
      <div className="flex items-end gap-2 px-3 py-2.5">
        <span className="pb-2 font-mono text-[10px] font-semibold uppercase tracking-wider text-ink-faint" title="message Argus">Message</span>
        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          rows={1}
          disabled={disabled}
          placeholder={disabled ? 'Select a session…' : 'Ask a question or assign work'}
          className="max-h-40 min-h-[38px] flex-1 resize-none bg-transparent py-2 font-sans text-sm text-ink outline-none placeholder:text-ink-faint"
          style={{ fieldSizing: 'content' } as React.CSSProperties}
        />
        <button
          type="button"
          onClick={pending ? onCancel : submit}
          disabled={disabled || (!pending && !text.trim())}
          title={pending ? 'stop waiting for this reply; server-side work may continue' : undefined}
          className={`h-[38px] shrink-0 rounded border px-4 text-sm font-medium transition-colors disabled:opacity-40 ${
            pending
              ? 'border-line text-warn hover:border-warn/60 hover:bg-warn/10'
              : 'border-blue-deep bg-blue-deep text-ink hover:border-blue hover:bg-blue-deep/80'
          }`}
        >
          {pending ? 'Stop waiting' : 'Send'}
        </button>
      </div>
      <div className="px-3 pb-1.5 text-[10px] text-ink-faint">
        {pending
          ? 'Esc detaches this reply; server-side work may continue'
          : 'Enter to send · Shift+Enter for newline · Ctrl+K for commands'}
      </div>
    </div>
  );
}

function Bubble({ turn }: { turn: ChatTurn }) {
  if (turn.role === 'system') {
    return <div className="border-l-2 border-line px-3 py-1.5 font-mono text-[10px] text-ink-faint">{turn.text}</div>;
  }
  const you = turn.role === 'you';
  return (
    <div className="grid grid-cols-[52px_minmax(0,1fr)] border-b border-line/40 py-2 last:border-b-0">
      <span className={`px-1 font-mono text-[10px] font-semibold uppercase tracking-wide ${you ? 'text-ink-faint' : 'text-blue-sky'}`}>
        {you ? 'you' : 'argus'}
      </span>
      <span className={`whitespace-pre-wrap pr-2 text-sm leading-relaxed ${you ? 'text-ink' : 'text-ink-dim'}`}>{turn.text}</span>
    </div>
  );
}
