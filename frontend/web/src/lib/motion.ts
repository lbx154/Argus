import { useEffect, useRef, type DependencyList, type RefObject } from 'react';

type Gsap = (typeof import('gsap'))['gsap'];
type MotionSetup = (gsap: Gsap, reduceMotion: boolean) => void | (() => void);

export const motionDuration = {
  fast: 0.18,
  normal: 0.28,
} as const;

export const motionDistance = {
  magnetic: 5,
} as const;

export const motionQueries = {
  all: '(min-width: 0px)',
  reduceMotion: '(prefers-reduced-motion: reduce)',
};

/** Load GSAP only when an animated surface mounts; matchMedia owns cleanup. */
export function useGsapMotion(
  scope: RefObject<Element>,
  setup: MotionSetup,
  dependencies: DependencyList = [],
): void {
  const setupRef = useRef(setup);
  setupRef.current = setup;

  useEffect(() => {
    let disposed = false;
    let media: ReturnType<Gsap['matchMedia']> | null = null;
    void import('gsap').then((module) => {
      if (disposed || !scope.current) return;
      const gsap = module.gsap;
      media = gsap.matchMedia();
      media.add(motionQueries, (context) => {
        return setupRef.current(gsap, Boolean(context.conditions?.reduceMotion));
      }, scope.current);
    });
    return () => {
      disposed = true;
      media?.revert();
    };
    // Dependencies are explicit at each call site, like useEffect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies);
}

/** Fine-pointer magnetic motion for bounded controls; inert on touch and reduced motion. */
export function useMagneticMotion(
  scope: RefObject<HTMLElement>,
  enabled = true,
): void {
  useGsapMotion(scope, (gsap, reduceMotion) => {
    const element = scope.current;
    if (
      !enabled
      || reduceMotion
      || !element
      || !window.matchMedia('(hover: hover) and (pointer: fine)').matches
    ) return;
    const xTo = gsap.quickTo(element, 'x', { duration: motionDuration.fast, ease: 'power2.out' });
    const yTo = gsap.quickTo(element, 'y', { duration: motionDuration.fast, ease: 'power2.out' });
    const move = (event: PointerEvent) => {
      const rect = element.getBoundingClientRect();
      const x = ((event.clientX - rect.left) / rect.width - 0.5) * motionDistance.magnetic * 2;
      const y = ((event.clientY - rect.top) / rect.height - 0.5) * motionDistance.magnetic * 2;
      xTo(x);
      yTo(y);
    };
    const leave = () => {
      xTo(0);
      yTo(0);
    };
    element.addEventListener('pointermove', move, { passive: true });
    element.addEventListener('pointerleave', leave);
    return () => {
      element.removeEventListener('pointermove', move);
      element.removeEventListener('pointerleave', leave);
    };
  }, [enabled]);
}
