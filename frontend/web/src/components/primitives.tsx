import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { useI18n } from '../i18n';

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
  ...buttonProps
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  children: ReactNode;
  variant?: 'ghost' | 'primary' | 'danger';
}) {
  const styles: Record<string, string> = {
    ghost: 'brand-button-ghost',
    primary: 'brand-button-primary',
    danger: 'brand-button-danger',
  };
  return (
    <button
      {...buttonProps}
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`brand-button ${styles[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

/** A section header used across the right-rail panels. */
export function PanelHeader({ title, right }: { title: string; right?: ReactNode }) {
  return (
    <div className="panel-header flex min-h-11 items-center justify-between border-b px-4">
      <span className="text-sm font-medium text-ink-dim">{title}</span>
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

/**
 * Raw text (a log tail, a JSON dump, command output) folded away behind one
 * line, so the page shows the sentence that matters and the block waits for
 * whoever wants it. Native `<details>` keeps it keyboard-reachable for free.
 */
export function RawDisclosure({
  label,
  children,
  className = '',
}: {
  label?: string;
  children: ReactNode;
  className?: string;
}) {
  const { t } = useI18n();
  return (
    <details className={`raw-disclosure mt-1 text-xs text-ink-faint ${className}`}>
      <summary className="cursor-pointer select-none hover:text-ink">{label ?? t('common.showRaw')}</summary>
      {children}
    </details>
  );
}
