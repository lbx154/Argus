import { useEffect, useRef, type CSSProperties, type ReactNode } from 'react';
import { useGsapMotion } from '../lib/motion';

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
  viewport = false,
  style,
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  label: string;
  width?: string;
  align?: 'center' | 'top';
  viewport?: boolean;
  style?: CSSProperties;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useGsapMotion(dialogRef, (gsap, reduceMotion) => {
    if (!open || !dialogRef.current || !backdropRef.current) return;
    if (reduceMotion) {
      gsap.set([backdropRef.current, dialogRef.current], { clearProps: 'all' });
      return;
    }
    gsap.timeline({ defaults: { overwrite: 'auto' } })
      .fromTo(
        backdropRef.current,
        { autoAlpha: 0 },
        { autoAlpha: 1, duration: 0.18, ease: 'power1.out' },
        0,
      )
      .fromTo(
        dialogRef.current,
        { autoAlpha: 0, y: align === 'top' ? -10 : 12, scale: 0.985 },
        {
          autoAlpha: 1,
          y: 0,
          scale: 1,
          duration: 0.28,
          ease: 'power3.out',
          clearProps: 'transform,opacity,visibility',
        },
        0.03,
      );
  }, [open, align]);
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
      className={`fixed inset-0 z-50 flex ${align === 'top' ? 'items-start pt-4 sm:pt-16' : 'items-center'} justify-center ${viewport ? 'p-0' : 'p-4'}`}
      onMouseDown={onClose}
    >
      <div ref={backdropRef} className="absolute inset-0 bg-black/25 backdrop-blur-sm" />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        style={style}
        className={`brand-modal glass-panel glass-panel--raised relative z-10 w-full ${width} scroll-thin ${viewport
          ? 'flex h-[100dvh] max-h-[100dvh] flex-col overflow-hidden rounded-none'
          : 'max-h-[calc(100dvh-2rem)] overflow-x-hidden overflow-y-auto rounded-xl sm:max-h-[88dvh]'
        }`}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}

export function ModalHeader({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="px-6 pb-3 pt-5">
      <h2 className="text-base font-semibold tracking-[-0.01em] text-ink">{title}</h2>
      {sub && <p className="mt-1 text-sm text-ink-faint">{sub}</p>}
    </div>
  );
}
