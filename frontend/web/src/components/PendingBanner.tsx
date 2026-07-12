import type { BacklogItem } from '../api';

/**
 * When a mission blocks waiting for the operator (a backlog item gets a
 * ``pending_question``), the REPL surfaces it in /status. The web must too —
 * otherwise the daemon silently stalls. Answer by typing in the chat box; the
 * Manager routes the reply back to the blocked mission.
 */
export function PendingBanner({
  questions,
  backlog,
  onAnswer,
}: {
  questions: Array<Record<string, unknown>>;
  backlog: BacklogItem[];
  onAnswer: () => void;
}) {
  const fromBacklog = backlog
    .map((b) => (b as unknown as { pending_question?: string }).pending_question)
    .filter((q): q is string => !!q && q.trim().length > 0);
  const fromStatus = questions
    .map((q) => String(q.question ?? q.text ?? q.pending_question ?? '').trim())
    .filter(Boolean);
  const all = [...new Set([...fromStatus, ...fromBacklog])];
  if (all.length === 0) return null;

  return (
    <div className="mb-2 flex min-h-9 items-center gap-3 rounded-md border border-gold/40 bg-gold/5 px-3 py-2">
      <span className="min-w-0 flex-1 truncate text-xs text-ink-dim" title={all[0]}>{all[0]}</span>
      {all.length > 1 ? <span className="font-mono text-xs text-ink-faint">+{all.length - 1}</span> : null}
      <button onClick={onAnswer} className="shrink-0 text-xs font-medium text-gold hover:text-gold-soft">
        Reply
      </button>
    </div>
  );
}
