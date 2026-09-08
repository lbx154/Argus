import { useEffect, useId, useRef, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { ArgusMark } from '../components/Wordmark';
import type { MessageDispatch } from './submission';

export interface MapDispatchFlight {
  id: number;
  text: string;
  origin: { x: number; y: number; width: number; height: number };
  result?: MessageDispatch;
  landed?: boolean;
}
type Point = { x: number; y: number };
const mix = (a: number, b: number, t: number) => a + (b - a) * t;
const smooth = (t: number) => t * t * (3 - 2 * t);
const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));

/** The seed and its short wake use the same curve, including a bow for vertical trips. */
export function dispatchCurve(from: Point, to: Point, width: number) {
  const distance = Math.hypot(to.x - from.x, to.y - from.y);
  const bow = Math.min(140, Math.max(54, distance * .22)) * (to.x >= from.x ? -1 : 1);
  const lift = Math.min(120, distance * .3);
  const p = { x: clamp(from.x + bow, 28, width - 28), y: from.y - lift };
  const q = { x: clamp(to.x + bow * .6, 28, width - 28), y: to.y + lift };
  return (t: number): Point => {
    const u = 1 - t;
    return {
      x: u ** 3 * from.x + 3 * u ** 2 * t * p.x + 3 * u * t ** 2 * q.x + t ** 3 * to.x,
      y: u ** 3 * from.y + 3 * u ** 2 * t * p.y + 3 * u * t ** 2 * q.y + t ** 3 * to.y,
    };
  };
}

export function MapDispatchMotion({ flight, canvas, zh, historical = false, onReveal, onLand, onFinish }: {
  flight: MapDispatchFlight;
  canvas: RefObject<HTMLDivElement>;
  zh: boolean;
  historical?: boolean;
  onReveal: (taskId: string) => void;
  onLand: (id: number) => void;
  onFinish: (id: number) => void;
}) {
  const element = useRef<HTMLDivElement>(null);
  const trail = useRef<SVGPathElement>(null);
  const gradient = useRef<SVGLinearGradientElement>(null);
  const halo = useRef<HTMLDivElement>(null);
  const gradientId = `dispatch-wake-${useId().replace(/:/g, '')}`;
  const callbacks = useRef({ onReveal, onLand, onFinish });
  callbacks.current = { onReveal, onLand, onFinish };
  const isTask = flight.result?.type === 'task' && !historical;

  useEffect(() => {
    if (!flight.result) return;
    let frame = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let haloAnimation: Animation | undefined;
    const finish = () => callbacks.current.onFinish(flight.id);
    if (flight.result.type !== 'task' || historical) {
      timer = setTimeout(finish, 2200);
      return () => clearTimeout(timer);
    }
    const el = element.current;
    if (!el) return;
    const taskId = flight.result.taskId;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const root = canvas.current;
    const interrupt = () => {
      cancelAnimationFrame(frame);
      clearTimeout(timer);
      callbacks.current.onLand(flight.id);
      finish();
    };
    root?.addEventListener('pointerdown', interrupt, { once: true });
    root?.addEventListener('wheel', interrupt, { once: true, passive: true });
    const started = performance.now();
    let lastReveal = 0;
    let revealed = false;
    let stableSince = 0;
    let lastBox: DOMRect | undefined;
    const find = (now: number) => {
      if (now - started > 6000) { finish(); return; }
      const target = canvas.current?.querySelector<HTMLElement>(`.map-macro[data-task-id="${CSS.escape(taskId)}"]`);
      if (!target || target.getBoundingClientRect().width < 8) {
        if (now - lastReveal > 600) { callbacks.current.onReveal(taskId); lastReveal = now; }
        frame = requestAnimationFrame(find);
        return;
      }
      if (reduced) { callbacks.current.onLand(flight.id); timer = setTimeout(finish, 1200); return; }
      if (!revealed) {
        callbacks.current.onReveal(taskId);
        revealed = true;
        lastReveal = now;
      }
      const box = target.getBoundingClientRect();
      if (!lastBox || Math.abs(lastBox.x - box.x) + Math.abs(lastBox.y - box.y) + Math.abs(lastBox.width - box.width) > .7) stableSince = now;
      lastBox = box;
      // Let the camera and island settle once; don't restart the camera during the flight.
      if (now - lastReveal < 360 || now - stableSince < 100) { frame = requestAnimationFrame(find); return; }
      const source = canvas.current?.querySelector('.map-composer')?.getBoundingClientRect();
      const origin = source ? { x: source.left, y: source.top, width: source.width, height: source.height } : flight.origin;
      const from = { x: origin.x + origin.width / 2, y: origin.y + origin.height / 2 };
      const takeoff = { x: from.x, y: from.y - 26 };
      const to = { x: box.left + box.width / 2, y: box.top + box.height / 2 };
      const curve = dispatchCurve(takeoff, to, innerWidth);
      const initialWidth = Math.min(320, origin.width);
      const initialHeight = Math.min(68, origin.height);
      const flightStart = performance.now();
      let landed = false;
      const draw = (now: number) => {
        if (!target.isConnected) { finish(); return; }
        const t = Math.min(1, (now - flightStart) / 1050);
        const compress = smooth(Math.min(1, t / .22));
        const travel = smooth(clamp((t - .22) / .6, 0, 1));
        const arrive = smooth(clamp((t - .82) / .18, 0, 1));
        const center = t < .22 ? { x: from.x, y: mix(from.y, takeoff.y, compress) } : curve(travel);
        const size = mix(52, 22, arrive);
        const w = mix(initialWidth, size, compress), h = mix(initialHeight, size, compress);
        el.style.width = `${w}px`;
        el.style.height = `${h}px`;
        el.style.transform = `translate3d(${center.x - w / 2}px,${center.y - h / 2}px,0)`;
        el.style.opacity = String(Math.min(1, t / .045) * (1 - arrive));
        el.style.setProperty('--dispatch-copy', String(1 - smooth(Math.min(1, t / .12))));
        el.style.setProperty('--dispatch-mark', String(mix(1, .7, arrive)));
        el.dataset.phase = t < .22 ? 'compress' : t < .82 ? 'travel' : 'arrive';
        if (trail.current && gradient.current && t > .22) {
          const tail = Math.max(0, travel - .16);
          const points = Array.from({ length: 13 }, (_, i) => curve(mix(tail, travel, i / 12)));
          trail.current.setAttribute('d', points.map((p, i) => `${i ? 'L' : 'M'} ${p.x} ${p.y}`).join(' '));
          trail.current.style.opacity = String(.7 * (1 - arrive));
          gradient.current.setAttribute('x1', String(points[0].x));
          gradient.current.setAttribute('y1', String(points[0].y));
          gradient.current.setAttribute('x2', String(center.x));
          gradient.current.setAttribute('y2', String(center.y));
        }
        if (t >= .82 && !landed) {
          landed = true;
          callbacks.current.onLand(flight.id);
          if (halo.current) {
            halo.current.style.left = `${to.x}px`;
            halo.current.style.top = `${to.y}px`;
            haloAnimation = halo.current.animate([
              { transform: 'translate(-50%,-50%) scale(.65)', opacity: .65 },
              { transform: 'translate(-50%,-50%) scale(2.8)', opacity: 0 },
            ], { duration: 650, easing: 'cubic-bezier(.16,1,.3,1)', fill: 'both' });
          }
        }
        if (t < 1) frame = requestAnimationFrame(draw);
        else timer = setTimeout(finish, 550);
      };
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(find);
    return () => {
      cancelAnimationFrame(frame); clearTimeout(timer); haloAnimation?.cancel();
      root?.removeEventListener('pointerdown', interrupt);
      root?.removeEventListener('wheel', interrupt);
    };
  }, [flight.id, flight.result, canvas, historical]);

  // Processing and outcome feedback belong to the island, not a second notification card.
  if (!isTask) return null;
  return createPortal(<>
    <svg className="map-dispatch-trail" aria-hidden="true">
      <defs><linearGradient ref={gradient} id={gradientId} gradientUnits="userSpaceOnUse">
        <stop stopColor="#6fbcce" stopOpacity="0" /><stop offset="1" stopColor="#87d9cf" />
      </linearGradient></defs>
      <path ref={trail} fill="none" stroke={`url(#${gradientId})`} strokeWidth="2.5" strokeLinecap="round" />
    </svg>
    <div ref={element} className="map-dispatch-flight" data-testid="map-dispatch-flight" data-state="task"
      data-task-id={flight.result?.type === 'task' ? flight.result.taskId : undefined} aria-hidden="true">
      <div className="map-dispatch-symbol"><ArgusMark size={25} /></div>
      <div className="map-dispatch-copy"><strong>{flight.text}</strong><span>{zh ? '进入任务地图' : 'Into your task map'}</span></div>
    </div>
    <div ref={halo} className="map-dispatch-halo" aria-hidden="true" />
  </>, document.body);
}
