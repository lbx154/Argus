import type { ReactNode } from 'react';

/** A steady status dot. Motion is reserved for real loading operations. */
export function StatusDot({ ok, pulse = false, title }: { ok: boolean; pulse?: boolean; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-block h-1.5 w-1.5 rounded-full transition-shadow duration-150 ${
        ok ? 'bg-ok ring-1 ring-ok/30 ring-offset-1 ring-offset-panel' : 'bg-ink-faint/50'
      }`}
      data-live={ok && pulse ? 'true' : undefined}
    />
  );
}

export function Chip({
  children,
  color,
  className = '',
}: {
  children: ReactNode;
  color?: string;
  className?: string;
}) {
  return (
    <span
      className={`chip text-ink-dim ${className}`}
      style={color ? { color, borderColor: `${color}44` } : undefined}
    >
      {children}
    </span>
  );
}

export function Button({
  children,
  onClick,
  variant = 'ghost',
  disabled,
  title,
  className = '',
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: 'ghost' | 'primary' | 'danger';
  disabled?: boolean;
  title?: string;
  className?: string;
}) {
  const styles: Record<string, string> = {
    ghost: 'border-line bg-transparent text-ink-dim hover:border-ink-faint hover:bg-surface hover:text-ink',
    primary: 'border-blue-deep bg-blue-deep text-white hover:border-blue hover:bg-blue-deep/80',
    danger: 'border-line text-err hover:border-err/60 hover:bg-err/10',
  };
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`rounded border px-2.5 py-1 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${styles[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

/** A section header used across the right-rail panels. */
export function PanelHeader({ title, right }: { title: string; right?: ReactNode }) {
  return (
    <div className="flex min-h-11 items-center justify-between border-b border-line/50 px-4">
      <span className="text-xs font-semibold uppercase tracking-[0.06em] text-ink-faint">{title}</span>
      {right}
    </div>
  );
}

export function Spinner() {
  return (
    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-line border-t-blue" />
  );
}

export function EmptyHint({ children }: { children: ReactNode }) {
  return <div className="px-3 py-6 text-center text-xs text-ink-faint">{children}</div>;
}
