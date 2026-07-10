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
    <div className="mx-3 mt-3 rounded-md border border-gold/40 bg-gold/5 px-4 py-2.5">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 font-mono text-xs font-bold text-gold">?</span>
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-gold">
            Input required {all.length > 1 ? `· ${all.length}` : ''}
          </div>
          {all.slice(0, 2).map((q, i) => (
            <div key={i} className="mt-1 text-sm leading-snug text-ink">{q}</div>
          ))}
        </div>
        <button
          onClick={onAnswer}
          className="shrink-0 rounded-md border border-gold/50 bg-gold/15 px-3 py-1 text-xs font-medium text-gold transition-colors hover:bg-gold/25"
        >
          Answer
        </button>
      </div>
    </div>
  );
}
