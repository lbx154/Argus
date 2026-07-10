import { useEffect, useRef, type ReactNode } from 'react';

/**
 * A centered modal over a plain scrim — the container for the command palette,
 * keybinding help, and the secondary views (doctor/config/identity/journal).
 * Esc closes; the scrim is click-to-dismiss.
 */
export function Modal({
  open,
  onClose,
  children,
  label,
  width = 'max-w-2xl',
  align = 'center',
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  label: string;
  width?: string;
  align?: 'center' | 'top';
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = window.requestAnimationFrame(() => {
      const target = dialogRef.current?.querySelector<HTMLElement>('[data-autofocus]')
        ?? dialogRef.current?.querySelector<HTMLElement>(
          'input:not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
        );
      (target ?? dialogRef.current)?.focus();
    });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeRef.current();
        return;
      }
      if (e.key !== 'Tab' || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
        'input:not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      )).filter((element) => element.getAttribute('aria-hidden') !== 'true');
      if (focusable.length === 0) {
        e.preventDefault();
        dialogRef.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && (document.activeElement === first || !dialogRef.current.contains(document.activeElement))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('keydown', onKey);
      if (previous?.isConnected) previous.focus();
    };
  }, [open]);

  if (!open) return null;
  return (
    <div
      className={`fixed inset-0 z-50 flex ${align === 'top' ? 'items-start pt-24' : 'items-center'} justify-center p-4`}
      onMouseDown={onClose}
    >
      <div className="absolute inset-0 bg-[rgba(0,0,0,0.68)]" />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        className={`relative z-10 w-full ${width} max-h-[80vh] overflow-hidden rounded-lg border border-line bg-panel shadow-glow`}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}

export function ModalHeader({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="border-b border-line px-5 py-3">
      <h2 className="text-sm font-semibold text-ink">{title}</h2>
      {sub && <p className="mt-0.5 text-xs text-ink-faint">{sub}</p>}
    </div>
  );
}
