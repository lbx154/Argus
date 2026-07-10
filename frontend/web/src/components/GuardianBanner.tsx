import type { GuardianAlert } from '../lib/guardian';

/**
 * The proactive guardian banner (监视守护) — a can't-miss strip the web cockpit
 * pins when the daemon raised an alert for the operator (a hard block, a reviewer
 * backend failure, a budget pause, a stall). Argus Panoptes holds the problem in
 * front of you instead of letting it scroll past; it clears itself the moment the
 * mission moves on. Derived from the event stream — no polling, no LLM.
 */
export function GuardianBanner({ alert }: { alert: GuardianAlert | null }) {
  if (!alert) return null;
  const block = alert.tone === 'block';
  const tone = block
    ? 'border-err/50 bg-err/10 text-err'
    : 'border-warn/50 bg-warn/10 text-warn';
  return (
    <div
      className={`mx-3 mt-3 flex items-center gap-2.5 rounded-lg border px-3.5 py-2 text-[13px] ${tone}`}
      role="status"
    >
      <span className="shrink-0 font-mono text-xs font-bold leading-none">{block ? '!' : 'i'}</span>
      <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide">
        {block ? 'action required' : 'notice'}
      </span>
      <span className="min-w-0 flex-1 truncate" title={alert.text}>
        {alert.text}
      </span>
    </div>
  );
}
