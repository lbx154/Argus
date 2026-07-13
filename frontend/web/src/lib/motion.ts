import { useEffect, useRef, type DependencyList, type RefObject } from 'react';

type Gsap = (typeof import('gsap'))['gsap'];
type MotionSetup = (gsap: Gsap, reduceMotion: boolean) => void;

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
        setupRef.current(gsap, Boolean(context.conditions?.reduceMotion));
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
