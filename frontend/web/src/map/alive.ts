import { useEffect, useRef, useState } from "react";
import { translate } from '../i18n';
import { agentRoleName, isAgentRole } from '../lib/agentRoles';

/** The atlas moves the way a colleague's whiteboard does: cards settle into a
 * new arrangement instead of teleporting, relations light up under the
 * pointer, a freshly opened map composes itself in reading order, and the
 * card being worked on keeps a quiet, living pulse. Geometry still comes from
 * the layout model; these helpers only decide how it is shown moving. */

export type Point = { x: number; y: number };
export type Positions = Record<string, Point>;

export const SETTLE_MS = 680;
/** Ease-out cubic: quick departure, gentle landing. */
const ease = (t: number) => 1 - (1 - t) ** 3;

/** Every key of `to` at `progress` (0..1) of the way from `from`; keys that
 * have no starting point simply appear where they belong. */
export function settle(from: Positions, to: Positions, progress: number): Positions {
  const t = ease(Math.min(1, Math.max(0, progress)));
  const out: Positions = {};
  for (const id in to) {
    const a = from[id], b = to[id];
    out[id] = !a || t >= 1 ? b : { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
  }
  return out;
}

/** True when some card that already had a place has been given a new one. */
export function moved(from: Positions, to: Positions): boolean {
  for (const id in to) {
    const a = from[id];
    if (a && (a.x !== to[id].x || a.y !== to[id].y)) return true;
  }
  return false;
}

/** Cards glide to a re-laid-out position instead of jumping. The first layout,
 * new cards, and every change under reduced motion still snap. */
export function useSettledPositions(target: Positions, reducedMotion: boolean, duration = SETTLE_MS): Positions {
  const [shown, setShown] = useState(target);
  const shownRef = useRef(target);
  const targetRef = useRef(target);
  useEffect(() => {
    if (target === targetRef.current) return;
    targetRef.current = target;
    const from = shownRef.current;
    if (reducedMotion || !moved(from, target) || typeof requestAnimationFrame !== "function") {
      shownRef.current = target;
      setShown(target);
      return;
    }
    let frame: number | null = null;
    const started = performance.now();
    const tick = (now: number) => {
      const progress = (now - started) / duration;
      if (progress >= 1) {
        shownRef.current = target;
        setShown(target);
        frame = null;
        return;
      }
      shownRef.current = settle(from, target, progress);
      setShown(shownRef.current);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    // A newer layout retargets mid-flight from wherever the cards are now.
    return () => { if (frame != null) cancelAnimationFrame(frame); };
  }, [target, reducedMotion, duration]);
  return shown;
}

export interface Attention { edges: Set<string>; nodes: Set<string> }
const NOBODY: Attention = { edges: new Set(), nodes: new Set() };

/** What the pointer is asking about: the card under it, the relations that
 * touch it, and the cards on the other end of those relations. */
export function attention(links: Array<{ id: string; source: string; target: string }>, focus: string | null): Attention {
  if (!focus) return NOBODY;
  const edges = new Set<string>(), nodes = new Set<string>([focus]);
  for (const link of links) {
    if (link.source !== focus && link.target !== focus) continue;
    edges.add(link.id);
    nodes.add(link.source);
    nodes.add(link.target);
  }
  return { edges, nodes };
}

/** Opening choreography: cards take the stage in reading order, and each
 * relation is drawn once its source card is standing. Capped so a long
 * history never keeps a reader waiting. */
export const ARRIVAL_WINDOW_MS = 2600;
export const arrivalDelay = (ordinal: number) => Math.min(Math.max(ordinal - 1, 0), 10) * 80;
export const arrivalEdgeDelay = (sourceOrdinal: number) => arrivalDelay(sourceOrdinal) + 320;

/** How long the current work has been under way, in words a person would use. */
export function elapsedLabel(seconds: number, zh: boolean): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return zh ? `${s} 秒` : `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return zh ? `${m} 分钟` : `${m} min`;
  const h = Math.floor(m / 60), rest = m % 60;
  return zh ? `${h} 小时 ${rest} 分` : `${h} h ${String(rest).padStart(2, "0")} min`;
}

export function roleName(role: string | undefined, zh: boolean): string {
  if (!role) return 'Argus';
  return isAgentRole(role) ? agentRoleName(role, key => translate(key, {}, zh ? 'zh-CN' : 'en'))
    : role.charAt(0).toUpperCase() + role.slice(1);
}
