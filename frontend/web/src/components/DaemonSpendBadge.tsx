function compactMoney(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '$0.00';
  if (value >= 100) return `$${value.toFixed(0)}`;
  if (value >= 10) return `$${value.toFixed(1)}`;
  if (value >= 1) return `$${value.toFixed(2)}`;
  return `$${value.toFixed(3)}`;
}

export function daemonSpendText({
  settledUsd,
  knownUsd = 0,
  status = 'empty',
}: {
  settledUsd?: number | null;
  knownUsd?: number;
  status?: string;
}): string {
  const value = typeof settledUsd === 'number' && Number.isFinite(settledUsd)
    ? settledUsd
    : knownUsd;
  const incomplete = status === 'partial' || status === 'unpriced';
  return `${compactMoney(Math.max(0, value || 0))}${incomplete ? '+' : ''}`;
}

export function DaemonSpendBadge({
  settledUsd,
  knownUsd,
  status,
  calls = 0,
  premiumRequests = 0,
  live = false,
  compact = false,
}: {
  settledUsd?: number | null;
  knownUsd?: number;
  status?: string;
  calls?: number;
  premiumRequests?: number;
  live?: boolean;
  compact?: boolean;
}) {
  const value = daemonSpendText({ settledUsd, knownUsd, status });
  const detail = [
    'Cumulative settled project spend',
    `${calls} model call${calls === 1 ? '' : 's'}`,
    premiumRequests > 0 ? `${premiumRequests.toFixed(1)} premium requests` : '',
    status && status !== 'empty' ? `pricing: ${status}` : '',
  ].filter(Boolean).join(' · ');
  return (
    <span
      title={detail}
      aria-label={`Project spend ${value}`}
      className={`inline-flex shrink-0 items-center rounded-full border border-gold/25 bg-gold/8 font-mono tabular-nums text-gold ${
        compact ? 'h-6 gap-1 px-2 text-[10px]' : 'h-5 gap-1 px-1.5 text-[9px]'
      }`}
    >
      {live ? <span aria-hidden="true" className="h-1.5 w-1.5 animate-pulse rounded-full bg-gold/80" /> : null}
      <span>{value}</span>
    </span>
  );
}
