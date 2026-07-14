import { useRef, type ReactNode } from 'react';
import { useMagneticMotion } from '../lib/motion';

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
  const buttonRef = useRef<HTMLButtonElement>(null);
  useMagneticMotion(buttonRef, variant !== 'danger' && !disabled);
  const styles: Record<string, string> = {
    ghost: 'brand-button-ghost',
    primary: 'brand-button-primary',
    danger: 'brand-button-danger',
  };
  return (
    <button
      ref={buttonRef}
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
