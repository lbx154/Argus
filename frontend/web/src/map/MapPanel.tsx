import type { ConversationPagination } from '../useConversationHistory';
import { errorText } from '../lib/format';
import { MapConversation } from './MapConversation';
import { ComposerRuntime } from '../components/ComposerRuntime';
import { WorkStatusBar } from '../components/WorkStatusBar';
import { Button } from '../components/primitives';
import { Modal, ModalHeader } from '../components/Modal';
import { MapReaderContent } from './MapReaderContent';
import { Activity, PackageCheck, MessageCircle } from 'lucide-react';
import { ProjectRoleLogs } from '../components/EventStream';
import { useRoleLog } from '../useRoleLog';
import { MapDispatchMotion, type MapDispatchFlight } from './MapDispatchMotion';
import type { MapSend, DispatchObserver } from './submission';
import { splitDraft } from './presentation';
import { useMapGrowth } from './useMapGrowth';
import { stepIdentity } from './growth';
import './motion.css';
import './alive.css';
import { ARRIVAL_WINDOW_MS, arrivalEdgeDelay, attention as spotlight, useSettledPositions } from './alive';
import { memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { replaceEqualDeep, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  type Edge,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useNodesInitialized,
} from "@xyflow/react";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  GitBranch,
  History,
  Info,
  LocateFixed,
  Maximize2,
  Expand,
  Shrink,
  Pause,
  Play,
  RotateCcw,
  Search,
  X,
} from "lucide-react";
import { api, type Snapshot, type MessageRouteOverride } from "../api";
import { useI18n } from "../i18n";
import { ACTIVE, buildMap, currentTask, statusKey, taskDependencies, type Dataset, } from "./model";
import { layoutScene } from "./submap";
import { edgeLanes, relationPorts } from "./graphLayout";
import { MacroTaskNode, MapArtifactContext, type MacroNode } from "./MacroTaskNode";
import { ZoomStepContext } from "./zoomStep";
import { INITIAL_VIEWPORT, useSemanticCamera } from "./useSemanticCamera";
import { MapComposer, type MapComposerProps } from "./MapComposer";
import { referenceText, type CardReference } from "./presentation";
import type { ArtifactInfo, DeliveryReceipt, EventMsg } from "../../../core/src/types";
import "@xyflow/react/dist/style.css";
import "./map.css";
import "./submap.css";
import "./atlas.css";
import "./design.css";
import { MapRelationEdge } from "./MapRelationEdge";
import { livePollInterval, mapIsPaused, mergeMapProgress } from "./incremental";
import { latestManagerMessage } from "../lib/taskLanguage";
import { taskFigures } from "./cardFigure";
import { MapCardFigureContext } from "./cardContext";
import { recalledView, rememberView } from "./viewMemory";

interface MapWorkspaceActions {
  conversationEvents: EventMsg[];
  conversationPagination?: ConversationPagination;
  connected: boolean;
  artifacts: ArtifactInfo[];
  deliveryCount: number;
  onOpenDelivery: () => void;
  onOpenReceipt: (receipt: DeliveryReceipt, path?: string) => void;
  onOpenArtifact: (path: string) => void;
  pendingQuestion?: ReactNode;
  /** Delivery receipts, newest first: the figures of the tasks they came from. */
  deliveries?: DeliveryReceipt[];
  /** The team status line with 暂停团队 / 继续, the same one the chat shows. */
  teamStatus?: ReactNode;
}

/** How long the opening fit waits for saved explanations before it shows the map anyway. */
const OPENING_WAIT_MS = 700;
const NODE_TYPES = { task: MacroTaskNode };
const EDGE_TYPES = { relation: MapRelationEdge };
/** The canvas's zoom buttons, named in the reader's language. */
const ZH_CONTROLS = { 'controls.ariaLabel': '地图缩放', 'controls.zoomIn.ariaLabel': '放大', 'controls.zoomOut.ariaLabel': '缩小', 'controls.fitView.ariaLabel': '显示整张地图' };
const EN_CONTROLS = { 'controls.ariaLabel': 'Map zoom', 'controls.zoomIn.ariaLabel': 'Zoom in', 'controls.zoomOut.ariaLabel': 'Zoom out', 'controls.fitView.ariaLabel': 'Show the whole map' };
/** The map's key (ⓘ in the toolbar): what each kind of card is. */
export const MAP_CARD_LEGEND: Array<[[string, string], string, string]> = [
  [["任务卡", "Task card"], "团队拆出来、能单独完成和验收的一件事", "one piece of work the team split out that can be finished and checked on its own"],
];
// Minimap fills echo the card state palette a step lighter, so the overview
// inset reads as a status heatmap instead of undifferentiated confetti.
const MINIMAP_STATUS: Record<string, string> = {
  done: "#a9c4b6",
  running: "#9fb6cc",
  question: "#dcc797",
  failed: "#d9a79f",
  review_unavailable: "#cdbba3",
  held: "#cdbba3",
  paused: "#cdbba3",
  superseded: "#c5bdcd",
  aborted: "#c6c8cb",
  skipped: "#c6c8cb",
  missing: "#c6c8cb",
};

export function MapCanvas({
  data,
  zh,
  composer,
  snapshot,
  pendingLabel,
  readOnly,
  sessionId,
  viewKey,
  paused,
  actions,
  immersive = false,
  onImmersiveChange,
}: {
  data: Dataset;
  sessionId: string;
  zh: boolean;
  composer: MapComposerProps;
  snapshot: Snapshot;
  pendingLabel?: string;
  readOnly: boolean;
  viewKey: string;
  paused: boolean;
  actions: MapWorkspaceActions;
  /** Whether plan-replacement links are drawn; the panel owns the switch so
   * the options menu can flip it on screens that hide the legend. */
  immersive?: boolean;
  onImmersiveChange?: (immersive: boolean) => void;
}) {
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [conversationOpen, setConversationOpen] = useState(false);
  const [flight, setFlight] = useState<MapDispatchFlight | null>(null);
  const dispatchSerial = useRef(0);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const graph = useMemo(() => buildMap(data.tasks), [data.tasks]);
  // Requests still being read wait by the composer; the newest few.
  const [nodes, setNodes, onNodesChange] = useNodesState<MacroNode>([]);
  const canvasRef = useRef<HTMLDivElement>(null);
  const camera = useSemanticCamera(canvasRef, !readOnly);
  const nodesReady = useNodesInitialized();
  const initialFit = useRef(false);
  // Gates the canvas fade-in: the first frames render at an arbitrary
  // viewport until the opening fit lands, and nobody should see that.
  const [fitted, setFitted] = useState(false);
  const savedView = useRef<ReturnType<typeof recalledView> | null>(null);
  if (!savedView.current) savedView.current = recalledView(viewKey);
  const [selectedParts, setSelectedParts] = useState<ReadonlyMap<string, number>>(() =>
    new Map(savedView.current?.scene?.cards.map(card => [card.task.id, card.part])));
  const [pendingCard, setPendingCard] = useState<string | null>(null);
  const [seenCards] = useState(() => new Set(savedView.current?.scene?.cards.map((card) => card.id)));
  const focusedNode = nodes.find((n) => n.id === camera.focusId);
  const [readingCopy, setReadingCopy] = useState<{ nodeId: string; key: string } | null>(null);
  const readCopy = useCallback((nodeId: string, key: string | null) => {
    // An explicit task modal outlives changes to its background card's focus.
    // Step readers still close when their owning card leaves detailed mode.
    setReadingCopy(previous => key ? { nodeId, key }
      : previous?.nodeId === nodeId && !data.tasks.some(task => task.id === previous.key) ? null : previous);
  }, [data.tasks]);
  useEffect(() => {
    setReadingCopy(previous => previous && (previous.nodeId === camera.focusId
      || data.tasks.some(task => task.id === previous.key)) ? previous : null);
  }, [camera.focusId, data.tasks]);
  const readingTask = readingCopy ? data.tasks.find(task => task.id === readingCopy.key) : undefined;
  const readingAnswer = readingTask?.turn_kind === 'qa'
    ? data.events.find(event => event.item_id === readingTask.id && event.type === 'turn.replied') : undefined;
  const readingNode = readingCopy ? nodes.find(node => node.id === readingCopy.nodeId) : undefined;
  const [traceId, setTraceId] = useState<string | null>(null);
  const tracedTask = graph.tasks.find((task) => task.id === traceId);
  const dependencies = useMemo(
    () => tracedTask ? taskDependencies(graph, tracedTask.id) : null,
    [graph, tracedTask],
  );
  const traceTasks = useMemo(() => tracedTask && dependencies ? new Set([
    tracedTask.id, ...dependencies.upstream.map((task) => task.id),
    ...dependencies.downstream.map((task) => task.id),
  ]) : null, [tracedTask, dependencies]);
  // Lines follow recorded relationships; time order never adds a dependency.
  const links = useMemo(
    () => graph.links,
    [graph, zh],
  );

  const sceneCache = useRef<ReturnType<typeof layoutScene> | undefined>(savedView.current.scene);
  // Before the canvas is measured the window stands in for it, so a phone
  // lays its cards out once instead of re-placing them after the first frame.
  const narrow = (camera.canvasSize.width || window.innerWidth) < 640;
  const workspace = snapshot.session.workdir;
  const scene = useMemo(() => {
    const next = layoutScene(graph, data.events, zh, sceneCache.current, links, selectedParts, narrow, workspace);
    sceneCache.current = replaceEqualDeep(sceneCache.current, next);
    return sceneCache.current;
  }, [graph, data.events, zh, links, selectedParts, narrow, workspace]);
  const positions = useSettledPositions(scene.positions, camera.reducedMotion);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const lit = useMemo(() => spotlight(scene.links, hoverId), [scene.links, hoverId]);
  // A first opening composes the map in reading order; a replay walks a live
  // session's history the way the datasets already can.
  const [arriving, setArriving] = useState(false);
  const [replaying, setReplaying] = useState(false);
  const ordinalOf = useMemo(() => new Map(scene.cards.map((card) => [card.id, card.ordinal])), [scene.cards]);
  const growth = useMapGrowth(scene, !!data.history_loading);
  const submitFromMap: MapSend = async (text, files = [], _observe, options) => {
    // A note for the running turn has no flight of its own; it joins that turn.
    if (options?.whileRunning) return composer.onSend(text, files, undefined, options);
    const id = ++dispatchSerial.current;
    const source = canvasRef.current?.querySelector('.map-composer')?.getBoundingClientRect();
    setFlight({ id, text: splitDraft(text).text.replace(/\s+/g, ' ').slice(0, 180), origin: { x: source?.left ?? 20, y: source?.top ?? innerHeight - 100, width: source?.width ?? 260, height: source?.height ?? 56 } });
    const observe: DispatchObserver = (result) => {
      if (alive.current) setFlight((current) => current?.id === id ? { ...current, result } : current);
    };
    try {
      const accepted = await composer.onSend(text, files, observe);
      if (!accepted) observe({ type: 'settled', outcome: 'error' });
      return accepted;
    } catch (error) {
      observe({ type: 'settled', outcome: 'error' });
      throw error;
    }
  };
  // The map is fitted when its first cards appear (a map opened before its
  // project has a task fits when the tasks come), and again whenever cards
  // come or go while the reader has not moved the view (fitUpdatedScene).
  const [waited, setWaited] = useState(false);
  useEffect(() => {
    if (!nodesReady || waited) return;
    const timer = setTimeout(() => setWaited(true), OPENING_WAIT_MS);
    return () => clearTimeout(timer);
  }, [nodesReady, waited]);
  useEffect(() => {
    if (!nodesReady || initialFit.current) return;
    if (savedView.current?.camera && data.history_loading && !waited) return;
    const frame = requestAnimationFrame(() => {
      initialFit.current = true;
      const saved = savedView.current?.camera;
      const canvas = canvasRef.current!;
      // A viewport is in screen pixels. A desktop view cannot be restored
      // onto a phone's stacked cards or a differently sized canvas.
      // A phone too opens on the whole map: fitted to one task, its tall card
      // filled the screen and ran under the composer.
      if (saved && saved.canvasSize.width === canvas.clientWidth && saved.canvasSize.height === canvas.clientHeight)
        camera.restore(saved);
      else camera.fit();
      setFitted(true);
    });
    return () => cancelAnimationFrame(frame);
  }, [nodesReady, camera.fit, camera.restore, scene.cards, data.history_loading, waited]);
  useEffect(() => {
    if (!fitted || savedView.current?.camera || camera.reducedMotion) return;
    setArriving(true);
    const timer = window.setTimeout(() => setArriving(false), ARRIVAL_WINDOW_MS);
    return () => window.clearTimeout(timer);
  }, [fitted, camera.reducedMotion]);
  useEffect(() => {
    const save = () => {
      if (initialFit.current) rememberView(viewKey, { scene: sceneCache.current, camera: camera.capture() });
    };
    window.addEventListener("pagehide", save);
    return () => { save(); window.removeEventListener("pagehide", save); };
  }, [viewKey, camera.capture]);
  useEffect(() => {
    if (!nodesReady) return;
    const frame = requestAnimationFrame(camera.fitUpdatedScene);
    return () => cancelAnimationFrame(frame);
  }, [scene.structure, nodesReady, camera.fitUpdatedScene]);
  const composerRef = useRef(composer);
  composerRef.current = composer;
  const quote = useCallback(
    (ref: CardReference) => {
      if (readOnly) return;
      const c = composerRef.current;
      if (camera.canvasSize.width < 640) {
        setReadingCopy(null);
        camera.back();
      }
      c.onChange(referenceText(ref) + c.value);
      window.setTimeout(
        () =>
          document
            .querySelector<HTMLTextAreaElement>(".map-composer textarea")
            ?.focus(),
        0,
      );
    },
    [readOnly, camera.canvasSize.width, camera.back],
  );
  const [menu, setMenu] = useState<{
    ref: CardReference;
    x: number;
    y: number;
  } | null>(null);
  const showMenu = useCallback(
    (ref: CardReference, point: { x: number; y: number }) => {
      if (readOnly) return;
      setMenu({
        ref,
        x: Math.min(window.innerWidth - 180, point.x),
        y: Math.min(window.innerHeight - 140, point.y),
      });
    },
    [readOnly],
  );
  useEffect(() => {
    if (!menu) return;
    const dismiss = (e: PointerEvent) => {
      if (!(e.target as Element).closest(".map-context-menu")) setMenu(null);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setMenu(null);
      }
    };
    window.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", key, true);
    return () => {
      window.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", key, true);
    };
  }, [menu]);
  const [query, setQuery] = useState("");
  // One predicate serves the dimmed-card filter, the match counter, and Enter
  // cycling, so the three can never disagree about what "a match" is.
  const cardSearchText = useCallback(
    (card: { task: { id: string; title: string; objective?: string }; part: number }) =>
      `${card.task.title} ${card.task.objective ?? ""} ${card.part > 1 ? (zh ? `续篇 ${card.part - 1}` : `Continued ${card.part - 1}`) : ""}`.toLowerCase(),
    [zh],
  );
  const matches = useMemo(
    () =>
      query
        ? scene.cards.filter((card) =>
            cardSearchText(card).includes(query.toLowerCase()),
          )
        : [],
    [query, scene.cards, cardSearchText],
  );
  const matchIndex = matches.findIndex((card) => card.id === camera.focusId);
  const [visibleCount, setVisibleCount] = useState(graph.tasks.length);
  const [playing, setPlaying] = useState(false);
  const [focusFeedback, setFocusFeedback] = useState("");
  const traceTask = useCallback((id: string | null) => {
    setTraceId(id);
    setQuery("");
    setFocusFeedback("");
    if (id) {
      const { upstream, downstream } = taskDependencies(graph, id);
      camera.fit(new Set([id, ...upstream.map((task) => task.id), ...downstream.map((task) => task.id)]));
    } else camera.fit();
  }, [camera.fit, graph]);
  const openCard = useCallback((id: string) => {
    const part = sceneCache.current?.allCards.find(card => card.id === id);
    if (part) {
      setSelectedParts(current => new Map([...current, [part.task.id, part.part]]));
      setPendingCard(part.task.id);
      return;
    }
    if (tracedTask) traceTask(id === tracedTask.id ? null : id);
    else camera.enter(id);
  }, [camera.enter, tracedTask, traceTask]);
  useEffect(() => {
    if (!pendingCard || !nodes.some((node) => node.id === pendingCard)) return;
    camera.enter(pendingCard);
    setPendingCard(null);
  }, [pendingCard, nodes, camera.enter]);
  const showOverview = useCallback(() => {
    setTraceId(null);
    setFocusFeedback("");
    camera.fit();
  }, [camera.fit]);
  useEffect(() => {
    setNodes((previous) => {
      return replaceEqualDeep(previous, scene.cards.map((card) => ({
        id: card.id,
        type: "task",
        position: positions[card.id] ?? scene.positions[card.id],
        width: scene.frames[card.id].width,
        height: scene.frames[card.id].height,
        style: {
          width: scene.frames[card.id].width,
          height: scene.frames[card.id].height,
        },
        data: {
          ...card,
          zh,
          open: openCard,
          readStep: camera.readStep,
          readCopy,
          menu: showMenu,
          quote,
          source: data.id,
          readOnly,
          managerMessage: card.task.role === 'manager' && ACTIVE.has(card.task.status) ? pendingLabel : undefined,
          paused,
          seenCards,
          restoring: !!savedView.current?.camera && !initialFit.current,
          layout: scene.layouts[card.id],
          frame: scene.frames[card.id],
          focused: false,
          detailed: false,
        },
      })) as MacroNode[]);
    });
  }, [
    graph,
    scene,
    positions,
    zh,
    setNodes,
    openCard,
    camera.readStep,
    readCopy,
    showMenu,
    quote,
    data.id,
    pendingLabel,
    readOnly,
    paused,
    seenCards,
  ]);
  useEffect(() => {
    setVisibleCount((c) => Math.min(Math.max(c, 1), graph.tasks.length));
  }, [graph.tasks.length]);
  useEffect(() => {
    if (!playing || camera.detailed) return;
    const timer = window.setInterval(
      () =>
        setVisibleCount((count) => {
          if (count >= graph.tasks.length) {
            setPlaying(false);
            return count;
          }
          return count + 1;
        }),
      900,
    );
    return () => window.clearInterval(timer);
  }, [playing, graph.tasks.length, camera.detailed]);
  // A live replay returns to the present on its own once the last card has
  // had time to take its place.
  useEffect(() => {
    if (!replaying || playing || visibleCount < graph.tasks.length) return;
    const timer = window.setTimeout(() => setReplaying(false), 1400);
    return () => window.clearTimeout(timer);
  }, [replaying, playing, visibleCount, graph.tasks.length]);
  const visibleIds = useMemo(
    () => new Set(
      scene.cards
        .filter(
          (card) => (!replaying) || card.ordinal <= visibleCount,
        )
        .map((card) => card.id),
    ),
    [scene.cards, visibleCount, replaying],
  );
  const previousDisplay = useRef<MacroNode[]>([]);
  const displayNodes = useMemo(
    () => {
      const next = nodes.map<MacroNode>((n) => ({
        ...n,
        hidden: !visibleIds.has(n.id),
        // A card opened for reading scrolls and selects; it is not dragged.
        draggable: false,
        data: {
          ...n.data,
          focused: n.id === camera.focusId,
          detailed: camera.detailed && n.id === camera.focusId,
          canvasSize: camera.canvasSize,
          lit: lit.nodes.has(n.id),
          hovered: n.id === hoverId,
          arriving,
          revealing: replaying,
          growthDelay: growth.cards[n.id],
          dispatchState: flight?.result?.type === 'task' && flight.result.taskId === n.data.task.id
            ? flight.landed ? 'landed' : 'receiving'
            : undefined,
          growingSteps: Object.fromEntries(n.data.layout.steps.flatMap((step) => {
            const delay = growth.steps[stepIdentity(n.id, step.id)];
            return delay == null ? [] : [[step.id, delay]];
          })),
          growingLinks: Object.fromEntries(n.data.layout.links.flatMap((link) => {
            const delay = growth.links[stepIdentity(n.id, link.id)];
            return delay == null ? [] : [[link.id, delay]];
          })),
        },
        style: {
          ...n.style,
          opacity:
            (traceTasks && !traceTasks.has(n.data.task.id)) ||
            (query && !cardSearchText(n.data).includes(query.toLowerCase()))
              ? 0.22
              : 1,
        },
      }));
      previousDisplay.current = replaceEqualDeep(previousDisplay.current, next);
      return previousDisplay.current;
    },
    [
      nodes,
      visibleIds,
      camera.focusId,
      camera.detailed,
      camera.canvasSize,
      query,
      cardSearchText,
      readingCopy,
      zh,
      growth,
      flight,
      traceTasks,
      lit,
      hoverId,
      arriving,
      replaying,
    ],
  );
  const edges: Edge[] = useMemo(
    () => {
      const visibleLinks = scene.links.filter(
        (e) =>
          visibleIds.has(e.source) &&
          visibleIds.has(e.target),
      );
      // Single pass over the visible links, covering all edge kinds; no
      // per-edge rescans.
      const lanes = edgeLanes(visibleLinks);
      const taskByCard = new Map(scene.cards.map((card) => [card.id, card.task]));
      const activeTargets = new Set<string>();
      if (!paused) {
        for (const card of scene.cards)
          if (ACTIVE.has(card.task.status)) activeTargets.add(card.id);
      }
      return visibleLinks.map((e, index) => {
        const sourceTask = taskByCard.get(e.source)?.id;
        const targetTask = taskByCard.get(e.target)?.id;
        const highlighted = !!tracedTask && e.kind === "dependency" &&
          (sourceTask === tracedTask.id || targetTask === tracedTask.id);
        const muted = !!tracedTask && !highlighted &&
          !(e.kind === "continuation" && sourceTask === tracedTask.id);
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          ...relationPorts(
            { ...positions[e.source], ...scene.frames[e.source] },
            { ...positions[e.target], ...scene.frames[e.target] },
          ),
          type: "relation",
          data: {
            // New evidence draws its own link; otherwise a first opening
            // draws every relation once its source card stands, and a replay
            // draws the links into the card just revealed.
            growthDelay:
              growth.links[e.id] ??
              (arriving
                ? arrivalEdgeDelay(ordinalOf.get(e.source) ?? 1)
                : replaying && ordinalOf.get(e.target) === visibleCount
                  ? 120
                  : undefined),
            active: activeTargets.has(e.target),
            lit: lit.edges.has(e.id),
            lane: lanes[index],
            muted,
            // A line whose only label would be the name of its kind ("same
            // study", "dependency") says that with its stroke and the key. The
            // words come forward when the reader points at either task; a map
            // of ten tasks is not annotated with the same two words nine times.
            quiet: !e.stated && (e.kind === "context" || (e.kind === "dependency" && !e.label)),
          },
          className: `map-edge-${e.kind}${lit.edges.has(e.id) ? " is-lit" : ""}`,
          label: e.label ||
            (e.kind === "dependency"
                ? zh
                  ? "依赖"
                  : "Dependency"
                : zh
                  ? "同一研究"
                  : "Related work"),
          labelStyle: {
            fontSize: 30,
            fill: "#6685a4",
          },
          labelBgPadding: [12, 6] as [number, number],
          labelBgBorderRadius: 12,
          labelBgStyle: { fill: "var(--map-paper)", fillOpacity: 0.96 },
          style: {
            opacity: muted ? 0.12 : 1,
            stroke: e.cycle
              ? "#dc6648"
              : e.kind === "dependency"
                  ? "#527fa7"
                  : "#7594ad",
            // Dependencies stay the strongest line; context is a fainter,
            // sparser dash.
            strokeWidth: highlighted ? 2.6 : e.kind === "dependency" ? 2 : 1.6,
            vectorEffect: "non-scaling-stroke",
            // The order work happened in is the spine of most maps, so it is
            // a continuous line; a dash is kept for "related work", which is
            // an association and not a path.
            strokeDasharray:
              e.kind === "dependency" || e.kind === "context" || e.kind === "continuation"
                ? undefined
                : "5 6",
          },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: "#8aa5b8",
            width: 32,
            height: 32,
          },
          ariaLabel:
            e.evidence ||
            (e.kind === "dependency"
              ? `Dependency: ${e.source} → ${e.target}`
              : `Recorded order: ${e.source} → ${e.target}`),
        };
      });
    },
    [
      scene.links,
      scene.frames,
      visibleIds,
      zh,
      growth,
        paused,
      scene.cards,
      tracedTask,
      positions,
      lit,
      arriving,
      replaying,
      visibleCount,
      ordinalOf,
    ],
  );
  const cyclic = graph.links.some((link) => link.cycle);
  const focus = (id: string) => {
    setTraceId(null);
    setFocusFeedback("");
    setVisibleCount((c) =>
      Math.max(c, scene.cards.find((card) => card.id === id)?.ordinal || 1),
    );
    camera.enter(id);
  };
  const locateCurrent = () => {
    const target = currentTask(data.tasks);
    if (target) {
      focus(
        scene.cards.filter((card) => card.task.id === target.id).at(-1)!.id,
      );
      setFocusFeedback("");
    } else
      setFocusFeedback(
        zh ? "发送一个目标，地图就会开始生长" : "Send a goal to start your map",
      );
  };
  // Map-wide shortcuts; typing surfaces (search, composer, notes) keep every key.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return;
      const target = e.target as Element | null;
      if (target?.closest('input, textarea, select, [contenteditable], [role="dialog"]')) return;
      if (e.key === "/") {
        e.preventDefault();
        canvasRef.current
          ?.querySelector<HTMLInputElement>(".map-search input")
          ?.focus();
      } else if (e.key === "f" || e.key === "F") {
        showOverview();
      } else if (
        (e.key === "ArrowRight" || e.key === "ArrowLeft") &&
        camera.detailed &&
        camera.focusId
      ) {
        const order = scene.cards.filter((card) => visibleIds.has(card.id));
        const index = order.findIndex((card) => card.id === camera.focusId);
        if (index < 0) return;
        const next = order[index + (e.key === "ArrowRight" ? 1 : -1)];
        if (next) {
          e.preventDefault();
          camera.enter(next.id);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showOverview, camera.enter, camera.detailed, camera.focusId, scene.cards, visibleIds]);
  const artifactScope = useMemo(
    () => ({ artifacts: actions.artifacts, onOpenArtifact: actions.onOpenArtifact,
      workspace: snapshot.session.workdir }),
    [actions.artifacts, actions.onOpenArtifact, snapshot.session.workdir],
  );
  // The reveal strip floats over the canvas during replay, so the canvas never resizes and the
  // overview camera stays where the reader left it.
  const playbackStrip = (
    <div className="map-playback" data-floating>
      <button
        aria-label={playing ? zh ? "暂停展开" : "Pause reveal" : zh ? "播放展开" : "Play reveal"}
        onClick={() => {
          if (visibleCount >= graph.tasks.length) setVisibleCount(1);
          setPlaying((v) => !v);
        }}
      >
        {playing ? <Pause size={14} /> : <Play size={14} />}
      </button>
      <button
        aria-label={zh ? "从头展开" : "Restart reveal"}
        onClick={() => {
          setPlaying(false);
          setVisibleCount(1);
        }}
      >
        <RotateCcw size={13} />
      </button>
      <span>{zh ? "逐卡展开" : "Reveal cards"}</span>
      <input
        aria-label={zh ? "逐卡展开" : "Reveal cards"}
        type="range"
        min={Math.min(1, graph.tasks.length)}
        max={graph.tasks.length}
        value={visibleCount}
        onChange={(e) => {
          setPlaying(false);
          setVisibleCount(Number(e.target.value));
        }}
      />
      <span className="map-count">
        {visibleCount} / {graph.tasks.length}
      </span>
      <button
        aria-label={zh ? "展开下一张" : "Reveal next card"}
        disabled={visibleCount >= graph.tasks.length}
        onClick={() =>
          setVisibleCount((c) => Math.min(graph.tasks.length, c + 1))
        }
      >
        <ChevronRight size={14} />
      </button>
      <small>{zh ? "时间顺序" : "Chronological order"}</small>
      {(
        <button
          className="map-replay-exit"
          aria-label={zh ? "回到当前" : "Back to the present"}
          title={zh ? "回到当前" : "Back to the present"}
          onClick={() => {
            setPlaying(false);
            setReplaying(false);
            setVisibleCount(graph.tasks.length);
          }}
        >
          <X size={14} />
        </button>
      )}
    </div>
  );
  return (
    <ZoomStepContext.Provider value={camera.zoomSteps}>
    <MapArtifactContext.Provider value={artifactScope}>
      {!replaying && actions.teamStatus}
      {tracedTask && dependencies && (
        <nav className="map-dependencies" aria-label={zh ? "任务来路与去向" : "Task dependencies"}>
          <div>
            <strong>{zh ? "来路" : "Depends on"}</strong>
            {dependencies.upstream.length ? dependencies.upstream.map((task) => (
              <button key={task.id} title={task.title} onClick={() => traceTask(task.id)}>{task.title}</button>
            )) : <span>{zh ? "无已记录的前置依赖" : "No recorded prerequisites"}</span>}
          </div>
          <div>
            <strong>{zh ? "当前" : "Selected"}</strong>
            <button className="map-trace-task" title={tracedTask.title}
              onClick={() => focus(scene.cards.find((card) => card.task.id === tracedTask.id)!.id)}>
              {tracedTask.title} · {zh ? "查看任务" : "Open task"}
            </button>
          </div>
          <div>
            <strong>{zh ? "去向" : "Enables"}</strong>
            {dependencies.downstream.length ? dependencies.downstream.map((task) => (
              <button key={task.id} title={task.title} onClick={() => traceTask(task.id)}>{task.title}</button>
            )) : <span>{zh ? "无已记录的后续依赖" : "No recorded dependents"}</span>}
          </div>
        </nav>
      )}
      <div className="map-workspace">
        <div
          ref={canvasRef}
          className="map-canvas-wrap"
          data-focused={!!camera.focusId}
          data-detailed={camera.detailed}
          data-fitted={fitted}
          data-hovering={!!hoverId && !camera.detailed}
        >
          {conversationOpen && <MapConversation snapshot={snapshot} events={actions.conversationEvents} pagination={actions.conversationPagination} connected={actions.connected} pending={composer.pending} artifacts={actions.artifacts} zh={zh} onClose={() => setConversationOpen(false)} onOpenArtifact={actions.onOpenArtifact} onOpenDelivery={actions.onOpenReceipt} />}
          {agentsOpen && <aside className="map-agent-drawer nowheel nodrag nopan">
            <header className="mb-3 flex items-center justify-between text-sm font-semibold">
              <span>{zh ? '工作详情' : 'Work details'}</span>
              <button type="button" className="rounded p-2" onClick={() => setAgentsOpen(false)} aria-label={zh ? '关闭工作详情' : 'Close work details'}><X size={17} /></button>
            </header>
            <ProjectRoleLogs snapshot={snapshot} connected={actions.connected} details taskId={focusedNode?.data.task.id} />
          </aside>}
          {flight && !readOnly && <MapDispatchMotion flight={flight} canvas={canvasRef} zh={zh}
            onReveal={(taskId) => { if (data.tasks.some((task) => task.id === taskId)) camera.fit(); }}
            onLand={(id) => setFlight((current) => current?.id === id ? { ...current, landed: true } : current)}
            onFinish={(id) => setFlight((current) => current?.id === id ? null : current)} />}
          <div className="map-canvas-toolbar nowheel">
            {onImmersiveChange && (
              <button
                type="button"
                className="map-immersive-toggle"
                data-testid="map-immersive-toggle"
                aria-pressed={immersive}
                onClick={() => onImmersiveChange(!immersive)}
                title={immersive ? (zh ? "显示项目列表、总览和图例" : "Show projects, overview and legend") : (zh ? "全屏画布：收起周围的内容" : "Full canvas: fold everything around it")}
                aria-label={immersive ? (zh ? "退出全屏画布" : "Leave full canvas") : (zh ? "全屏画布" : "Full canvas")}
              >
                {immersive ? <Shrink size={15} /> : <Expand size={15} />}
                <span>{immersive ? (zh ? "显示周围" : "Show panels") : (zh ? "全屏画布" : "Full canvas")}</span>
              </button>
            )}
            <label className="map-search">
              <Search size={14} />
              <input
                aria-label={zh ? "搜索地图任务" : "Search map tasks"}
                placeholder={zh ? "搜索任务…" : "Find a task…"}
                title={zh ? "Enter 逐个跳转匹配" : "Enter jumps through matches"}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && matches.length) {
                    focus(matches[(matchIndex + 1) % matches.length].id);
                  } else if (e.key === "Escape" && query) {
                    // Clear the search without also backing the camera out.
                    e.stopPropagation();
                    setQuery("");
                  }
                }}
              />
              {query && (
                <span className="map-search-count" aria-live="polite">
                  {matches.length
                    ? matchIndex >= 0 ? `${matchIndex + 1}/${matches.length}` : `${matches.length} ${zh ? "项" : "found"}`
                    : zh
                      ? "无匹配"
                      : "0 found"}
                </span>
              )}
            </label>
            <button
              onClick={locateCurrent}
              title={zh ? "定位当前或最近任务" : "Locate current or latest task"}
              aria-label={zh ? "定位当前" : "Locate current"}
            >
              <LocateFixed size={15} />
              <span>{zh ? "定位当前" : "Locate current"}</span>
            </button>
            <button
              className="map-fit-button"
              onClick={showOverview}
              title={zh ? "适配全图" : "Fit map"}
              aria-label={zh ? "适配全图" : "Fit map"}
            >
              <Maximize2 size={15} />
            </button>
            <button
              type="button"
              aria-label={zh ? "来路 / 去向" : "Trace dependencies"}
              title={zh ? "突出显示任务的直接前后依赖；点击卡片切换，再次点击退出" : "Highlight direct dependencies; click a card to switch, click it again to exit"}
              aria-pressed={!!tracedTask}
              disabled={!graph.tasks.length}
              onClick={() => traceTask(tracedTask ? null : focusedNode?.data.task.id ?? currentTask(data.tasks)?.id ?? null)}
            >
              <GitBranch size={15} /><span>{zh ? "来路 / 去向" : "Dependencies"}</span>
            </button>
            {graph.tasks.length > 1 && !camera.detailed && !replaying && (
              <button
                onClick={() => {
                  // The overview stays where it is; the map fills back in.
                  setReplaying(true);
                  setVisibleCount(1);
                  setPlaying(true);
                }}
                title={zh ? "回放这项研究是如何展开的" : "Replay how this research unfolded"}
                aria-label={zh ? "回放" : "Replay"}
              >
                <History size={15} />
                <span>{zh ? "回放" : "Replay"}</span>
              </button>
            )}
            <span className="map-legend-info">
              <button type="button" data-testid="map-legend-info" aria-label={zh ? "卡片是怎么分的" : "What the cards are"}
                aria-describedby="map-legend-cards">
                <Info size={15} />
              </button>
              <span role="tooltip" id="map-legend-cards">{MAP_CARD_LEGEND.map(([name, zhText, enText]) => (
                <span key={name[0]} style={{ display: "block" }}><b>{zh ? name[0] : name[1]}</b> = {zh ? zhText : enText}</span>
              ))}</span>
            </span>
            {camera.detailed && (
              <button
                onClick={camera.back}
                className="map-back-button"
                aria-label={zh ? "返回全图" : "Overview"}
              >
                <ArrowLeft size={14} />
                <span>{zh ? "返回全图" : "Overview"}</span>
              </button>
            )}
            {/* On a phone the pager sits in this row, so it never floats over
                the card heading; larger screens use the card's own part links. */}
            {camera.detailed && focusedNode && focusedNode.data.partCount > 1 && (
              <nav
                className="map-part-switcher"
                aria-label={zh ? "切换任务部分" : "Switch task part"}
              >
                <button
                  aria-label={zh ? "上一部分" : "Previous part"}
                  disabled={!focusedNode.data.previousId}
                  onClick={() =>
                    focusedNode.data.previousId &&
                    openCard(focusedNode.data.previousId)
                  }
                >
                  <ChevronLeft size={15} />
                </button>
                <span>
                  {focusedNode.data.part} / {focusedNode.data.partCount}
                </span>
                <button
                  aria-label={zh ? "下一部分" : "Next part"}
                  disabled={!focusedNode.data.nextId}
                  onClick={() =>
                    focusedNode.data.nextId &&
                    openCard(focusedNode.data.nextId)
                  }
                >
                  <ChevronRight size={15} />
                </button>
              </nav>
            )}
            <span className="map-toolbar-gap" aria-hidden />
            {/* The conversation, the agents' activity and the deliveries open
                from the same row as the navigation, so the map has one
                toolbar rather than a stack of them. */}
            {(
              <div className="map-workspace-actions">
                <button
                  type="button"
                  aria-expanded={conversationOpen}
                  aria-label={zh ? '对话' : 'Conversation'}
                  title={zh ? '与 Argus 的对话' : 'Your conversation with Argus'}
                  onClick={() => { setConversationOpen((open) => !open); setAgentsOpen(false); }}
                >
                  <MessageCircle size={15} /><span>{zh ? '对话' : 'Conversation'}</span>
                </button>
                <button
                  type="button"
                  className="map-agent-toggle"
                  aria-expanded={agentsOpen}
                  aria-label={zh ? 'Agent 动态' : 'Agent activity'}
                  title={zh ? '各个角色正在做什么' : 'What each role is doing'}
                  onClick={() => { setAgentsOpen((open) => !open); setConversationOpen(false); }}
                >
                  <Activity size={15} /><i data-active={(!paused && snapshot.roles.some(role => role.active)) || composer.pending} aria-hidden /><span>{zh ? '工作详情' : 'Work details'}</span>
                </button>
                <button
                  type="button"
                  className="map-delivery-toggle"
                  disabled={!actions.deliveryCount}
                  aria-label={zh ? '交付成果' : 'Deliveries'}
                  title={zh ? '已交付的成果' : 'What has been delivered'}
                  onClick={actions.onOpenDelivery}
                >
                  <PackageCheck size={15} /><span>{zh ? '交付成果' : 'Deliveries'}</span>{actions.deliveryCount > 0 && <b>{actions.deliveryCount}</b>}
                </button>
              </div>
            )}
          </div>
          {focusFeedback && (
            <div className="map-feedback" role="status">
              {focusFeedback}
            </div>
          )}
          {graph.tasks.length === 0 ? (
            <div className="map-empty">
              <GitBranch size={36} />
              <h3>{zh ? "把一个目标，变成可见的成果" : "Turn a goal into a visible result"}</h3>
              <p>
                {readOnly
                  ? zh
                    ? "尚无任务记录。"
                    : "No task records are available."
                  : zh
                    ? "描述你想完成的事情，看 Argus 规划、执行、审查，最后在这里交付。"
                    : "Describe your goal. Watch Argus plan, build, review, and deliver here."}
              </p>
              {!readOnly && <div className="map-starters">{(zh ? [
                ['交互实验', '做一个交互式实验室，用动画展示 Dijkstra 和 A* 怎样寻找最短路径。让我能画障碍、单步播放、比较探索范围，并验证两个算法的结果一致。'],
                ['数据洞察', '用一组可复现的模拟数据，做一个辛普森悖论交互演示。让我能切换整体和分组视角，看结论怎样反转，附上验证过程。'],
                ['产品原型', '做一个精致的个人旅行规划网页。我能调整预算和出行天数，比较三种行程方案，并将选中的方案导出。让手机上也方便操作。'],
              ] : [
                ['Interactive lab', 'Build an interactive Dijkstra vs A* pathfinding lab with editable obstacles, step-by-step animation, and correctness checks.'],
                ['Data insights', 'Create an interactive Simpson’s paradox demo using reproducible synthetic data, with aggregate and grouped views and validation.'],
                ['Product prototype', 'Build a polished travel planner. Let me adjust budget and duration, compare three itineraries, and export my choice. Make it easy to use on a phone.'],
              ]).map(([label, prompt]) => <button key={label} type="button" onClick={() => { composer.onChange(prompt); requestAnimationFrame(() => canvasRef.current?.querySelector('textarea')?.focus()); }}>{label} ↗</button>)}</div>}
            </div>
          ) : (
            <ReactFlow<MacroNode>
              nodes={displayNodes}
              edges={edges}
              nodeTypes={NODE_TYPES}
              edgeTypes={EDGE_TYPES}
              onNodesChange={onNodesChange}
              onNodeMouseEnter={(_, node) => setHoverId(node.id)}
              onNodeMouseLeave={() => setHoverId(null)}
              onMove={camera.onMove}
              defaultViewport={INITIAL_VIEWPORT}
              minZoom={0.01}
              maxZoom={3.5}
              nodesDraggable={false}
              nodesFocusable={false}
              nodesConnectable={false}
              edgesReconnectable={false}
              zoomOnScroll={false}
              zoomOnPinch
              zoomOnDoubleClick={false}
              deleteKeyCode={null}
              selectionKeyCode={null}
              onlyRenderVisibleElements={fitted && nodesReady}
              proOptions={{ hideAttribution: true }}
              ariaLabelConfig={zh ? ZH_CONTROLS : EN_CONTROLS}
            >
              <Background
                variant={BackgroundVariant.Dots}
                gap={88}
                size={3}
                color="var(--map-dot)"
              />
              <Controls
                orientation="horizontal"
                showInteractive={false}
                onFitView={showOverview}
                fitViewOptions={{
                  padding: 0.16,
                  maxZoom: 0.27,
                  minZoom: 0.01,
                  duration: camera.reducedMotion ? 0 : 320,
                }}
              />
              <MiniMap<MacroNode>
                nodeColor={(n) =>
                  MINIMAP_STATUS[statusKey((n as MacroNode).data.task)] ?? "#c6c8cb"
                }
                maskColor="var(--map-minimap-mask)"
                maskStrokeColor="#6d737a"
                maskStrokeWidth={1.5}
                onClick={(_, point) => camera.navigate(point)}
                pannable
                zoomable
                ariaLabel={zh ? "地图导航预览" : "Map navigation preview"}
              />
            </ReactFlow>
          )}
          {replaying && playbackStrip}
          <div className="map-legend nowheel">
            <span title={zh ? "没有记录关系的卡片按时间先后相连" : "Cards with no recorded relation are joined in time order"}>
              <b className="order" />
              {zh ? "先后" : "Order"}
            </span>
            <span>
              <b />
              {zh ? "任务依赖" : "Dependency"}
            </span>
          </div>

          {!readOnly && (
            <MapComposer {...composer} before={actions.pendingQuestion} pendingLabel={pendingLabel} progressVisible={!conversationOpen}
              dispatchStatus={flight?.result?.type === 'task' ? flight.landed ? 'task' : 'launching' : flight?.result?.outcome}
              onSend={submitFromMap} overview={!camera.detailed} startCompact={immersive} />
          )}
          {menu && !readOnly && (
            <div
              className="map-context-menu"
              role="menu"
              style={{ left: menu.x, top: menu.y }}
            >
              <button
                role="menuitem"
                onClick={() => {
                  quote(menu.ref);
                  setMenu(null);
                }}
              >
                {zh ? "问这张卡" : "Ask about this card"}
              </button>
              {(
                <>
                  <button
                    role="menuitem"
                    onClick={() => {
                      composer.onRouteOverrideChange?.("task");
                      quote(menu.ref);
                      setMenu(null);
                    }}
                  >
                    {zh ? "从这里展开" : "Branch from here"}
                  </button>
                </>
              )}
            </div>
          )}
          {(cyclic || graph.missing > 0) && (
            <div className="map-graph-warning">
              {cyclic
                ? zh
                  ? "检测到循环依赖（红线）"
                  : "Cycle detected (red links)"
                : `${graph.missing} ${zh ? "个依赖不在当前记录范围内" : "dependencies outside the available history"}`}
            </div>
          )}
        </div>
      </div>
      <Modal open={!!readingTask} onClose={() => setReadingCopy(null)} label={readingTask?.turn_kind === 'qa' ? (zh ? '问答' : 'Q&A') : zh ? "任务说明" : "Task explanation"} width="max-w-3xl map-task-modal">
        {readingTask ? <>
          <ModalHeader title={readingTask.turn_kind === 'qa' ? (zh ? '问答' : 'Q&A') : zh ? "任务说明" : "Task explanation"} sub={readingTask.turn_kind === 'qa' ? readingTask.objective || readingTask.title : readingTask.title} />
          <div className="px-6 pb-6" data-testid="map-task-reading" data-task-id={readingTask.id}>
            {readingNode?.data.completionScope ? <p className="mb-2 text-xs text-ink-dim">{readingNode.data.completionScope}</p> : null}
            <MapReaderContent cardKey={readingTask.id} taskId={readingTask.id} task={readingTask}
              sessionId={sessionId} zh={zh}
              workspace={artifactScope.workspace}
              originalDetail={readingTask.turn_kind === 'qa'
                ? readingAnswer?.text || (readingTask.status === 'running' ? (zh ? '正在回答…' : 'Answering…') : readingTask.status === 'cancelled' ? (zh ? '本次回答已中断。' : 'This answer was interrupted.') : readingTask.latest_action?.text || (zh ? '尚无回答。' : 'No answer recorded.'))
                : readingTask.objective || readingTask.latest_action?.text || (zh ? "这项任务尚无详细记录。" : "No detailed task record is available.")}
              artifacts={artifactScope.artifacts} onOpenArtifact={artifactScope.onOpenArtifact} />
            {!readOnly ? <Button className="mt-3 text-xs" onClick={() => quote({ source: data.id, task_id: readingTask.id,
              task_title: readingTask.title,
              event_ids: readingAnswer ? [readingAnswer.id] : data.events.filter(event => event.item_id === readingTask.id).map(event => event.id), lang: zh ? 'zh' : 'en' })}>{readingTask.turn_kind === 'qa' ? (zh ? '引用这条问答' : 'Reference this Q&A') : zh ? "引用此任务" : "Reference this task"}</Button> : null}
          </div>
        </> : null}
      </Modal>
    </MapArtifactContext.Provider>
    </ZoomStepContext.Provider>
  );
}

/** Memoized: the app re-renders on every streamed event and poll; the map
 * only needs to follow its own (throttled) props, not that firehose. */
export const MapPanel = memo(function MapPanel({
  immersive = false,
  onImmersiveChange,
  snapshot,
  events,
  draft,
  onDraftChange,
  onSend,
  attachments: controlledAttachments,
  onAttachmentsChange,
  pending,
  rewriting,
  focusSignal,
  readOnly = false,
  onOpenSettings,
  routeOverride,
  onRouteOverrideChange,
  conversationEvents,
  conversationPagination,
  connected,
  artifacts,
  deliveryCount,
  onOpenDelivery,
  onOpenReceipt,
  onOpenArtifact,
  pendingQuestion,
  deliveries,
  onStopWork,
  onResumeTeam,
  teamBusy,
}: {
  onStopWork?: () => void;
  onResumeTeam?: () => void;
  teamBusy?: boolean;
  snapshot: Snapshot;
  events: EventMsg[];
  draft: string;
  onDraftChange: (text: string) => void;
  onSend: MapSend;
  attachments?: File[];
  onAttachmentsChange?: (files: File[]) => void;
  pending: boolean;
  rewriting?: boolean;
  focusSignal: number;
  readOnly?: boolean;
  /** The map fills the window: the header, overview and legend step aside. */
  immersive?: boolean;
  onImmersiveChange?: (immersive: boolean) => void;
  onOpenSettings?: () => void;
  routeOverride?: MessageRouteOverride;
  onRouteOverrideChange?: (route: MessageRouteOverride) => void;
} & MapWorkspaceActions) {
  const { locale, t } = useI18n();
  const zh = locale === "zh-CN";
  const [localAttachments, setLocalAttachments] = useState<File[]>([]);
  const attachments = controlledAttachments ?? localAttachments;
  const attachmentRef = useRef(attachments);
  attachmentRef.current = attachments;
  const setAttachments = useCallback((update: File[] | ((current: File[]) => File[])) => {
    const next = typeof update === 'function' ? update(attachmentRef.current) : update;
    attachmentRef.current = next;
    if (onAttachmentsChange) onAttachmentsChange(next);
    else setLocalAttachments(next);
  }, [onAttachmentsChange]);
  const currentDraft = useRef(draft);
  currentDraft.current = draft;
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const send: MapSend = useCallback(async (text, files = [], observe, options) => {
    let failedBeforeAcceptance = false;
    const accepted = await onSend(text, files, (result) => {
      if (result.type === 'settled' && result.outcome === 'error') failedBeforeAcceptance = true;
      if (!mounted.current) return;
      if (result.type === 'settled' && result.outcome === 'error' && !currentDraft.current.trim()) {
        onDraftChange(text);
        setAttachments((current) => current.length ? current : files);
      }
      observe?.(result);
    }, options);
    if (accepted && !failedBeforeAcceptance && mounted.current) {
      if (currentDraft.current === text) onDraftChange("");
      setAttachments((current) =>
        current.filter((file) => !files.includes(file)),
      );
    }
    return accepted;
  }, [onSend, onDraftChange, setAttachments]);
  const roleLog = useRoleLog(snapshot.session.id);
  const managerMessage = latestManagerMessage(roleLog.events);
  const client = useQueryClient();
  // The whole map is one answer; each read asks only for what came after the last.
  const liveKey = ["map-live", snapshot.session.id];
  const viewKey = JSON.stringify([snapshot.session.id, locale]);
  const live = useQuery({
    queryKey: liveKey,
    queryFn: async ({ signal }) => {
      const previous = client.getQueryData<Dataset>(liveKey);
      const next = await api.liveMap(snapshot.session.id, signal, previous?.cursor);
      return mergeMapProgress(client.getQueryData<Dataset>(liveKey), next);
    },
    staleTime: Infinity,
    gcTime: 2 * 60 * 60 * 1000,
    refetchOnMount: "always",
    refetchInterval: (query) => livePollInterval(query.state.data, snapshot), });
  const paused = mapIsPaused(snapshot);
  const latestMapEvent = events.filter((e) => e.run_label !== "map-summary" &&
    /^(life\.(mission\.|phase\.|planner\.task_added)|manager\.turn\.|round\.|agent\.message|engineer\.progress|ui\.argus)/.test(String(e.type))).at(-1);
  const updateKey = JSON.stringify([
    latestMapEvent?.ts,
    latestMapEvent?.event_id || latestMapEvent?.id,
    latestMapEvent?.revision,
    latestMapEvent?.updated_ts,
    latestMapEvent?.status,
    snapshot.backlog,
    paused,
  ]);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // A refresh that lands mid-gesture commits a fresh node set under the
  // operator's fingers — the profiled zoom stutter on live sessions. Track
  // pointer activity on the panel and let refreshes wait out the gesture.
  const interactingUntil = useRef(0);
  const sectionRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const el = sectionRef.current;
    if (!el) return;
    const bump = () => {
      interactingUntil.current = Date.now() + 900;
    };
    const drag = (event: PointerEvent) => {
      if (event.buttons) bump();
    };
    el.addEventListener("wheel", bump, { passive: true });
    el.addEventListener("pointerdown", bump);
    el.addEventListener("pointermove", drag);
    return () => {
      el.removeEventListener("wheel", bump);
      el.removeEventListener("pointerdown", bump);
      el.removeEventListener("pointermove", drag);
    };
  }, []);
  useEffect(() => {
    if (refreshTimer.current) return;
    const fire = () => {
      const wait = interactingUntil.current - Date.now();
      if (wait > 0) {
        refreshTimer.current = setTimeout(fire, wait + 120);
        return;
      }
      refreshTimer.current = null;
      void client.invalidateQueries({
        queryKey: ["map-live", snapshot.session.id],
      });
    };
    refreshTimer.current = setTimeout(fire, 650);
  }, [updateKey, snapshot.session.id, client]);
  useEffect(
    () => () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      refreshTimer.current = null;
    },
    [snapshot.session.id],
  );

  const data = live.data;
  // Stable object identities: fresh actions/composer objects on every render
  // would invalidate the node-data memos in every mounted MapCanvas card.
  // The team line lives at the top of the canvas, beside the overview, so it
  // stays in view when the composer is folded away.
  const teamStatus = useMemo(() => <WorkStatusBar snapshot={snapshot} connected={connected !== false} compact
    managerMessage={managerMessage}
    pending={pending} onStopWork={readOnly ? undefined : onStopWork} onResumeTeam={readOnly ? undefined : onResumeTeam} teamBusy={teamBusy} />,
  [snapshot, connected, readOnly, pending, onStopWork, onResumeTeam, teamBusy, managerMessage]);
  const actions = useMemo<MapWorkspaceActions>(
    () => ({ conversationEvents, conversationPagination, connected, artifacts, deliveryCount,
             onOpenDelivery, onOpenReceipt, onOpenArtifact, pendingQuestion, deliveries,
             teamStatus }),
    [conversationEvents, conversationPagination, connected, artifacts, deliveryCount,
     onOpenDelivery, onOpenReceipt, onOpenArtifact, pendingQuestion, deliveries, teamStatus],
  );
  const composer = useMemo<MapComposerProps>(
    () => ({
      footer: <><ComposerRuntime sid={snapshot.session.id} roles={snapshot.roles} running={snapshot.daemon.alive || Boolean(snapshot.manager_requests?.length)} lastCall={snapshot.last_call} onOpenSettings={readOnly ? undefined : onOpenSettings} /></>,
      routeOverride,
      onRouteOverrideChange,
      value: draft,
      onChange: onDraftChange,
      onSend: send,
      attachments,
      onAttachmentsChange: setAttachments,
      pending,
      rewriting,
      focusSignal,
      zh, }),
    [routeOverride, onRouteOverrideChange, draft, onDraftChange, send, attachments,
     pending, rewriting, focusSignal, snapshot.session.display_name, snapshot.session.id,
     zh, t, snapshot.roles, snapshot.daemon.alive, snapshot.manager_requests, snapshot.last_call,
     readOnly, onOpenSettings, events, connected, onStopWork, onResumeTeam, teamBusy],
  );
  // Each task's figure, loaded separately from the map's task projection.
  const workspace = snapshot.session.workdir || '';
  const cardFigures = useMemo(() => ({
    sid: snapshot.session.id,
    figures: taskFigures(deliveries ?? [], artifacts ?? [], events, workspace),
  }), [snapshot.session.id, deliveries, artifacts, events, workspace]);
  return (
    <MapCardFigureContext.Provider value={cardFigures}>
    <section
      ref={sectionRef}
      className="argus-map"
      data-immersive={immersive || undefined}
      aria-label={zh ? "进度地图" : "Progress map"}
    >
      <header className="map-header">
        <div className="map-heading">
          <h1 title={snapshot.session.display_name}>{snapshot.session.display_name || t("sidebar.unnamedSession")}</h1>
        </div>

      </header>
      {data && live.isError && (
        <div className="map-data-error" role="status">
          {zh ? "地图暂时无法更新。" : "The map can't update right now."}
          <button onClick={() => void live.refetch()}>{zh ? "重试" : "Retry"}</button>
        </div>
      )}
      {live.isError && !data ? (
        <div className="map-empty">
          <h3>{zh ? "地图暂时无法读取" : "Map unavailable"}</h3>
          <p>{errorText(live.error, zh ? "zh-CN" : "en")}</p>
          <button
            onClick={() =>
              void live.refetch()
            }
          >
            {zh ? "重试" : "Retry"}
          </button>
        </div>
      ) : data ? (
        <ReactFlowProvider key={viewKey}>
          <MapCanvas
            data={data}
            actions={actions}
            snapshot={snapshot}
            pendingLabel={managerMessage}
            viewKey={viewKey}
            paused={paused}
            sessionId={snapshot.session.id}
            zh={zh}
            readOnly={readOnly}
            immersive={immersive}
            onImmersiveChange={onImmersiveChange}
            composer={composer}
          />
        </ReactFlowProvider>
      ) : (
        <div className="map-empty is-loading" aria-busy="true">
          <div className="map-ghosts" aria-hidden>
            <i />
            <i />
            <i />
          </div>
          {zh ? "正在载入地图…" : "Loading map…"}
        </div>
      )}
    </section>
    </MapCardFigureContext.Provider>
  );
});
