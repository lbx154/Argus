import { useRef } from 'react';
import { useI18n } from '../i18n';
import { useGsapMotion } from '../lib/motion';
import { ArgusMark } from './Wordmark';

const STEPS = ['handshake.service', 'handshake.project', 'handshake.ready'];

export function BackendHandshake() {
  const { t } = useI18n();
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
    <div ref={rootRef} role="status" aria-label={t('handshake.connecting')} className="w-full max-w-xl px-6 text-center">
      <div data-handshake-mark className="handshake-mark glass-card mx-auto flex h-16 w-16 items-center justify-center rounded-3xl text-blue shadow-glow sm:h-20 sm:w-20">
        <ArgusMark size={48} className="text-blue" />
      </div>
      <div className="relative mx-auto mt-8 h-10 max-w-sm sm:max-w-md">
        <div className="absolute left-[10%] right-[10%] top-3 h-px bg-line/80" />
        <div data-handshake-line className="handshake-line absolute left-[10%] right-[10%] top-3 h-px" />
        <div className="relative flex justify-between">
          {STEPS.map((step) => (
            <div key={step} className="flex w-20 flex-col items-center gap-2.5">
              <span data-handshake-node className="handshake-node h-6 w-6 rounded-full border ring-4 ring-bg">
                <span className="m-auto mt-[7px] block h-2 w-2 rounded-full bg-blue" />
              </span>
              <span className="text-xs font-medium text-ink-faint">{t(step)}</span>
            </div>
          ))}
        </div>
      </div>
      <p className="mt-9 text-base font-medium text-ink-dim">{t('handshake.title')}</p>
      <p className="mt-1.5 text-sm text-ink-faint">{t('handshake.detail')}</p>
    </div>
  );
}
