import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefObject,
} from "react";
import { useReactFlow, type Viewport } from "@xyflow/react";
import type { MacroNode } from "./MacroTaskNode";
import { zoomTarget } from "./submap";

export const INITIAL_VIEWPORT = { x: 52, y: 125, zoom: 0.24 };
export interface CameraMemory {
  viewport: Viewport;
  overview: Viewport;
  focusId: string | null;
  detailed: boolean;
}
const MIN_ZOOM = 0.035,
  MAX_ZOOM = 3.5;
const clamp = (n: number, lo: number, hi: number) =>
  Math.max(lo, Math.min(hi, n));
type ReaderRect = Parameters<MacroNode["data"]["readStep"]>[1];

function viewingArea(el: HTMLElement) {
  const bounds = el.getBoundingClientRect();
  const toolbar = el
    .querySelector(".map-canvas-toolbar")
    ?.getBoundingClientRect();
  const composer = el
    .querySelector(".map-composer-dock")
    ?.getBoundingClientRect();
  const minimap = el
    .querySelector(".react-flow__minimap")
    ?.getBoundingClientRect();
  const legend = el.querySelector(".map-legend")?.getBoundingClientRect();
  const controls = el
    .querySelector(".react-flow__controls")
    ?.getBoundingClientRect();
  const left = el.clientWidth < 640 ? 20 : 50;
  const top = Math.max(
    toolbar ? toolbar.bottom - bounds.top + 20 : 85,
    el.clientWidth < 640 && legend ? legend.bottom - bounds.top + 16 : 0,
    el.clientWidth < 640 && controls ? controls.bottom - bounds.top + 16 : 0,
  );
  const bottom = Math.max(
    composer ? bounds.bottom - composer.top + 24 : 100,
    minimap ? bounds.bottom - minimap.top + 20 : 0,
  );
  return {
    x: left,
    y: top,
    width: Math.max(180, el.clientWidth - left * 2),
    height: Math.max(100, el.clientHeight - top - bottom),
  };
}

/** Camera/opacity updates never update node dimensions or edge geometry. */
export function useSemanticCamera(
  root: RefObject<HTMLDivElement>,
  composerVisible = true,
) {
  const flow = useReactFlow<MacroNode>();
  const [focusId, setFocusId] = useState<string | null>(null);
  const [detailed, setDetailed] = useState(false);
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });
  const currentFocus = useRef<string | null>(null);
  const reading = useRef(false);
  const allowRefit = useRef(false);
  const fitOnResize = useRef(false);
  const refitOverview = useRef<() => void>(() => {});
  const readerOwner = useRef<string | null>(null);
  const readerTarget = useRef<{ id: string; rect: ReaderRect } | null>(null);
  const refitReader = useRef<(id: string, rect: ReaderRect) => void>(() => {});
  const refit = useRef<(id: string) => void>(() => {});
  const [reducedMotion, setReducedMotion] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const overview = useRef<Viewport>(INITIAL_VIEWPORT);
  const pointer = useRef<{ x: number; y: number; until: number } | null>(null);
  const lockedFocus = useRef<string | null>(null);
  const fittedDetail = useRef<{ id: string; zoom: number } | null>(null);
  const raf = useRef<number>(0);
  const target = useRef<Viewport | null>(null);
  const cancelWheel = useCallback(() => {
    cancelAnimationFrame(raf.current);
    raf.current = 0;
    target.current = null;
  }, []);
  const onMove = useCallback(
    (event: unknown, viewport: Viewport) => {
      if (event) {
        fitOnResize.current = false;
        allowRefit.current = false;
      }
      const el = root.current;
      if (!el) return;
      const center = { x: el.clientWidth / 2, y: el.clientHeight / 2 };
      const point =
        pointer.current && pointer.current.until > performance.now()
          ? pointer.current
          : center;
      const id =
        lockedFocus.current ??
        (reading.current ? readerOwner.current : null) ??
        zoomTarget(flow.getNodes(), viewport, point, center);
      const contentScale = id ? flow.getNode(id)?.data.frame.scale || 1 : 1;
      const normalAlpha =
        clamp((viewport.zoom - 0.3) / 0.14, 0, 1) *
        clamp((viewport.zoom * contentScale - 0.28) / 0.3, 0, 1);
      const fitted = fittedDetail.current;
      const alpha = reading.current
        ? 1
        : fitted?.id === id
          ? clamp(
              (viewport.zoom - fitted.zoom * 0.65) / (fitted.zoom * 0.35),
              0,
              1,
            )
          : normalAlpha;
      currentFocus.current = alpha > 0 ? id : null;
      el.style.setProperty("--detail-alpha", String(alpha));
      el.style.setProperty("--summary-alpha", String(1 - alpha));
      el.style.setProperty(
        "--context-alpha",
        String(id ? 1 - alpha * 0.72 : 1),
      );
      el.dataset.zoom = viewport.zoom.toFixed(3);
      setFocusId(alpha > 0 && id ? id : null);
      setDetailed(alpha >= 0.55);
      if (
        viewport.zoom <= 0.32 &&
        !lockedFocus.current &&
        !target.current &&
        !fittedDetail.current
      )
        overview.current = viewport;
    },
    [flow, root],
  );
  const enter = useCallback(
    (id: string) => {
      fitOnResize.current = false;
      cancelWheel();
      const node = flow.getNode(id),
        el = root.current;
      if (!node || !el) return;
      if (flow.getZoom() <= 0.32 && !fittedDetail.current)
        overview.current = flow.getViewport();
      lockedFocus.current = id;
      reading.current = false;
      allowRefit.current = true;
      // Keep a readable scale for long tasks; the same canvas pans to the remaining steps.
      const area = viewingArea(el);
      const zoom = clamp(
        Math.max(
          Math.min(
            area.width / (node.width || 1440),
            area.height / (node.height || 1080),
          ),
          el.clientWidth < 640 || node.data.layout.steps.length > 20
            ? 0.6 / (node.data.frame.scale || 1)
            : 0,
        ),
        MIN_ZOOM,
        MAX_ZOOM,
      );
      fittedDetail.current = { id, zoom };
      const width = node.width || 1440,
        height = node.height || 1080;
      const x =
        area.x +
        Math.max(0, (area.width - width * zoom) / 2) -
        node.position.x * zoom;
      const y =
        area.y +
        Math.max(0, (area.height - height * zoom) / 2) -
        node.position.y * zoom;
      void flow
        .setViewport({ x, y, zoom }, { duration: reducedMotion ? 0 : 380 })
        .then(() => {
          lockedFocus.current = null;
        });
    },
    [cancelWheel, flow, reducedMotion, root],
  );
  refit.current = enter;
  const back = useCallback(() => {
    fitOnResize.current = false;
    cancelWheel();
    lockedFocus.current = null;
    reading.current = false;
    allowRefit.current = false;
    fittedDetail.current = null;
    pointer.current = null;
    const cardId = root.current?.querySelector<HTMLElement>(
      '.map-macro[data-focused="true"]',
    )?.dataset.cardId;
    void flow
      .setViewport(overview.current, {
        duration: reducedMotion ? 0 : 320,
      })
      .then(() => {
        if (cardId)
          root.current
            ?.querySelector<HTMLButtonElement>(
              `[data-testid="map-card"][data-card-id="${CSS.escape(cardId)}"]`,
            )
            ?.focus({ preventScroll: true });
      });
  }, [cancelWheel, flow, reducedMotion, root]);
  const readStep = useCallback(
    (
      id: string,
      rect: {
        x: number;
        y: number;
        width: number;
        height: number;
        scale: number;
      },
    ) => {
      fitOnResize.current = false;
      cancelWheel();
      lockedFocus.current = id;
      const node = flow.getNode(id);
      const el = root.current;
      if (!node || !el) return;
      reading.current = true;
      allowRefit.current = true;
      readerOwner.current = id;
      readerTarget.current = { id, rect };
      const area = viewingArea(el);
      const zoom = Math.min(
        MAX_ZOOM,
        Math.min(1.05, area.width / rect.width, area.height / rect.height) /
          rect.scale,
      );
      void flow
        .setViewport(
          {
            x:
              area.x +
              area.width / 2 -
              (node.position.x + (rect.x + rect.width / 2) * rect.scale) * zoom,
            y:
              area.y +
              area.height / 2 -
              (node.position.y + (rect.y + rect.height / 2) * rect.scale) *
                zoom,
            zoom,
          },
          {
            duration: reducedMotion ? 0 : 340,
          },
        )
        .then(() => {
          lockedFocus.current = null;
        });
    },
    [cancelWheel, flow, reducedMotion, root],
  );
  refitReader.current = readStep;
  const navigate = useCallback(
    (position: { x: number; y: number }) => {
      fitOnResize.current = false;
      cancelWheel();
      reading.current = false;
      allowRefit.current = false;
      lockedFocus.current = null;
      pointer.current = null;
      void flow.setCenter(position.x, position.y, {
        zoom: flow.getZoom(),
        duration: reducedMotion ? 0 : 180,
      });
    },
    [cancelWheel, flow, reducedMotion],
  );
  const fit = useCallback(() => {
    fitOnResize.current = true;
    cancelWheel();
    lockedFocus.current = null;
    reading.current = false;
    allowRefit.current = false;
    fittedDetail.current = null;
    pointer.current = null;
    const el = root.current;
    const nodes = flow.getNodes().filter((n) => !n.hidden);
    if (!el || !nodes.length) return;
    const bounds = flow.getNodesBounds(nodes);
    const area = viewingArea(el);
    const zoom = clamp(
      Math.min(
        area.width / (bounds.width + 160),
        area.height / (bounds.height + 160),
      ),
      MIN_ZOOM,
      0.27,
    );
    void flow.setViewport(
      {
        x: area.x + (area.width - bounds.width * zoom) / 2 - bounds.x * zoom,
        y: area.y + (area.height - bounds.height * zoom) / 2 - bounds.y * zoom,
        zoom,
      },
      { duration: reducedMotion ? 0 : 320 },
    );
  }, [cancelWheel, flow, reducedMotion, root]);
  refitOverview.current = fit;
  const fitUpdatedScene = useCallback(() => {
    if (fitOnResize.current) refitOverview.current();
    else if (allowRefit.current && reading.current && readerTarget.current)
      refitReader.current(readerTarget.current.id, readerTarget.current.rect);
    else if (allowRefit.current && currentFocus.current)
      refit.current(currentFocus.current);
  }, []);
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    let timer: ReturnType<typeof setTimeout>;
    const dock = el.querySelector(".map-composer-dock");
    let width = -1,
      height = -1,
      dockHeight = -1;
    const observer = new ResizeObserver(() => {
      const nextDockHeight = dock?.getBoundingClientRect().height || 0;
      if (
        width === el.clientWidth &&
        height === el.clientHeight &&
        dockHeight === nextDockHeight
      )
        return;
      width = el.clientWidth;
      height = el.clientHeight;
      dockHeight = nextDockHeight;
      el.style.setProperty("--map-composer-height", `${dockHeight}px`);
      setCanvasSize((previous) =>
        previous.width === width && previous.height === height
          ? previous
          : { width, height },
      );
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (allowRefit.current && reading.current && readerTarget.current)
          refitReader.current(
            readerTarget.current.id,
            readerTarget.current.rect,
          );
        else if (allowRefit.current && currentFocus.current && !reading.current)
          refit.current(currentFocus.current);
        else if (fitOnResize.current) refitOverview.current();
      }, 100);
    });
    observer.observe(el);
    if (dock) observer.observe(dock);
    return () => {
      observer.disconnect();
      clearTimeout(timer);
    };
  }, [root, composerVisible]);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const changed = () => setReducedMotion(query.matches);
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  }, []);
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const wheel = (event: WheelEvent) => {
      if (
        (event.target as Element).closest(
          ".nowheel, .map-canvas-toolbar, .map-legend, .react-flow__minimap, .react-flow__controls",
        )
      )
        return;
      event.preventDefault();
      event.stopPropagation();
      fitOnResize.current = false;
      reading.current = false;
      allowRefit.current = true;
      lockedFocus.current = null;
      const rect = el.getBoundingClientRect();
      const px = event.clientX - rect.left,
        py = event.clientY - rect.top;
      pointer.current = { x: px, y: py, until: performance.now() + 1000 };
      const from = target.current ?? flow.getViewport();
      if (!target.current && from.zoom <= 0.32 && !fittedDetail.current)
        overview.current = from;
      const delta =
        event.deltaY *
        (event.deltaMode === 1
          ? 16
          : event.deltaMode === 2
            ? el.clientHeight
            : 1);
      const zoom = clamp(
        from.zoom * Math.exp(-clamp(delta, -400, 400) * 0.002),
        MIN_ZOOM,
        MAX_ZOOM,
      );
      target.current = {
        x: px - ((px - from.x) * zoom) / from.zoom,
        y: py - ((py - from.y) * zoom) / from.zoom,
        zoom,
      };
      if (reducedMotion) {
        void flow.setViewport(target.current);
        target.current = null;
        return;
      }
      if (raf.current) return;
      let previous = performance.now();
      const tick = (now: number) => {
        if (!target.current) return;
        const t = 1 - Math.exp(-Math.min(now - previous, 64) / 55);
        previous = now;
        const current = flow.getViewport(),
          dest = target.current;
        const settled =
          Math.abs(dest.zoom - current.zoom) < 0.00015 &&
          Math.abs(dest.x - current.x) + Math.abs(dest.y - current.y) < 0.15;
        void flow.setViewport(
          settled
            ? dest
            : {
                x: current.x + (dest.x - current.x) * t,
                y: current.y + (dest.y - current.y) * t,
                zoom: current.zoom + (dest.zoom - current.zoom) * t,
              },
        );
        if (settled) {
          raf.current = 0;
          target.current = null;
        } else raf.current = requestAnimationFrame(tick);
      };
      raf.current = requestAnimationFrame(tick);
    };
    const pointerdown = () => {
      cancelWheel();
      lockedFocus.current = null;
      pointer.current = null;
    };
    const key = (event: KeyboardEvent) => {
      if (
        event.key === "Escape" &&
        !["INPUT", "TEXTAREA", "SELECT"].includes(
          (event.target as Element).tagName,
        )
      )
        back();
    };
    el.addEventListener("wheel", wheel, { passive: false, capture: true });
    el.addEventListener("pointerdown", pointerdown, true);
    window.addEventListener("keydown", key);
    return () => {
      cancelWheel();
      el.removeEventListener("wheel", wheel, true);
      el.removeEventListener("pointerdown", pointerdown, true);
      window.removeEventListener("keydown", key);
    };
  }, [back, cancelWheel, flow, reducedMotion, root]);
  const capture = useCallback((): CameraMemory => ({
    viewport: flow.getViewport(), overview: overview.current,
    focusId: currentFocus.current, detailed: !!currentFocus.current &&
      (reading.current || !!fittedDetail.current || flow.getZoom() >= 0.44),
  }), [flow]);
  const restore = useCallback((saved: CameraMemory) => {
    cancelWheel();
    overview.current = saved.overview;
    const id = saved.focusId && flow.getNode(saved.focusId) ? saved.focusId : null;
    lockedFocus.current = saved.detailed ? id : null;
    fittedDetail.current = saved.detailed && id ? { id, zoom: saved.viewport.zoom } : null;
    allowRefit.current = false;
    fitOnResize.current = false;
    void flow.setViewport(saved.viewport, { duration: 0 });
    onMove(null, saved.viewport);
  }, [cancelWheel, flow, onMove]);
  return {
    capture,
    restore,
    focusId,
    canvasSize,
    detailed,
    enter,
    back,
    fit,
    fitUpdatedScene,
    navigate,
    readStep,
    onMove,
    reducedMotion,
  };
}
