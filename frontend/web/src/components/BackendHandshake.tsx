import { useRef } from 'react';
import { useGsapMotion } from '../lib/motion';
import { ArgusMark } from './Wordmark';

const STEPS = ['API', 'Protocol', 'Workspace'];

export function BackendHandshake() {
  const rootRef = useRef<HTMLDivElement>(null);
  useGsapMotion(rootRef, (gsap, reduceMotion) => {
    if (reduceMotion) {
      gsap.set('[data-handshake-line], [data-handshake-node]', {
        opacity: 1,
        scale: 1,
        clearProps: 'transform',
      });
      return;
    }
    gsap.to('[data-handshake-mark]', {
      scale: 1.055,
      duration: 0.75,
      ease: 'sine.inOut',
      repeat: -1,
      yoyo: true,
      transformOrigin: '50% 50%',
    });
    gsap.timeline({ repeat: -1, repeatDelay: 0.25 })
      .fromTo(
        '[data-handshake-line]',
        { scaleX: 0, opacity: 0.25, transformOrigin: '0% 50%' },
        { scaleX: 1, opacity: 0.8, duration: 0.9, ease: 'power2.inOut' },
      )
      .fromTo(
        '[data-handshake-node]',
        { autoAlpha: 0.25, scale: 0.72 },
        {
          autoAlpha: 1,
          scale: 1,
          duration: 0.28,
          stagger: 0.16,
          ease: 'back.out(1.8)',
        },
        0.12,
      )
      .to(
        '[data-handshake-node]',
        { autoAlpha: 0.35, duration: 0.3, stagger: 0.08 },
        '+=0.35',
      );
  });

  return (
    <div ref={rootRef} role="status" aria-label="Connecting to Argus backend" className="w-full max-w-md px-6 text-center">
      <div data-handshake-mark className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl border border-blue/30 bg-blue/10 text-blue shadow-glow">
        <ArgusMark size={34} className="text-blue" />
      </div>
      <div className="relative mx-auto mt-6 h-7 max-w-xs">
        <div className="absolute left-[12%] right-[12%] top-2.5 h-px bg-line/80" />
        <div data-handshake-line className="absolute left-[12%] right-[12%] top-2.5 h-px bg-blue" />
        <div className="relative flex justify-between">
          {STEPS.map((step) => (
            <div key={step} className="flex w-16 flex-col items-center gap-2">
              <span data-handshake-node className="h-5 w-5 rounded-full border border-blue/50 bg-panel ring-4 ring-bg">
                <span className="m-auto mt-[6px] block h-1.5 w-1.5 rounded-full bg-blue" />
              </span>
              <span className="text-[10px] font-medium text-ink-faint">{step}</span>
            </div>
          ))}
        </div>
      </div>
      <p className="mt-7 text-sm font-medium text-ink-dim">Connecting to Argus</p>
      <p className="mt-1 text-xs text-ink-faint">Negotiating protocol and restoring your workspace…</p>
    </div>
  );
}
