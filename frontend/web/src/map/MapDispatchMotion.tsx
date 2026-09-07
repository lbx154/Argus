import { useEffect, useId, useRef, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, ArrowUpRight, Check, GitBranch, LoaderCircle } from 'lucide-react';
import type { MessageDispatch } from './submission';

export interface MapDispatchFlight {
  id: number; text: string; origin: { x: number; y: number };
  result?: MessageDispatch;
}
export function MapDispatchMotion({ flight, canvas, zh, pendingLabel, historical = false, onReveal, onFinish }: {
  flight: MapDispatchFlight; canvas: RefObject<HTMLDivElement>; zh: boolean; pendingLabel?: string; historical?: boolean;
  onReveal: (taskId: string) => void; onFinish: (id: number) => void;
}) {
  const element = useRef<HTMLDivElement>(null);
  const trail = useRef<SVGPathElement>(null);
  const gradientId = `dispatch-trail-${useId().replace(/:/g, '')}`;
  const callbacks = useRef({ onReveal, onFinish });
  callbacks.current = { onReveal, onFinish };
  const waiting = useRef({ x: flight.origin.x, y: flight.origin.y });
  const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  useEffect(() => {
    const el = element.current, root = canvas.current;
    if (!el || !root) return;
    const box = root.getBoundingClientRect();
    const composer = root.querySelector('.map-composer-dock')?.getBoundingClientRect();
    waiting.current = {
      x: Math.max(12, Math.min(innerWidth - el.offsetWidth - 12, box.left + (box.width - el.offsetWidth) / 2)),
      y: Math.max(box.top + 85, (composer?.top ?? box.bottom - 80) - 112),
    };
    const target = `translate3d(${waiting.current.x}px,${waiting.current.y}px,0)`;
    el.style.transform = target;
    if (reduced() || !el.animate) return;
    const animation = el.animate([
      { transform: `translate3d(${Math.max(12, Math.min(innerWidth - el.offsetWidth - 12, flight.origin.x))}px,${flight.origin.y}px,0) scale(.94)`, opacity: 0 },
      { opacity: 1, offset: .2 },
      { transform: target, opacity: 1 },
    ], { duration: 520, easing: 'cubic-bezier(.18,.85,.24,1)', fill: 'backwards' });
    return () => animation.cancel();
  }, [flight.id, canvas]);
  useEffect(() => {
    if (!flight.result) return;
    const el = element.current;
    if (!el) return;
    let frame = 0, timer: ReturnType<typeof setTimeout> | undefined;
    let animation: Animation | undefined, glow: Animation | undefined, trailAnimation: Animation | undefined;
    const finish = () => callbacks.current.onFinish(flight.id);
    if (flight.result.type === 'settled' || historical) {
      timer = setTimeout(finish, flight.result.type === 'settled' && flight.result.outcome === 'error' ? 2400 : 1300);
      return () => clearTimeout(timer);
    }
    const taskId = flight.result.taskId;
    const started = performance.now();
    let revealAt = 0;
    const find = () => {
      if (performance.now() - started > 10000) { finish(); return; }
      if (performance.now() - revealAt > 700) {
        callbacks.current.onReveal(taskId);
        revealAt = performance.now();
      }
      const target = canvas.current?.querySelector<HTMLElement>(`.map-macro[data-task-id="${CSS.escape(taskId)}"]`);
      if (!target || target.getBoundingClientRect().width < 8) { frame = requestAnimationFrame(find); return; }
      timer = setTimeout(() => {
        if (!target.isConnected) { find(); return; }
        const box = target.getBoundingClientRect();
        const scale = Math.min(1.2, Math.max(.5, box.width / el.offsetWidth));
        const x = box.left + box.width / 2 - el.offsetWidth * scale / 2;
        const y = box.top + Math.min(box.height / 2, 90) - el.offsetHeight * scale / 2;
        el.dataset.landing = 'true';
        if (trail.current && !reduced()) {
          const sx = waiting.current.x + el.offsetWidth / 2, sy = waiting.current.y + el.offsetHeight / 2;
          const tx = x + el.offsetWidth * scale / 2, ty = y + el.offsetHeight * scale / 2;
          trail.current.setAttribute('d', `M ${sx} ${sy} C ${sx} ${Math.min(sy, ty) - 70}, ${tx} ${Math.min(sy, ty) - 70}, ${tx} ${ty}`);
          trailAnimation = trail.current.animate([{ strokeDashoffset: 1, opacity: 0 }, { opacity: .9, offset: .2 }, { strokeDashoffset: 0, opacity: .8, offset: .7 }, { strokeDashoffset: 0, opacity: 0 }], { duration: 1150, easing: 'cubic-bezier(.2,.6,.2,1)', fill: 'both' });
        }
        if (reduced() || !el.animate) { finish(); return; }
        animation = el.animate([
          { transform: `translate3d(${waiting.current.x}px,${waiting.current.y}px,0) scale(1)`, opacity: 1 },
          { transform: `translate3d(${(waiting.current.x + x) / 2}px,${Math.min(waiting.current.y, y) - 25}px,0) scale(.94)`, opacity: .95, offset: .5 },
          { transform: `translate3d(${x}px,${y}px,0) scale(${scale})`, opacity: 0 },
        ], { duration: 700, easing: 'cubic-bezier(.35,0,.18,1)', fill: 'forwards' });
        glow = target.animate([
          { boxShadow: '0 0 0 0 rgba(74,139,187,0)' },
          { boxShadow: '0 0 0 8px rgba(74,139,187,.24),0 12px 50px rgba(74,139,187,.18)', offset: .4 },
          { boxShadow: '0 0 0 18px rgba(74,139,187,0)' },
        ], { duration: 900, delay: 420, easing: 'ease-out' });
        glow.onfinish = finish;
      }, reduced() ? 0 : 430);
    };
    frame = requestAnimationFrame(find);
    return () => { cancelAnimationFrame(frame); clearTimeout(timer); animation?.cancel(); glow?.cancel(); trailAnimation?.cancel(); };
  }, [flight.id, flight.result, canvas, historical]);
  const settled = flight.result?.type === 'settled' ? flight.result.outcome : null;
  const label = settled === 'error' ? (zh ? '发送未完成，草稿已保留' : 'Not sent · draft preserved')
    : settled === 'cancelled' ? (zh ? '已停止等待' : 'Stopped waiting')
    : settled === 'message' ? (zh ? 'Argus 已回应' : 'Argus replied')
    : flight.result?.type === 'task' ? (historical ? (zh ? '任务已发送至当前会话' : 'Task sent to the current session') : (zh ? '任务已接收，进入地图' : 'Task accepted · adding to the map'))
    : pendingLabel || (zh ? '正在理解你的任务…' : 'Understanding your request…');
  return createPortal(<><svg className="map-dispatch-trail" aria-hidden="true"><defs><linearGradient id={gradientId}><stop stopColor="#5e94d6" /><stop offset="1" stopColor="#73d7c4" /></linearGradient></defs><path ref={trail} pathLength={1} fill="none" stroke={`url(#${gradientId})`} strokeWidth="3" strokeDasharray="1" strokeLinecap="round" /></svg><div ref={element} className="map-dispatch-flight" data-testid="map-dispatch-flight"
    data-task-id={flight.result?.type === 'task' ? flight.result.taskId : undefined} data-state={settled || (flight.result ? 'task' : 'sending')} style={{ transformOrigin: 'top left' }} role="status" aria-live="polite">
    <div className="map-dispatch-symbol">{flight.result?.type === 'task' ? <GitBranch size={19} /> : settled === 'error' ? <AlertTriangle size={19} /> : settled ? <Check size={19} /> : <ArrowUpRight size={19} />}</div>
    <div className="map-dispatch-copy"><strong>{flight.text}</strong><span>{!settled && <LoaderCircle size={12} />}{label}</span></div>
    <i className="map-dispatch-shimmer" aria-hidden="true" />
  </div></>, document.body);
}
