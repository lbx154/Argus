import { MapConversation } from './MapConversation';
import { mapStatusSentence } from './status';
import { PendingBanner } from '../components/PendingBanner';
import { ComposerRuntime } from '../components/ComposerRuntime';
import { WorkStatusBar } from '../components/WorkStatusBar';
import { Button } from '../components/primitives';
import { Modal, ModalHeader } from '../components/Modal';
import { MapReaderContent, type MapReaderSelection } from './MapReaderContent';
import { Activity, PackageCheck, MessageCircle, SlidersHorizontal } from 'lucide-react';
import { AgentActivity } from '../components/AgentActivity';
import { MapDispatchMotion, type MapDispatchFlight } from './MapDispatchMotion';
import type { MapSend, DispatchObserver } from './submission';
import { attentionSummary, splitDraft } from './presentation';
import { projectOverview } from './overview';
import { MapOverview } from './MapOverview';
import { MapLaunchContext } from './launchContext';
import { operatorDecisionCards } from '../../../core/src/decisions';
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
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useNodesInitialized,
  ConnectionMode,
  type ReactFlowInstance,
  type Edge,
  type FinalConnectionState,
  type NodeChange,
} from "@xyflow/react";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Compass,
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
import { readLocalStorage, writeLocalStorage } from "../lib/storage";
import { useI18n } from "../i18n";
import { ACTIVE, attentionTasks, buildMap, connectMap, currentTask, RELATION_KINDS, statusKey, taskDependencies, latestCertifiedTask, type Dataset, type MapTask } from "./model";
import { layoutScene } from "./submap";
import { edgeLanes, relationPorts } from "./graphLayout";
import { MacroTaskNode, MapArtifactContext, MapNotesContext, type MacroNode } from "./MacroTaskNode";
import { ZoomStepContext } from "./zoomStep";
import { groupNotesByNode } from "./notes";
import "./notes.css";
import { INITIAL_VIEWPORT, openCanvasArea, useSemanticCamera } from "./useSemanticCamera";
import { useMapCopy } from "./useMapCopy";
import { useSelectedFoundation } from '../research-brief/foundation';
import { MapComposer, type MapComposerProps } from "./MapComposer";
import { referenceText, type CardReference } from "./presentation";
import type { ArtifactInfo, DeliveryReceipt, EventMsg } from "../../../core/src/types";
import "@xyflow/react/dist/style.css";
import "./map.css";
import "./submap.css";
import "./atlas.css";
import "./design.css";
import { MapRelationEdge } from "./MapRelationEdge";
import { MapHistoryChoice } from "./MapHistoryChoice";
import { livePollInterval, mapIsPaused, mergeMapProgress, parseMapSelection, type MapSelection } from "./incremental";
import { currentWorkStartedAt, currentWorkStatus } from "../lib/workStatus";
import { taskNow } from "../lib/liveActivity";
import { taskFigures } from "./cardFigure";
import { MapCardFigureContext, MapCardNowContext } from "./cardContext";
import { workSummary } from '../lib/workSummary';
import { COUNTED_OUT } from '../lib/projectOverview';
import { recalledView, rememberView } from "./viewMemory";
import { clearOfChrome, humanFrame, isHumanCardId, LINK_KINDS, resolveLinkTarget, placeAroundPeople, useCanvas, type HumanCard, type LinkKind, type Point, type RouteStep } from "./canvas";
import { HumanCardView, HumanLinkView, LinkGuide, type HumanCardNode, type HumanLinkEdge } from "./CanvasNodes";

interface MapWorkspaceActions {
  conversationEvents: EventMsg[];
  connected: boolean;
  artifacts: ArtifactInfo[];
  deliveryCount: number;
  onOpenDelivery: () => void;
  onOpenReceipt: (receipt: DeliveryReceipt, path?: string) => void;
  onOpenArtifact: (path: string) => void;
  onAnswer: () => void;
  /** Delivery receipts, newest first: the overview's 主要成果. */
  deliveries?: DeliveryReceipt[];
  /** The chat confirm card's "开始执行", for a plan waiting on the person. */
  onStartLaunch?: () => Promise<void>;
  /** The team status line with 暂停团队 / 继续, the same one the chat shows. */
  teamStatus?: ReactNode;
}

const NO_RECEIPTS: DeliveryReceipt[] = [];
/** What each recorded relation means, for the legend's hover text. */
const RELATION_HINTS: Partial<Record<string, [string, string]>> = {
  carries_out: ["这项任务在执行你的某条请求", "This task carries out one of your requests"],
  from: ["来自：这项任务由那张卡交给团队后生成", "Came from: the task was made from that card when it was handed to the team"],
  about: ["关于：针对那张卡的提问或回答", "About: a question or answer about that card"],
  checks: ["检查：这项工作在核查那项工作", "Checks: this work checks that work"],
  follows: ["接续：在那项工作之后接着做", "Follows: continues after that work"],
  replaces: ["替代：新的计划替代了旧的", "Replaces: a newer plan replaced the older one"],
};
/** How long the opening fit waits for saved explanations before it shows the map anyway. */
const OPENING_WAIT_MS = 700;
const NODE_TYPES = { task: MacroTaskNode, human: HumanCardView };
const EDGE_TYPES = { relation: MapRelationEdge, human: HumanLinkView };
const CANVAS_HINT_KEY = "argus.map.canvasHintDismissed.v1";
/** The map's key (ⓘ in the toolbar): what each kind of card is. */
export const MAP_CARD_LEGEND: Array<[[string, string], string, string]> = [
  [["任务卡", "Task card"], "团队拆出来、能单独完成和验收的一件事", "one piece of work the team split out that can be finished and checked on its own"],
  [["你的卡", "Your cards"], "你加的想法和资料", "the ideas and files you added"],
  [["必经步骤", "Required steps"], "你定的必须经过的路", "the road you laid that the work must pass through"],
];
type CanvasMenu =
  | { type: "card"; id: string; x: number; y: number }
  | { type: "link"; id: string; x: number; y: number }
  | { type: "connect"; source: string; target: string; x: number; y: number };
const isCanvasNodeId = (id: string) => isHumanCardId(id) || id.startsWith("draft-");
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
  activePhase,
  snapshot,
  currentTaskId,
  events,
  pendingLabel,
  readOnly,
  sessionId,
  viewKey,
  paused,
  actions,
  replacements,
  immersive = false,
  onImmersiveChange,
}: {
  data: Dataset;
  sessionId: string;
  zh: boolean;
  composer: MapComposerProps;
  activePhase?: string;
  snapshot: Snapshot;
  currentTaskId?: string | null;
  events: EventMsg[];
  pendingLabel?: string;
  readOnly: boolean;
  viewKey: string;
  paused: boolean;
  actions: MapWorkspaceActions;
  /** Whether plan-replacement links are drawn; the panel owns the switch so
   * the options menu can flip it on screens that hide the legend. */
  replacements?: { shown: boolean; toggle: () => void };
  immersive?: boolean;
  onImmersiveChange?: (immersive: boolean) => void;
}) {
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [conversationOpen, setConversationOpen] = useState(false);
  const operatorMessage = [...actions.conversationEvents].reverse().find(event => event.type === 'ui.operator');
  const operatorMessageId = operatorMessage ? String(operatorMessage.message_id || operatorMessage.ts || '') : '';
  const lastOperatorMessage = useRef(operatorMessageId);
  const wasPending = useRef(composer.pending);
  // A message about a card (交给团队 / 问这张卡) keeps the canvas in view: the
  // conversation would cover the card and the task it makes, so a small
  // notice links to it instead of opening it.
  const quietUntil = useRef(0);
  const [conversationNotice, setConversationNotice] = useState("");
  useEffect(() => {
    if (Date.now() < quietUntil.current) {
      lastOperatorMessage.current = operatorMessageId;
      wasPending.current = composer.pending;
      return;
    }
    if (data.kind === 'live' && !readOnly && ((operatorMessageId && operatorMessageId !== lastOperatorMessage.current)
      || (composer.pending && !wasPending.current))) {
      setConversationOpen(true);
      setAgentsOpen(false);
    }
    lastOperatorMessage.current = operatorMessageId;
    wasPending.current = composer.pending;
  }, [operatorMessageId, composer.pending, data.kind, readOnly]);
  const [flight, setFlight] = useState<MapDispatchFlight | null>(null);
  const dispatchSerial = useRef(0);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const graph = useMemo(() => buildMap(data.tasks), [data.tasks]);
  // Requests still being read wait by the composer; the newest few.
  const looseRequests = useMemo(() => (data.requests ?? []).slice(-3).reverse(), [data.requests]);
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
  const [expandedMissions, setExpandedMissions] = useState<ReadonlySet<string>>(() => {
    const selected = savedView.current?.scene?.cards.find((card) => card.id === savedView.current?.camera?.focusId);
    return new Set(selected && selected.id !== selected.task.id ? [selected.task.id] : []);
  });
  const [pendingCard, setPendingCard] = useState<string | null>(null);
  const [seenCards] = useState(() => new Set(savedView.current?.scene?.cards.map((card) => card.id)));
  const focusedNode = nodes.find((n) => n.id === camera.focusId);
  const finalReview = useMemo(() => latestCertifiedTask(data.tasks, data.events), [data.tasks, data.events]);
  const foundationChoice = useSelectedFoundation(sessionId, zh ? 'zh-CN' : 'en-US');
  const [readingCopy, setReadingCopy] = useState<{ nodeId: string; key: string; foundationId: string | null } | null>(null);
  const readCopy = useCallback((nodeId: string, key: string | null) => {
    // An explicit task modal outlives changes to its background card's focus.
    // Step readers still close when their owning card leaves detailed mode.
    setReadingCopy(previous => key ? { nodeId, key, foundationId: foundationChoice.id }
      : previous?.nodeId === nodeId && !data.tasks.some(task => task.id === previous.key) ? null : previous);
  }, [data.tasks, foundationChoice.id]);
  useEffect(() => {
    setReadingCopy(previous => previous && (previous.nodeId === camera.focusId
      || data.tasks.some(task => task.id === previous.key)) ? previous : null);
  }, [camera.focusId, data.tasks]);
  const readingTask = readingCopy ? data.tasks.find(task => task.id === readingCopy.key) : undefined;
  const readingAnswer = readingTask?.turn_kind === 'qa'
    ? data.events.find(event => event.item_id === readingTask.id && event.type === 'turn.replied') : undefined;
  const readingNode = readingCopy ? nodes.find(node => node.id === readingCopy.nodeId) : undefined;
  const readingKey = readingTask?.id ?? (readingCopy?.nodeId === camera.focusId ? readingCopy.key : null);
  const attention = useMemo(() => attentionTasks(data.tasks), [data.tasks]);
  const attentionIndex = attention.findIndex((task) => task.id === focusedNode?.data.task.id);
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
  // Opening a card reads what was saved for it; it does not ask for new text.
  const { copy, ready: copyReady, readingRequest, questionContext } = useMapCopy(
    data,
    readingTask?.id || focusedNode?.data.task.id || null,
    zh,
    focusedNode?.data.layout.steps,
    sessionId,
    readingKey,
    readingCopy?.foundationId,
  );
  const readerSelection = useMemo<MapReaderSelection | undefined>(() => readingRequest ? {
    request: readingRequest, questionContext,
  } : undefined, [readingRequest, questionContext]);
  // Lines follow the relations the records state; nothing is written for
  // them, and time order only joins cards no relation reaches.
  const links = useMemo(
    () => connectMap(graph, copy?.relations || [], zh),
    [graph, copy?.relations, zh],
  );
  const relationKinds = useMemo(
    () => new Set(links.flatMap((link) => link.relation ? [link.relation] : [])),
    [links],
  );
  const sceneCache = useRef<ReturnType<typeof layoutScene> | undefined>(savedView.current.scene);
  // Before the canvas is measured the window stands in for it, so a phone
  // lays its cards out once instead of re-placing them after the first frame.
  const narrow = (camera.canvasSize.width || window.innerWidth) < 640;
  const scene = useMemo(() => {
    const next = layoutScene(graph, data.events, zh, sceneCache.current, links, expandedMissions, narrow);
    sceneCache.current = replaceEqualDeep(sceneCache.current, next);
    return sceneCache.current;
  }, [graph, data.events, zh, links, expandedMissions, narrow]);
  // The canvas: what a person placed, linked and moved. Moved cards keep
  // their place; Argus's own cards are laid out around people's cards.
  const editable = data.kind === "live" && !readOnly;
  const canvasApi = useCanvas(sessionId, data.kind === "live");
  const canvasRecord = canvasApi.canvas;
  const peopleBoxes = useMemo(() => canvasRecord.cards.flatMap((card) => {
    const at = canvasRecord.positions[card.id];
    return at ? [{ ...at, ...humanFrame(card) }] : [];
  }), [canvasRecord]);
  const pinnedTasks = useMemo(() => {
    const pinned: Record<string, Point> = {};
    for (const [id, at] of Object.entries(canvasRecord.positions))
      if (!isHumanCardId(id) && scene.frames[id]) pinned[id] = at;
    return pinned;
  }, [canvasRecord.positions, scene.frames]);
  const clearOfPeople = useMemo(
    () => placeAroundPeople(scene.positions, scene.frames, pinnedTasks, peopleBoxes),
    [scene.positions, scene.frames, pinnedTasks, peopleBoxes],
  );
  // Cards glide to a re-laid-out place; relations follow through the store.
  // A card a person moved stands where it was dropped, without a glide.
  const settled = useSettledPositions(clearOfPeople, camera.reducedMotion);
  const positions = useMemo(
    () => Object.keys(pinnedTasks).length ? { ...settled, ...pinnedTasks } : settled,
    [settled, pinnedTasks],
  );
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
    let hasTask = false;
    const aboutCard = splitDraft(text).refs.length > 0;
    if (aboutCard) {
      quietUntil.current = Date.now() + 120_000;
      setConversationNotice(zh ? "已发给 Argus，回复在对话里" : "Sent to Argus; the reply is in the conversation");
    }
    const observe: DispatchObserver = (result) => {
      if (result.type === 'task') hasTask = true;
      if (alive.current && result.type === 'settled') {
        if (aboutCard) quietUntil.current = 0;
        // A card message that made a task, or got its reply, keeps the canvas
        // in view; anything else (a plain reply, a failure, a cancel) opens
        // the conversation, where it is shown.
        if (aboutCard && hasTask) setConversationNotice(zh ? "已交给团队，新任务出现在画布上" : "Handed to the team; the new task is on the canvas");
        else if (aboutCard && result.outcome === 'message') setConversationNotice(zh ? "Argus 已回复" : "Argus replied");
        else if (aboutCard || (result.outcome === 'message' && !hasTask)) { setConversationNotice(""); setConversationOpen(true); setAgentsOpen(false); }
      }
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
  const cancelFromMap = () => {
    setFlight((current) => current ? { ...current, result: { type: 'settled', outcome: 'cancelled' } } : null);
    composer.onCancel();
  };
  // The opening fit waits for the saved explanations (their relations place
  // cards) and a remembered view for its history, but not for long: on a busy
  // server those reads can take seconds, and an invisible canvas reads as an
  // empty map. After OPENING_WAIT_MS the cards show where they stand; the
  // scene refits by itself when the late data moves them.
  const [waited, setWaited] = useState(false);
  useEffect(() => {
    if (!nodesReady || waited) return;
    const timer = setTimeout(() => setWaited(true), OPENING_WAIT_MS);
    return () => clearTimeout(timer);
  }, [nodesReady, waited]);
  useEffect(() => {
    if (!nodesReady || initialFit.current || (!copyReady && !waited)) return;
    if (savedView.current?.camera && data.history_loading && !waited) return;
    const frame = requestAnimationFrame(() => {
      initialFit.current = true;
      if (savedView.current?.camera) camera.restore(savedView.current.camera);
      else {
        camera.fit();
        // A phone opens on the current task's summary card, not inside it: the
        // card and the status line above it say where things stand, and one
        // tap opens the internals.
        if (data.kind === "live" && canvasRef.current!.clientWidth < 640) {
          const task = currentTask(data.tasks);
          if (task) camera.fit(new Set([task.id]));
        }
      }
      setFitted(true);
    });
    return () => cancelAnimationFrame(frame);
  }, [nodesReady, camera.fit, camera.enter, camera.restore, data.kind, data.tasks, scene.cards, data.history_loading, copyReady, waited]);
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
      c.onChange(referenceText(ref) + c.value);
      window.setTimeout(
        () =>
          document
            .querySelector<HTMLTextAreaElement>(".map-composer textarea")
            ?.focus(),
        0,
      );
    },
    [readOnly],
  );
  const [menu, setMenu] = useState<{
    ref: CardReference;
    x: number;
    y: number;
  } | null>(null);
  const noteClient = useQueryClient();
  const notesQ = useQuery({
    queryKey: ["map-notes", sessionId],
    queryFn: ({ signal }) => api.mapNotes(sessionId, signal),
    enabled: data.kind === "live" && !readOnly,
    staleTime: 60_000,
  });
  const notesScope = useMemo(
    () => ({ notes: groupNotesByNode(notesQ.data?.notes ?? []) }),
    [notesQ.data],
  );
  const [noteEditor, setNoteEditor] = useState<{
    ref: CardReference;
    x: number;
    y: number;
  } | null>(null);
  const [noteText, setNoteText] = useState("");
  const [noteError, setNoteError] = useState(false);
  useEffect(() => {
    if (!noteEditor) return;
    const dismiss = (e: PointerEvent) => {
      if (!(e.target as Element).closest(".map-note-editor")) setNoteEditor(null);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setNoteEditor(null);
      }
    };
    window.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", key, true);
    return () => {
      window.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", key, true);
    };
  }, [noteEditor]);
  const saveNote = async () => {
    const editor = noteEditor;
    const text = noteText.trim();
    if (!editor || !text) return;
    setNoteError(false);
    try {
      await api.addMapNote(sessionId, { node_id: editor.ref.task_id, text });
      await noteClient.invalidateQueries({ queryKey: ["map-notes", sessionId] });
      setNoteEditor(null);
      setNoteText("");
    } catch {
      // Keep the editor and the draft so nothing is silently lost.
      setNoteError(true);
    }
  };
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
      `${card.task.title} ${card.task.objective ?? ""} ${copy?.cards[card.task.id]?.title || ""} ${copy?.cards[card.task.id]?.summary || ""} ${card.part > 1 ? (zh ? `续篇 ${card.part - 1}` : `Continued ${card.part - 1}`) : ""}`.toLowerCase(),
    [copy, zh],
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
  const [localReplacements, setLocalReplacements] = useState(false);
  const showReplacements = replacements?.shown ?? localReplacements;
  const toggleReplacements = replacements?.toggle ?? (() => setLocalReplacements((v) => !v));
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
  const openCard = useCallback((id: string, history = false) => {
    const card = sceneCache.current?.allCards.find((card) => card.id === id);
    if (card && !expandedMissions.has(card.task.id) && (history || id !== card.task.id)) {
      setExpandedMissions((current) => new Set([...current, card.task.id]));
      setPendingCard(id);
      return;
    }
    if (tracedTask && card) traceTask(card.task.id === tracedTask.id ? null : card.task.id);
    else camera.enter(id);
  }, [camera.enter, tracedTask, traceTask, expandedMissions]);
  const toggleHistory = useCallback((taskId: string) => {
    const expanded = expandedMissions.has(taskId);
    setExpandedMissions((current) => {
      const next = new Set(current);
      if (expanded) next.delete(taskId); else next.add(taskId);
      return next;
    });
    if (focusedNode?.data.task.id === taskId) {
      const last = sceneCache.current?.allCards.filter((card) => card.task.id === taskId).at(-1);
      setPendingCard(expanded ? taskId : last?.id ?? taskId);
    }
  }, [expandedMissions, focusedNode?.data.task.id]);
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
          toggleHistory,
          readStep: camera.readStep,
          readCopy,
          menu: showMenu,
          quote,
          source: data.id,
          readOnly,
          connectable: editable,
          live: data.kind === "live",
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
    toggleHistory,
    camera.readStep,
    readCopy,
    showMenu,
    quote,
    data.id,
    data.kind,
    readOnly,
    editable,
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
          (card) => (data.kind === "live" && !replaying) || card.ordinal <= visibleCount,
        )
        .map((card) => card.id),
    ),
    [scene.cards, visibleCount, data.kind, replaying],
  );
  const previousDisplay = useRef<MacroNode[]>([]);
  const displayNodes = useMemo(
    () => {
      const next = nodes.map<MacroNode>((n) => ({
        ...n,
        hidden: !visibleIds.has(n.id),
        // A card opened for reading scrolls and selects; it is not dragged.
        draggable: editable && !(camera.detailed && n.id === camera.focusId),
        data: {
          ...n.data,
          copy: copy ? { version: copy.version, cards: Object.fromEntries(
            [n.data.task.id, ...n.data.layout.steps.map((s) => s.id)]
              .filter((id) => copy.cards[id]).map((id) => [id, copy.cards[id]]),
          ) } : undefined,
          readerCopy: readingCopy?.nodeId === n.id ? readerSelection : undefined,
          focused: n.id === camera.focusId,
          detailed: camera.detailed && n.id === camera.focusId,
          canvasSize: camera.canvasSize,
          lit: lit.nodes.has(n.id),
          hovered: n.id === hoverId,
          arriving,
          revealing: replaying,
          phase: activePhase && ACTIVE.has(n.data.task.status) ? activePhase : undefined,
          growthDelay: growth.cards[n.id],
          dispatchState: flight?.result?.type === 'task' && flight.result.taskId === n.data.task.id && !composer.historical
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
      editable,
      camera.focusId,
      camera.detailed,
      camera.canvasSize,
      query,
      cardSearchText,
      copy,
      readingCopy,
      readerSelection,
      zh,
      growth,
      flight,
      composer.historical,
      traceTasks,
      lit,
      hoverId,
      arriving,
      replaying,
      activePhase,
    ],
  );
  const edges: Edge[] = useMemo(
    () => {
      const visibleLinks = scene.links.filter(
        (e) =>
          visibleIds.has(e.source) &&
          visibleIds.has(e.target) &&
          (e.kind !== "replacement" || showReplacements),
      );
      // Single pass over the visible links, covering all edge kinds; no
      // per-edge rescans.
      const lanes = edgeLanes(visibleLinks);
      const taskByCard = new Map(scene.cards.map((card) => [card.id, card.task]));
      const activeTargets = new Set<string>();
      if (data.kind === "live" && !paused) {
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
          className: `map-edge-${e.kind}${e.relation ? ` map-edge-relation-${e.relation}` : ""}${lit.edges.has(e.id) ? " is-lit" : ""}`,
          label: e.label ||
            (e.kind === "relation"
              ? RELATION_KINDS.find(([kind]) => kind === e.relation)?.[zh ? 1 : 2] ?? ""
              : e.kind === "replacement"
              ? (taskByCard.get(e.source)?.superseded_reason || "")
                  .replace(/\s+/g, " ")
                  .slice(0, 60) ||
                (zh ? "转入新计划" : "New plan")
              : e.kind === "dependency"
                ? zh
                  ? "依赖"
                  : "Dependency"
                : zh
                  ? "同一研究"
                  : "Related work"),
          labelStyle: {
            fontSize: 30,
            fill: e.kind === "replacement" ? "#95809f" : "#6685a4",
          },
          labelBgPadding: [12, 6] as [number, number],
          labelBgBorderRadius: 12,
          labelBgStyle: { fill: "var(--map-paper)", fillOpacity: 0.96 },
          style: {
            opacity: muted ? 0.12 : 1,
            stroke: e.cycle
              ? "#dc6648"
              : e.kind === "replacement"
                ? "#a48caf"
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
            color: e.kind === "replacement" ? "#a48caf" : "#8aa5b8",
            width: 32,
            height: 32,
          },
          ariaLabel:
            e.evidence ||
            (e.kind === "dependency"
              ? `Dependency: ${e.source} → ${e.target}`
              : `Plan replacement: ${e.source} → ${e.target_plan_id} (${e.target_count} tasks, representative ${e.target})`),
        };
      });
    },
    [
      scene.links,
      scene.frames,
      visibleIds,
      showReplacements,
      zh,
      growth,
      data.kind,
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
  // Set by ReactFlow's onInit; screen points map to the canvas through it.
  const flowInstance = useRef<Pick<ReactFlowInstance, "screenToFlowPosition" | "getZoom"> | null>(null);
  const toFlow = useCallback((point: Point) => flowInstance.current?.screenToFlowPosition(point) ?? point, []);
  const [draft, setDraft] = useState<{ id: string; position: Point; kind: "idea" | "step" } | null>(null);
  const [humanDrag, setHumanDrag] = useState<Record<string, Point>>({});
  // The size React Flow measured for each person's card. A controlled node
  // without it is remeasured from scratch whenever the canvas changes, and
  // until then React Flow knows none of its handles: no link starts from the
  // card and none to or from it is drawn (a link vanished as it was saved).
  const [humanSize, setHumanSize] = useState<Record<string, { width: number; height: number }>>({});
  const [editingCard, setEditingCard] = useState<string | null>(null);
  const [canvasMenu, setCanvasMenu] = useState<CanvasMenu | null>(null);
  const [canvasNotice, setCanvasNotice] = useState("");
  // A quiet first-visit tip for an editable canvas; dismissing it, or adding
  // a card, is remembered in this browser.
  const [canvasHintShown, setCanvasHintShown] = useState(() => {
    try { return window.localStorage.getItem(CANVAS_HINT_KEY) !== "1"; } catch { return true; }
  });
  const dismissCanvasHint = useCallback(() => {
    setCanvasHintShown(false);
    try { window.localStorage.setItem(CANVAS_HINT_KEY, "1"); } catch { /* the tip just returns next visit */ }
  }, []);
  const [dropping, setDropping] = useState(false);
  const failed = useCallback((error: unknown) => {
    setCanvasNotice((zh ? "画布没有保存：" : "The canvas was not saved: ") +
      (error instanceof Error ? error.message : String(error)));
  }, [zh]);
  const commitCard = useCallback((id: string, text: string) => {
    setEditingCard(null);
    if (draft && id === draft.id) {
      setDraft(null);
      canvasApi.addCard({ kind: draft.kind, text, position: draft.position }).catch(failed);
    } else canvasApi.editCard(id, text).catch(failed);
  }, [draft, canvasApi.addCard, canvasApi.editCard, failed]);
  const setDraftKind = useCallback((kind: "idea" | "step") =>
    setDraft((current) => current && { ...current, kind }), []);
  // A step's status follows its tasks: refetch the route when they move.
  const taskSignature = data.tasks.map((task) => `${task.id}:${task.status}`).join("|");
  const { refreshRoute } = canvasApi;
  useEffect(() => { refreshRoute(); }, [taskSignature, refreshRoute]);
  const routeSteps = useMemo(() => new Map<string, RouteStep>(
    (canvasRecord.route?.steps ?? []).map((step) => [step.id, step])), [canvasRecord.route]);
  // A replaced task folded into its replacement (map_relations.fold_cards):
  // a step's tasks still name it, and its line goes to the replacement.
  const foldedInto = useMemo(() => new Map(data.tasks.flatMap((task) =>
    (task.replaced ?? []).map((old) => [old.id, task.id] as const))), [data.tasks]);
  const taskTitle = useMemo(() => new Map([
    ...data.tasks.flatMap((task) => (task.replaced ?? []).map((old) => [old.id, old.title] as const)),
    ...data.tasks.map((task) => [task.id, task.title] as const),
  ]), [data.tasks]);
  // ``focus`` is declared further down; the card reaches it through this ref.
  const focusRef = useRef<(id: string) => void>(() => undefined);
  const openStepQuestion = useCallback((itemId: string) => {
    const card = scene.cards.filter((item) => item.task.id === itemId).at(-1);
    if (card) focusRef.current(card.id);
    else actions.onAnswer();
  }, [scene.cards, actions.onAnswer]);
  const cancelCard = useCallback((id: string) => {
    setEditingCard(null);
    if (draft && id === draft.id) setDraft(null);
  }, [draft]);
  const cardMenu = useCallback((id: string, point: { x: number; y: number }) =>
    setCanvasMenu({ type: "card", id, x: Math.min(window.innerWidth - 180, point.x), y: Math.min(window.innerHeight - 140, point.y) }), []);
  const linkMenu = useCallback((id: string, point: { x: number; y: number }) =>
    setCanvasMenu({ type: "link", id, x: Math.min(window.innerWidth - 180, point.x), y: Math.min(window.innerHeight - 220, point.y) }), []);
  const humanNodes = useMemo<HumanCardNode[]>(() => {
    const cards: Array<[HumanCard, boolean]> = canvasRecord.cards.map((card) => [card, false]);
    if (draft) cards.push([{ id: draft.id, kind: draft.kind, text: "", attachment: null, created_ts: 0, updated_ts: 0 }, true]);
    return cards.map(([card, isDraft]) => {
      const frame = humanFrame(card);
      return {
        id: card.id,
        type: "human" as const,
        position: humanDrag[card.id] ?? (isDraft ? draft!.position : canvasRecord.positions[card.id]) ?? { x: 0, y: 0 },
        width: frame.width,
        height: frame.height,
        measured: humanSize[card.id],
        style: { width: frame.width, height: frame.height },
        draggable: editable && !isDraft,
        selectable: false,
        zIndex: 2,
        data: {
          card, zh, draft: isDraft, readOnly: !editable, commit: commitCard, cancel: cancelCard,
          openFile: actions.onOpenArtifact, menu: cardMenu, editing: editingCard === card.id,
          ...(card.kind === "step" && !isDraft ? {
            step: routeSteps.get(card.id),
            taskTitles: (routeSteps.get(card.id)?.task_ids ?? []).map((id) => taskTitle.get(id) ?? id),
            openQuestion: openStepQuestion,
          } : {}),
          ...(isDraft ? { setDraftKind } : {}),
        },
      };
    });
  }, [canvasRecord, draft, humanDrag, humanSize, editable, zh, commitCard, cancelCard, actions.onOpenArtifact, cardMenu, editingCard,
    routeSteps, taskTitle, openStepQuestion, setDraftKind]);
  const flowNodes = useMemo(() => humanNodes.length ? [...displayNodes, ...humanNodes] : displayNodes,
    [displayNodes, humanNodes]);
  const humanEdges = useMemo<HumanLinkEdge[]>(() => {
    const humanBoxes = new Map(humanNodes.map((n) => [n.id, { ...n.position, width: n.width!, height: n.height! }]));
    const box = (id: string) => humanBoxes.get(id) ??
      (visibleIds.has(id) && positions[id] && scene.frames[id] ? { ...positions[id], ...scene.frames[id] } : undefined);
    // What the records state between an Argus card and a card a person
    // placed: a task that came from an idea card, a question about one.
    const recorded = data.tasks.flatMap((task) => (task.relations ?? []).flatMap((relation) => {
      if (!isHumanCardId(relation.target)) return [];
      const a = box(relation.target);
      const b = box(task.id);
      if (!a || !b) return [];
      const name = RELATION_KINDS.find(([kind]) => kind === relation.kind);
      return [{
        id: JSON.stringify(["recorded", relation.kind, relation.target, task.id]),
        source: relation.target,
        target: task.id,
        ...relationPorts(a, b),
        type: "human" as const,
        zIndex: 1,
        data: { kind: "related" as LinkKind, zh, recorded: true, label: name ? (zh ? name[1] : name[2]) : relation.kind },
        markerEnd: { type: MarkerType.ArrowClosed, color: "rgb(var(--ink-faint))", width: 12, height: 12 },
        ariaLabel: `${relation.kind}: ${relation.target} → ${task.id}`,
      }];
    }));
    // A quiet silver "执行" line from a required step to each task fulfilling it.
    // and from a step to the question it raised with the person (rule 2: a
    // question links to the card it is about).
    const fulfilling = [...routeSteps.values()].flatMap((step) => [
      ...step.task_ids.map((taskId) => [taskId, zh ? "执行" : "Carries out"] as const),
      ...(step.question_item_id ? [[step.question_item_id, zh ? "问你" : "Asks you"] as const] : []),
    ].flatMap(([id, label]) => {
      const taskId = foldedInto.get(id) ?? id;
      const card = scene.cards.filter((item) => item.task.id === taskId).at(-1);
      const a = box(step.id);
      const b = card && box(card.id);
      if (!card || !a || !b) return [];
      return [{
        id: JSON.stringify(["fulfils", step.id, card.id]),
        source: step.id,
        target: card.id,
        ...relationPorts(a, b),
        type: "human" as const,
        zIndex: 1,
        data: { kind: "related" as LinkKind, zh, recorded: true, label },
        markerEnd: { type: MarkerType.ArrowClosed, color: "var(--map-silver)", width: 12, height: 12 },
        ariaLabel: `${label}: ${step.id} → ${card.id}`,
      }];
    }));
    // Two tasks of a step folded into one card draw one line to it.
    const lines = [...new Map(fulfilling.map((edge) => [edge.id, edge])).values()];
    return [...recorded, ...lines, ...canvasRecord.links.flatMap((link) => {
      const a = box(link.source);
      const b = box(link.target);
      if (!a || !b) return [];
      const path = link.kind === "order" && routeSteps.has(link.source) && routeSteps.has(link.target);
      return [{
        id: link.id,
        source: link.source,
        target: link.target,
        ...relationPorts(a, b),
        type: "human" as const,
        zIndex: 1,
        data: { kind: link.kind, zh, menu: editable ? linkMenu : undefined, path },
        markerEnd: { type: MarkerType.ArrowClosed, color: "var(--map-accent)", width: path ? 7 : 9, height: path ? 7 : 9 },
        ariaLabel: `${link.kind}: ${link.source} → ${link.target}`,
      }];
    })];
  }, [data.tasks, canvasRecord.links, humanNodes, visibleIds, positions, scene.frames, scene.cards, zh, editable, linkMenu, routeSteps, foldedInto]);
  const flowEdges = useMemo<Edge[]>(() => humanEdges.length ? [...edges, ...humanEdges] : edges, [edges, humanEdges]);
  const onFlowNodesChange = useCallback((changes: NodeChange[]) => {
    const theirs: NodeChange<MacroNode>[] = [];
    const moved: Record<string, Point> = {};
    const sized: Record<string, { width: number; height: number }> = {};
    for (const change of changes) {
      if ("id" in change && isCanvasNodeId(change.id)) {
        if (change.type === "position" && change.position) moved[change.id] = change.position;
        if (change.type === "dimensions" && change.dimensions) sized[change.id] = change.dimensions;
      } else theirs.push(change as NodeChange<MacroNode>);
    }
    if (Object.keys(moved).length) setHumanDrag((current) => ({ ...current, ...moved }));
    if (Object.keys(sized).length) setHumanSize((current) => Object.entries(sized).every(([id, size]) =>
      current[id]?.width === size.width && current[id]?.height === size.height) ? current : { ...current, ...sized });
    if (theirs.length) onNodesChange(theirs);
  }, [onNodesChange]);
  const onNodeDragStop = useCallback((_: unknown, _node: unknown, dragged: Array<{ id: string; position: Point }>) => {
    const placed: Record<string, Point> = {};
    for (const node of dragged)
      if (!node.id.startsWith("draft-")) placed[node.id] = { x: Math.round(node.position.x), y: Math.round(node.position.y) };
    if (!Object.keys(placed).length) return;
    canvasApi.move(placed).catch(failed);
    setHumanDrag((current) => {
      const next = { ...current };
      for (const id of Object.keys(placed)) delete next[id];
      return next;
    });
  }, [canvasApi.move, failed]);
  const [linking, setLinking] = useState(false);
  // Esc while drawing drops the line; React Flow still reports its end.
  const linkCancelled = useRef(false);
  const linkCandidates = useMemo(() => flowNodes.flatMap((n) => n.hidden ? [] : [{ id: n.id, kind: n.type ?? "",
    x: n.position.x, y: n.position.y, width: n.width ?? n.measured?.width ?? 0, height: n.height ?? n.measured?.height ?? 0 }]),
  [flowNodes]);
  /** The card a line from ``source`` would link to if dropped at this screen
   * point. Task cards take no links, so React Flow rarely names the target:
   * the point does (canvas.ts resolveLinkTarget). */
  const linkTargetAt = useCallback((source: string, point: Point, named?: string | null) => {
    const under = typeof document.elementsFromPoint === "function"
      ? document.elementsFromPoint(point.x, point.y)
      : [document.elementFromPoint(point.x, point.y)];
    // Only a drop on the canvas itself links: one over a panel, the rail or
    // past the canvas edge never reaches a card hidden behind it.
    if (!under[0]?.closest(".react-flow__renderer")) return undefined;
    const stack = [named, ...under.map((el) => el?.closest(".react-flow__node")?.getAttribute("data-id"))];
    return resolveLinkTarget(source, stack, toFlow(point), linkCandidates);
  }, [toFlow, linkCandidates]);
  const onConnectStart = useCallback(() => {
    linkCancelled.current = false;
    setLinking(true);
    camera.hold();
  }, [camera.hold]);
  const cancelLink = useCallback(() => { linkCancelled.current = true; setLinking(false); }, []);
  const onConnectEnd = useCallback((event: MouseEvent | TouchEvent, state: FinalConnectionState) => {
    setLinking(false);
    if (linkCancelled.current) return;
    const point = "changedTouches" in event ? event.changedTouches[0] : event;
    const source = state.fromNode?.id;
    if (!source || !point) return;
    const target = linkTargetAt(source, { x: point.clientX, y: point.clientY }, state.toNode?.id);
    if (!target) return;
    setCanvasMenu({ type: "connect", source, target,
      x: Math.min(window.innerWidth - 180, point.clientX), y: Math.min(window.innerHeight - 220, point.clientY) });
  }, [linkTargetAt]);
  const addIdeaAt = useCallback((event: React.MouseEvent) => {
    if (!editable || draft || !(event.target as Element).closest(".react-flow__pane")) return;
    camera.hold();
    const frame = humanFrame({ kind: "idea" });
    // A card stays out from under the toolbar, legend and composer: it opens
    // where it was asked for, pulled into the clear part of the canvas.
    const zoom = flowInstance.current?.getZoom() ?? 1;
    const area = canvasRef.current ? openCanvasArea(canvasRef.current) : null;
    const at = toFlow(area ? clearOfChrome({ x: event.clientX, y: event.clientY },
      { width: frame.width * zoom + 24, height: frame.height * zoom + 24 }, area) : { x: event.clientX, y: event.clientY });
    setDraft({ id: `draft-${Date.now()}`, kind: "idea", position: { x: Math.round(at.x - frame.width / 2), y: Math.round(at.y - frame.height / 2) } });
    dismissCanvasHint();
  }, [editable, draft, toFlow, dismissCanvasHint, camera.hold]);
  const fileDrag = (event: React.DragEvent) => editable && event.dataTransfer.types.includes("Files") &&
    !!(event.target as Element).closest(".react-flow");
  const dropFiles = useCallback(async (files: File[], at: Point, onto?: string) => {
    if (!files.length) return;
    setCanvasNotice(zh ? `正在上传 ${files.map((f) => f.name).join("、")}…` : `Uploading ${files.map((f) => f.name).join(", ")}…`);
    try {
      const { attachments } = await api.uploadAttachments(sessionId, files);
      const anchor = onto ? flowNodes.find((n) => n.id === onto) : undefined;
      const frame = humanFrame({ kind: "file" });
      for (const [index, file] of attachments.entries()) {
        const position = anchor
          ? { x: Math.round(anchor.position.x + (anchor.width ?? 0) + 160), y: Math.round(anchor.position.y + index * (frame.height + 60)) }
          : { x: Math.round(at.x - frame.width / 2 + index * 60), y: Math.round(at.y - frame.height / 2 + index * (frame.height + 60)) };
        const { card } = await canvasApi.addCard({ kind: "file", position, attachment: {
          attachment_id: file.attachment_id, name: file.original_name, relative_path: file.relative_path,
          mime: file.mime, size_bytes: file.size_bytes,
        } });
        if (onto) await canvasApi.addLink(card.id, onto, "basis");
      }
      setCanvasNotice("");
    } catch (error) {
      failed(error);
    }
  }, [zh, sessionId, flowNodes, canvasApi.addCard, canvasApi.addLink, failed]);
  const onFileDrop = useCallback((event: React.DragEvent) => {
    setDropping(false);
    if (!fileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    const onto = (event.target as Element).closest(".react-flow__node")?.getAttribute("data-id") ?? undefined;
    const at = toFlow({ x: event.clientX, y: event.clientY });
    void dropFiles([...event.dataTransfer.files], at, onto?.startsWith("draft-") ? undefined : onto);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toFlow, dropFiles, editable]);
  useEffect(() => {
    if (!canvasMenu) return;
    const dismiss = (e: PointerEvent) => {
      if (!(e.target as Element).closest(".canvas-menu")) setCanvasMenu(null);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); setCanvasMenu(null); }
    };
    window.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", key, true);
    return () => {
      window.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", key, true);
    };
  }, [canvasMenu]);
  const menuLink = canvasMenu?.type === "link" ? canvasRecord.links.find((l) => l.id === canvasMenu.id) : undefined;
  const menuCard = canvasMenu?.type === "card" ? canvasRecord.cards.find((c) => c.id === canvasMenu.id) : undefined;
  // A card a person placed, quoted like any map card: the server reads it
  // from the canvas and links the turn (and a task it starts) back to it.
  const humanReference = (card: HumanCard): CardReference => ({
    source: data.id,
    task_id: card.id,
    task_title: (card.kind === "file" ? card.attachment?.name || card.text : card.text).replace(/\s+/g, " ").slice(0, 80),
    lang: zh ? "zh" : "en",
    event_ids: [],
  });
  const askAboutCard = (card: HumanCard) => {
    setCanvasMenu(null);
    quote(humanReference(card));
  };
  // "交给团队" is a message to the Manager, who judges it as usual. It goes
  // at once unless the composer holds the person's own draft or keeps the
  // answer in chat; then it waits in the composer for them to send.
  const handToTeam = (card: HumanCard) => {
    setCanvasMenu(null);
    const what = card.kind === "file"
      ? [card.attachment?.name, card.text].filter(Boolean).join(zh ? "——" : " — ")
      : card.text;
    const message = referenceText(humanReference(card)) + (zh ? "把这张卡做成任务：" : "Make this card a task: ") + what;
    const c = composerRef.current;
    if (c.value.trim() || c.attachments.length || c.pending || c.routeOverride === "chat") {
      if (c.routeOverride === "chat") c.onRouteOverrideChange?.("auto");
      c.onChange(c.value.trim() ? `${message}\n${c.value}` : message);
      window.setTimeout(() => document.querySelector<HTMLTextAreaElement>(".map-composer textarea")?.focus(), 0);
      return;
    }
    void submitFromMap(message).catch(() => setCanvasNotice(zh ? "没有发出去，请再试一次。" : "It was not sent; try again."));
  };
  const chooseKind = (kind: LinkKind) => {
    const current = canvasMenu;
    setCanvasMenu(null);
    if (current?.type === "connect") canvasApi.addLink(current.source, current.target, kind).catch(failed);
    else if (current?.type === "link") canvasApi.setLinkKind(current.id, kind).catch(failed);
  };
  const replacementCount = graph.links.filter(
    (e) => e.kind === "replacement",
  ).length;
  const cyclic = graph.links.some((link) => link.cycle);
  const counted = useMemo(() => data.tasks.filter((task) => !COUNTED_OUT.has(task.status)), [data.tasks]);
  // One pass, one bucket per task: the strip must partition, not double-count
  // a failed task that also carries a question.
  const tally = useMemo(() => {
    const ended = new Set(scene.cards.filter((card) => card.completionScope).map((card) => card.task.id));
    const buckets = { done: 0, ended: 0, running: 0, question: 0, review_unavailable: 0, held: 0, failed: 0, other: 0 };
    for (const task of counted) {
      if (ended.has(task.id)) buckets.ended++;
      else if (task.status === "done") buckets.done++;
      else if (ACTIVE.has(task.status)) buckets.running++;
      else if (task.pending_question) buckets.question++;
      else if (statusKey(task) === "review_unavailable") buckets.review_unavailable++;
      else if (statusKey(task) === "held") buckets.held++;
      else if (statusKey(task) === "failed") buckets.failed++;
      else buckets.other++;
    }
    return buckets;
  }, [counted, scene.cards]);
  const complete = tally.done;
  // The writer's reader-language account of a task's outcome, when cached.
  const writtenOutcome = useCallback((task: MapTask) => copy?.cards?.[`${task.id}:outcome`]?.summary || "", [copy]);
  const status = currentWorkStatus(snapshot, snapshot.mission_view, events);
  const currentWork = workSummary(status, snapshot.mission_view, events, zh ? 'zh-CN' : 'en', actions.connected,
    currentWorkStartedAt(snapshot, snapshot.mission_view, status.taskId));
  const decisionCards = useMemo(() => operatorDecisionCards(snapshot.pending_questions ?? [],
    snapshot.backlog.map((item) => ({ ...item })), currentTaskId), [snapshot.pending_questions, snapshot.backlog, currentTaskId]);
  const launchScope = useMemo(() => ({ itemId: data.kind === "live" ? snapshot.launch_hold?.item_id : undefined,
    start: actions.onStartLaunch }), [data.kind, snapshot.launch_hold?.item_id, actions.onStartLaunch]);
  const overview = useMemo(() => projectOverview({
    goal: snapshot.goal, tasks: data.tasks, events: data.events, receipts: actions.deliveries ?? NO_RECEIPTS,
    decisions: decisionCards, launchHold: snapshot.launch_hold, zh,
  }), [snapshot.goal, data.tasks, data.events, actions.deliveries, decisionCards, snapshot.launch_hold, zh]);
  const statusSentence = mapStatusSentence({
    total: counted.length,
    qa: counted.filter(task => task.turn_kind === 'qa').length,
    complete,
    ended: tally.ended,
    reviewUnavailable: tally.review_unavailable,
    held: tally.held,
    running: tally.running,
    pending: composer.pending,
    paused,
    hasOpenWork: data.tasks.some((task) => ["running", "pending", "paused", "held", "question"].includes(statusKey(task))),
    role: activePhase,
    current: data.kind === 'live' && !['idle', 'step_finished'].includes(status.state) ? currentWork : undefined,
    zh,
  });
  const focus = (id: string) => {
    setTraceId(null);
    setFocusFeedback("");
    setVisibleCount((c) =>
      Math.max(c, scene.cards.find((card) => card.id === id)?.ordinal || 1),
    );
    camera.enter(id);
  };
  focusRef.current = focus;
  const openFinalReview = () => {
    const card = scene.cards.filter(item => item.task.id === finalReview?.id).at(-1);
    if (card) {
      setReadingCopy(null);
      focus(card.id);
    }
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
  const locateAttention = () => {
    const target = attention[(attentionIndex + 1) % attention.length];
    if (target)
      focus(
        scene.cards.filter((card) => card.task.id === target.id).at(-1)!.id,
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
    () => ({ artifacts: actions.artifacts, onOpenArtifact: actions.onOpenArtifact }),
    [actions.artifacts, actions.onOpenArtifact],
  );
  // One reveal strip, rendered in the layout for datasets and floating over
  // the canvas during a live replay, so the canvas never resizes and the
  // overview camera stays where the reader left it.
  const playbackStrip = (
    <div className="map-playback" data-floating={data.kind === "live"}>
      <button
        aria-label={playing ? "Pause reveal" : "Play reveal"}
        onClick={() => {
          if (visibleCount >= graph.tasks.length) setVisibleCount(1);
          setPlaying((v) => !v);
        }}
      >
        {playing ? <Pause size={14} /> : <Play size={14} />}
      </button>
      <button
        aria-label="Restart reveal"
        onClick={() => {
          setPlaying(false);
          setVisibleCount(1);
          if (data.kind !== "live") camera.back();
        }}
      >
        <RotateCcw size={13} />
      </button>
      <span>{zh ? "逐卡展开" : "Reveal cards"}</span>
      <input
        aria-label="Visible task count"
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
        aria-label="Reveal next card"
        disabled={visibleCount >= graph.tasks.length}
        onClick={() =>
          setVisibleCount((c) => Math.min(graph.tasks.length, c + 1))
        }
      >
        <ChevronRight size={14} />
      </button>
      <small>{zh ? "时间顺序" : "Chronological order"}</small>
      {data.kind === "live" && (
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
    <MapNotesContext.Provider value={notesScope}>
    <MapArtifactContext.Provider value={artifactScope}>
    <MapLaunchContext.Provider value={launchScope}>
      {data.kind === "live" && !replaying && (overview.goal || data.tasks.length > 0 || actions.teamStatus) && (
        <MapOverview overview={overview} zh={zh} team={actions.teamStatus} readOnly={readOnly} route={canvasRecord.route}
          onOpenReceipt={actions.onOpenReceipt} onOpenArtifact={actions.onOpenArtifact} onAnswer={actions.onAnswer} onStartLaunch={actions.onStartLaunch}
          onFocusTask={(taskId) => {
            const card = scene.cards.filter((item) => item.task.id === taskId).at(-1);
            if (card) focus(card.id);
            return !!card;
          }} />
      )}
      {/* The second header line: one sentence on where the work stands, and,
          when something waits on the reader, a link straight to it. */}
      {(statusSentence || attention.length > 0) && <div className="map-status-row">
        {/* How the tasks divide, as one thin bar ahead of the sentence that says
            it in words: the eye takes the proportion, the sentence the detail. */}
        {data.tasks.length > 0 && (
          <span className="map-progress" aria-hidden="true">
            {(["done", "ended", "running", "question", "review_unavailable", "held", "failed", "other"] as const).map((bucket) =>
              tally[bucket] > 0 ? <i key={bucket} data-bucket={bucket} style={{ flexGrow: tally[bucket] }} /> : null)}
          </span>
        )}
        <p className="map-status-line" role="status">
          {(composer.pending || (!paused && activePhase)) ? <i className="map-live-dot" aria-hidden /> : null}
          <span className="map-status-text">{statusSentence}</span>
          {attention.length > 0 && (
            <>
              <span className="map-status-sep" aria-hidden>·</span>
              <button
                type="button"
                className="map-attention-link"
                onClick={locateAttention}
                title={zh ? "跳到需要你处理的任务" : "Jump to the task waiting on you"}
                aria-label={zh ? `逐个查看 ${attention.length} 项需要你处理的任务` : `Cycle through ${attention.length} tasks needing attention`}
              >
                {zh
                  ? `${attention.length} 项需要你处理`
                  : `${attention.length} ${attention.length === 1 ? "needs" : "need"} your attention`}
              </button>
            </>
          )}
        </p>
      </div>}
      {data.kind === "live" && finalReview && !replaying && (
        <div className="map-final-review" role="status">
          <span>
            <strong>{zh ? "最终结果已检查通过" : "Final delivery passed review"}</strong>
            {focusedNode?.data.task.id !== finalReview.id
              && focusedNode?.data.task.outcome?.review_status === "unavailable"
              && (finalReview.finished_ts ?? 0) > (focusedNode.data.task.finished_ts ?? 0)
              ? <span>{zh ? " · 本项目后续已交付；此卡保留当次审查异常。" : " · This project was delivered later; this card retains its earlier review error."}</span>
              : null}
          </span>
          <button type="button" onClick={openFinalReview}>{zh ? "查看结果检查" : "View final review"}</button>
          {actions.deliveryCount > 0 && <button type="button" onClick={actions.onOpenDelivery}>
            {zh ? "查看项目成果" : "View project deliverables"}
          </button>}
        </div>
      )}
      {data.kind === 'live' && !readOnly && <PendingBanner questions={snapshot.pending_questions ?? []} backlog={snapshot.backlog}
        currentTaskId={currentTaskId ?? snapshot.mission_view?.mission.id} onAnswer={actions.onAnswer} onLocate={(taskId) => {
          const card = scene.cards.filter((item) => item.task.id === taskId).at(-1);
          if (card) focus(card.id);
        }} />}
      {camera.detailed && attentionIndex >= 0 && (
        <div className="map-attention-detail" role="status">
          <strong>{zh ? "待处理" : "Needs attention"} {attentionIndex + 1} / {attention.length}</strong>
          {(() => {
            const summary = attentionSummary(attention[attentionIndex], data.events, zh, writtenOutcome(attention[attentionIndex]));
            return <div tabIndex={0}>
              <p>{summary.reason}</p>
              <p>{summary.next}</p>
              {summary.detail && (summary.showDetail
                ? <blockquote className="map-attention-quote"><strong>{zh ? "暂停原因原文" : "Recorded reason"}</strong>{" "}{summary.detail}</blockquote>
                : <details><summary>{zh ? "原始记录" : "Recorded words"}</summary><p>{summary.detail}</p></details>)}
            </div>;
          })()}
        </div>
      )}
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
          data-linking={linking || undefined}
          data-hovering={!!hoverId && !camera.detailed}
          onDoubleClick={addIdeaAt}
          onDragOver={(e) => {
            if (!fileDrag(e)) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";
            setDropping(true);
          }}
          onDragLeave={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as globalThis.Node | null)) setDropping(false);
          }}
          onDrop={onFileDrop}
        >
          {dropping && <div className="canvas-drop-hint" aria-hidden />}
          {canvasNotice && (
            <div className="canvas-upload-status" role="status" onClick={() => setCanvasNotice("")}>{canvasNotice}</div>
          )}
          {conversationNotice && !conversationOpen && !canvasNotice && (
            <div className="canvas-upload-status canvas-conversation-notice nodrag nopan" role="status">
              <span>{conversationNotice}</span>
              <button type="button" onClick={() => { setConversationNotice(""); setConversationOpen(true); setAgentsOpen(false); }}>{zh ? "查看对话" : "Open conversation"}</button>
              <button type="button" aria-label={zh ? "关闭" : "Dismiss"} onClick={() => setConversationNotice("")}>×</button>
            </div>
          )}
          {canvasHintShown && editable && graph.tasks.length > 0 && (
            <div className="canvas-hint nodrag nopan" role="note">
              <span>{zh ? "双击空白处加想法卡 · 把文件拖进来 · 从卡片边缘拉线" : "Double-click empty space to add an idea card · Drop files here · Drag from a card edge to link"}</span>
              <button type="button" aria-label={zh ? "知道了，不再提示" : "Got it, hide this tip"} title={zh ? "知道了，不再提示" : "Got it, hide this tip"} onClick={dismissCanvasHint}>×</button>
            </div>
          )}
          {conversationOpen && <MapConversation events={actions.conversationEvents} connected={actions.connected} pending={composer.pending} artifacts={actions.artifacts} zh={zh} onClose={() => setConversationOpen(false)} onOpenArtifact={actions.onOpenArtifact} onOpenDelivery={actions.onOpenReceipt} />}
          {agentsOpen && data.kind === 'live' && <aside className="map-agent-drawer nowheel nodrag nopan">
            <AgentActivity sid={snapshot.session.id} view={snapshot.mission_view} roles={snapshot.roles} events={events}
              taskId={focusedNode?.data.task.id || snapshot.mission_view?.mission.id || undefined}
              paused={paused && !composer.pending} onClose={() => setAgentsOpen(false)} />
          </aside>}
          {flight && !readOnly && <MapDispatchMotion flight={flight} canvas={canvasRef} zh={zh} historical={composer.historical}
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
              aria-label="Fit map"
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
            {data.kind === "live" && graph.tasks.length > 1 && !camera.detailed && !replaying && (
              <button
                onClick={() => {
                  // The overview stays where it is; the map fills back in.
                  setReplaying(true);
                  setVisibleCount(1);
                  setPlaying(true);
                }}
                title={zh ? "回放这项研究是如何展开的" : "Replay how this research unfolded"}
                aria-label="Replay"
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
                aria-label="Return to map"
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
                    openCard(focusedNode.data.previousId, true)
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
                    openCard(focusedNode.data.nextId, true)
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
            {data.kind === 'live' && (
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
                  title={zh ? '各个 Agent 正在做什么' : 'What each agent is doing'}
                  onClick={() => { setAgentsOpen((open) => !open); setConversationOpen(false); }}
                >
                  <Activity size={15} /><i data-active={!!activePhase || composer.pending} aria-hidden /><span>{zh ? '工作详情' : 'Work details'}</span>
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
            <ReactFlow<MacroNode | HumanCardNode>
              nodes={flowNodes}
              edges={flowEdges}
              nodeTypes={NODE_TYPES}
              edgeTypes={EDGE_TYPES}
              onNodesChange={onFlowNodesChange}
              onNodeDragStop={onNodeDragStop}
              onInit={(instance) => { flowInstance.current = instance; }}
              onConnectStart={onConnectStart}
              onConnectEnd={onConnectEnd}
              connectionMode={ConnectionMode.Loose}
              connectOnClick={false}
              // A link is drawn between cards on screen. Panning while the
              // pointer is near the canvas edge (where cards under the toolbar
              // or composer sit) slid the target away before the drop, and the
              // line landed on empty canvas.
              autoPanOnConnect={false}
              nodeDragThreshold={4}
              onNodeMouseEnter={(_, node) => setHoverId(node.id)}
              onNodeMouseLeave={() => setHoverId(null)}
              onMove={camera.onMove}
              defaultViewport={INITIAL_VIEWPORT}
              minZoom={0.01}
              maxZoom={3.5}
              nodesDraggable={editable}
              nodesFocusable={false}
              nodesConnectable={editable}
              edgesReconnectable={false}
              zoomOnScroll={false}
              zoomOnPinch
              zoomOnDoubleClick={false}
              deleteKeyCode={null}
              selectionKeyCode={null}
              onlyRenderVisibleElements={fitted && nodesReady}
              proOptions={{ hideAttribution: true }}
            >
              {editable && <LinkGuide targetAt={linkTargetAt} boxes={linkCandidates} onCancel={cancelLink} />}
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
              <MiniMap<MacroNode | HumanCardNode>
                nodeColor={(n) =>
                  n.type === "human" ? "var(--map-accent)" :
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
          {data.kind === "live" && replaying && playbackStrip}
          <div className="map-legend nowheel">
            <span title={zh ? "内容相关的工作" : "Related in content"}>
              <b className="dashed" />
              {zh ? "内容关联" : "Related work"}
            </span>
            {RELATION_KINDS.filter(([kind]) => relationKinds.has(kind)).map(([kind, zhName, enName]) => (
              <span key={kind} title={RELATION_HINTS[kind]?.[zh ? 0 : 1] ?? (zh ? "记录里写明的关系" : "A relation the records state")}>
                <b className={`relation relation-${kind}`} />
                {zh ? zhName : enName}
              </span>
            ))}
            {humanEdges.some((edge) => !edge.data?.recorded) && (
              <span title={zh ? "你在画布上拉的线" : "Lines you drew on the canvas"}>
                <b className="human-link" />
                {zh ? "你画的线" : "Your links"}
              </span>
            )}
            {humanEdges.some((edge) => edge.data?.recorded) && (
              <span data-testid="legend-recorded-human"
                title={zh ? "虚线连到你放的卡片，是记录里写明的关系：来自 = 这项任务由这张卡交给团队后生成；关于 = 对这张卡的提问和回答"
                  : "Dashed lines to your cards are relations the records state: Came from = the task was made from this card when handed to the team; About = a question about this card"}>
                <b className="human-link is-recorded" />
                {zh ? "来自 / 关于你的卡片" : "From / about your cards"}
              </span>
            )}
            <span title={zh ? "没有记录关系的卡片按时间先后相连" : "Cards with no recorded relation are joined in time order"}>
              <b className="order" />
              {zh ? "先后" : "Order"}
            </span>
            <span>
              <b />
              {zh ? "任务依赖" : "Dependency"}
            </span>
            <button
              className={!showReplacements ? "is-muted" : ""}
              onClick={toggleReplacements}
              aria-pressed={showReplacements}
              disabled={replacementCount === 0}
              aria-label="Toggle plan replacements"
            >
              <b className="replacement" />
              {zh ? "计划替代" : "Plan changes"}
              {replacementCount ? ` · ${replacementCount}` : ""}
            </button>
          </div>
          {!readOnly && !!looseRequests.length && !camera.detailed && (
            <div className="map-loose-requests" data-testid="map-loose-requests" aria-live="polite">
              {looseRequests.map((request) => (
                <span key={request.id} title={request.full || request.text}>
                  <i aria-hidden />{zh ? "你说：" : "You said: "}{request.text}
                </span>
              ))}
            </div>
          )}
          {!readOnly && (
            <MapComposer {...composer} pendingLabel={pendingLabel} progressVisible={!conversationOpen}
              dispatchStatus={flight?.result?.type === 'task' ? flight.landed || composer.historical ? 'task' : 'launching' : flight?.result?.outcome}
              onSend={submitFromMap} onCancel={cancelFromMap} overview={!camera.detailed} startCompact={immersive} />
          )}
          {canvasMenu && editable && (
            <div className="map-context-menu canvas-menu" role="menu" style={{ left: canvasMenu.x, top: canvasMenu.y }}>
              {canvasMenu.type === "card" && menuCard && (
                <>
                  {(menuCard.kind === "file" || menuCard.text.trim()) && (
                    <button role="menuitem" onClick={() => handToTeam(menuCard)}>
                      {zh ? "交给团队" : "Hand to the team"}
                    </button>
                  )}
                  <button role="menuitem" onClick={() => askAboutCard(menuCard)}>
                    {zh ? "问这张卡" : "Ask about this card"}
                  </button>
                  {menuCard.kind !== "file" && (
                    <button role="menuitem" onClick={() => { setEditingCard(menuCard.id); setCanvasMenu(null); }}>
                      {zh ? "编辑文字" : "Edit text"}
                    </button>
                  )}
                  {menuCard.kind === "idea" && menuCard.text.trim() && (
                    <button role="menuitem" data-testid="canvas-make-step"
                      onClick={() => { setCanvasMenu(null); canvasApi.patchCard(menuCard.id, { kind: "step" }).catch(failed); }}>
                      {zh ? "设为必经步骤" : "Make it a required step"}
                    </button>
                  )}
                  {menuCard.kind === "step" && (
                    <>
                      <button role="menuitemcheckbox" aria-checked={!!menuCard.checkpoint} data-testid="canvas-step-checkpoint"
                        onClick={() => { setCanvasMenu(null); canvasApi.patchCard(menuCard.id, { checkpoint: !menuCard.checkpoint }).catch(failed); }}>
                        {menuCard.checkpoint ? "✓ " : ""}{zh ? "到这步先停下来给我看" : "Pause for me after this step"}
                      </button>
                      <button role="menuitem"
                        onClick={() => { setCanvasMenu(null); canvasApi.patchCard(menuCard.id, { kind: "idea" }).catch(failed); }}>
                        {zh ? "改回想法" : "Make it an idea again"}
                      </button>
                    </>
                  )}
                  <button role="menuitem" onClick={() => { setCanvasMenu(null); canvasApi.deleteCard(menuCard.id).catch(failed); }}>
                    {zh ? "删除卡片" : "Delete card"}
                  </button>
                </>
              )}
              {(canvasMenu.type === "connect" || (canvasMenu.type === "link" && menuLink)) && (
                <>
                  <span className="canvas-menu-title">{zh ? "选一种关系" : "Choose a relation"}</span>
                  {LINK_KINDS.map(([kind, labelZh, labelEn]) => (
                    <button key={kind} role="menuitemradio" aria-checked={menuLink?.kind === kind}
                      onClick={() => chooseKind(kind)}>
                      {menuLink?.kind === kind ? "✓ " : ""}{zh ? labelZh : labelEn}
                    </button>
                  ))}
                  {canvasMenu.type === "link" && menuLink && (
                    <button role="menuitem" onClick={() => { setCanvasMenu(null); canvasApi.deleteLink(menuLink.id).catch(failed); }}>
                      {zh ? "删除连线" : "Delete link"}
                    </button>
                  )}
                </>
              )}
            </div>
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
              {data.kind === "live" && (
                <>
                  <button
                    role="menuitem"
                    onClick={() => {
                      setNoteText("");
                      setNoteEditor({ ref: menu.ref, x: menu.x, y: menu.y });
                      setMenu(null);
                    }}
                  >
                    {zh ? "添加批注" : "Add a note"}
                  </button>
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
          {noteEditor && !readOnly && (
            <div
              className="map-note-editor nodrag nopan"
              style={{
                left: Math.min(window.innerWidth - 320, noteEditor.x),
                top: Math.min(window.innerHeight - 240, noteEditor.y),
              }}
            >
              <small>
                {zh
                  ? `批注《${noteEditor.ref.task_title}》`
                  : `Note on “${noteEditor.ref.task_title}”`}
              </small>
              <textarea
                autoFocus
                maxLength={2000}
                value={noteText}
                onChange={(e) => setNoteText(e.target.value)}
                placeholder={
                  zh
                    ? "写下你的观察，Argus 在下个规划周期会读到"
                    : "Your observation; Argus reads it next planning cycle"
                }
              />
              {noteError && (
                <small className="map-note-error">
                  {zh
                    ? "没有保存上，稍后再试；草稿还在"
                    : "Not saved; try again — the draft is kept"}
                </small>
              )}
              <div className="map-note-actions">
                <button onClick={() => setNoteEditor(null)}>
                  {zh ? "取消" : "Cancel"}
                </button>
                <button
                  className="is-primary"
                  disabled={!noteText.trim()}
                  onClick={() => void saveNote()}
                >
                  {zh ? "保存" : "Save"}
                </button>
              </div>
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
      {data.kind !== "live" && playbackStrip}
      <Modal open={!!readingTask} onClose={() => setReadingCopy(null)} label={readingTask?.turn_kind === 'qa' ? (zh ? '问答' : 'Q&A') : zh ? "任务说明" : "Task explanation"}>
        {readingTask && (readingRequest || readingTask.turn_kind === 'qa') ? <>
          <ModalHeader title={readingTask.turn_kind === 'qa' ? (zh ? '问答' : 'Q&A') : zh ? "任务说明" : "Task explanation"} sub={readingTask.turn_kind === 'qa' ? readingTask.objective || readingTask.title : copy?.cards[readingTask.id]?.title || readingTask.title} />
          <div className="px-6 pb-6" data-testid="map-task-reading" data-task-id={readingTask.id}>
            {readingTask.outcome?.review_status === "unavailable" && (
              <p className="mb-3 text-sm text-ink-dim" role="status">
                {zh ? "当次审查异常，未形成审阅结论。" : "No review judgment was returned on this attempt."}
                {finalReview && (finalReview.finished_ts ?? 0) > (readingTask.finished_ts ?? 0) && (
                  <button type="button" className="ml-2 text-blue underline" onClick={openFinalReview}>
                    {zh ? "查看本项目后续通过的最终审稿" : "View this project's later accepted final review"}
                  </button>
                )}
              </p>
            )}
            {readingNode?.data.completionScope ? <p className="mb-2 text-xs text-ink-dim">{readingNode.data.completionScope}</p> : null}
            <MapReaderContent cardKey={readingTask.id} taskId={readingTask.id} card={copy?.cards[readingTask.id]} task={readingTask}
              readOnly={readOnly} sessionId={data.kind === 'live' ? sessionId : undefined} zh={zh}
              originalDetail={readingTask.turn_kind === 'qa'
                ? readingAnswer?.text || (readingTask.status === 'running' ? (zh ? '正在回答…' : 'Answering…') : readingTask.status === 'cancelled' ? (zh ? '本次回答已中断。' : 'This answer was interrupted.') : readingTask.summary || (zh ? '尚无回答。' : 'No answer recorded.'))
                : readingTask.objective || readingTask.summary || (zh ? "这项任务尚无详细记录。" : "No detailed task record is available.")}
              selection={readerSelection}
              artifacts={artifactScope.artifacts} onOpenArtifact={artifactScope.onOpenArtifact} />
            {!readOnly ? <Button className="mt-3 text-xs" onClick={() => quote({ source: data.id, task_id: readingTask.id,
              task_title: copy?.cards[readingTask.id]?.title || readingTask.title,
              event_ids: readingAnswer ? [readingAnswer.id] : copy?.cards[readingTask.id]?.event_ids || readingRequest?.event_ids || [], lang: zh ? 'zh' : 'en' })}>{readingTask.turn_kind === 'qa' ? (zh ? '引用这条问答' : 'Reference this Q&A') : zh ? "引用此任务" : "Reference this task"}</Button> : null}
          </div>
        </> : null}
      </Modal>
    </MapLaunchContext.Provider>
    </MapArtifactContext.Provider>
    </MapNotesContext.Provider>
    </ZoomStepContext.Provider>
  );
}

/** Memoized: the app re-renders on every streamed event and poll; the map
 * only needs to follow its own (throttled) props, not that firehose. */
export const MapPanel = memo(function MapPanel({
  immersive = false,
  onImmersiveChange,
  snapshot,
  currentTaskId,
  events,
  managerSteps = [],
  draft,
  onDraftChange,
  onSend,
  attachments: controlledAttachments,
  onAttachmentsChange,
  pending,
  onCancel,
  focusSignal,
  readOnly = false,
  onOpenSettings,
  routeOverride,
  onRouteOverrideChange,
  conversationEvents,
  connected,
  artifacts,
  deliveryCount,
  onOpenDelivery,
  onOpenReceipt,
  onOpenArtifact,
  onAnswer,
  deliveries,
  onStartLaunch,
  onPauseTeam,
  onResumeTeam,
  teamBusy,
}: {
  onPauseTeam?: () => void;
  onResumeTeam?: () => void;
  teamBusy?: boolean;
  snapshot: Snapshot;
  currentTaskId?: string | null;
  events: EventMsg[];
  managerSteps?: Array<{ label: string; detail?: string }>;
  draft: string;
  onDraftChange: (text: string) => void;
  onSend: MapSend;
  attachments?: File[];
  onAttachmentsChange?: (files: File[]) => void;
  pending: boolean;
  onCancel: () => void;
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
  const [source, setSource] = useState(
    () =>
      new URLSearchParams(window.location.search).get("dataset") ||
      readLocalStorage("argus.map.source.v1") ||
      "live",
  );
  const index = useQuery({
    queryKey: ["map-datasets"],
    queryFn: ({ signal }) => api.mapDatasets(signal),
    staleTime: Infinity,
  });
  const dataset = useQuery({
    queryKey: ["map-dataset", source],
    queryFn: ({ signal }) => api.mapDataset(source, signal),
    enabled: source !== "live",
    staleTime: Infinity,
    retry: false,
  });
  const client = useQueryClient();
  const selectionKey = "argus.map.history.v1:" + snapshot.session.id;
  const [selection, setSelection] = useState<MapSelection | null>(() => parseMapSelection(readLocalStorage(selectionKey)));
  const [chooseHistory, setChooseHistory] = useState(false);
  const info = useQuery({
    queryKey: ["map-info", snapshot.session.id],
    queryFn: ({ signal }) => api.mapInfo(snapshot.session.id, signal),
    enabled: source === "live", staleTime: 60000,
  });
  const choice = selection ?? (info.data && !info.data.requires_choice ? { mode: "full" as const } : null);
  useEffect(() => {
    if (!selection && info.data && !info.data.requires_choice) {
      const initial: MapSelection = { mode: "full" };
      setSelection(initial);
      writeLocalStorage(selectionKey, JSON.stringify(initial));
    }
  }, [info.data, selection, selectionKey]);
  const approved = source !== "live" || !!choice && choice.mode !== "off" && !chooseHistory;
  const liveKey = ["map-live", snapshot.session.id, choice?.mode, choice?.since, choice?.eventSince, choice?.taskId];
  const viewKey = JSON.stringify([source, snapshot.session.id, locale, choice]);
  const selectHistory = (value: MapSelection) => {
    setSelection(value);
    writeLocalStorage(selectionKey, JSON.stringify(value));
    setChooseHistory(false);
  };
  const live = useQuery({
    queryKey: liveKey,
    queryFn: async ({ signal }) => {
      const previous = client.getQueryData<Dataset>(liveKey);
      const next = choice?.mode === "full"
        ? await api.mapHistory(snapshot.session.id, signal, previous?.history_cursor, previous?.cursor)
        : await api.liveMap(snapshot.session.id, signal, previous?.cursor, choice || undefined);
      return mergeMapProgress(client.getQueryData<Dataset>(liveKey), next);
    },
    enabled: source === "live" && approved,
    staleTime: Infinity,
    gcTime: 2 * 60 * 60 * 1000,
    refetchOnMount: "always",
    refetchInterval: (query) => livePollInterval(approved, query.state.data, snapshot),
  });
  const paused = source === "live" && mapIsPaused(snapshot);
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
    if (source !== "live" || !approved || refreshTimer.current) return;
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
  }, [updateKey, source, snapshot.session.id, client, approved]);
  useEffect(
    () => () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      refreshTimer.current = null;
    },
    [source, snapshot.session.id, approved],
  );

  const data = source === "live" ? live.data : dataset.data;
  // Plan-replacement links are off by default; the legend flips them on wide
  // screens and the options menu does the same where the legend is hidden.
  const [replacementsShown, setReplacementsShown] = useState(false);
  const replacements = useMemo(
    () => ({ shown: replacementsShown, toggle: () => setReplacementsShown((v) => !v) }),
    [replacementsShown],
  );
  const replacementCount = useMemo(
    () => data ? buildMap(data.tasks).links.filter((link) => link.kind === "replacement").length : 0,
    [data],
  );
  // Stable object identities: fresh actions/composer objects on every render
  // would invalidate the node-data memos in every mounted MapCanvas card.
  // The team line lives at the top of the canvas, beside the overview, so it
  // stays in view when the composer is folded away.
  const teamStatus = useMemo(() => <WorkStatusBar snapshot={snapshot} view={snapshot.mission_view} events={events} connected={connected !== false} compact
    onPauseTeam={readOnly ? undefined : onPauseTeam} onResumeTeam={readOnly ? undefined : onResumeTeam} teamBusy={teamBusy} />,
  [snapshot, events, connected, readOnly, onPauseTeam, onResumeTeam, teamBusy]);
  const actions = useMemo<MapWorkspaceActions>(
    () => ({ conversationEvents, connected, artifacts, deliveryCount,
             onOpenDelivery, onOpenReceipt, onOpenArtifact, onAnswer, deliveries,
             onStartLaunch: readOnly ? undefined : onStartLaunch, teamStatus }),
    [conversationEvents, connected, artifacts, deliveryCount,
     onOpenDelivery, onOpenReceipt, onOpenArtifact, onAnswer, deliveries, onStartLaunch, readOnly, teamStatus],
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
      onCancel,
      focusSignal,
      sessionName: snapshot.session.display_name || t("sidebar.unnamedSession"),
      historical: source !== "live",
      zh,
    }),
    [routeOverride, onRouteOverrideChange, draft, onDraftChange, send, attachments,
     pending, onCancel, focusSignal, snapshot.session.display_name, snapshot.session.id,
     source, zh, t, snapshot.roles, snapshot.daemon.alive, snapshot.manager_requests, snapshot.last_call,
     readOnly, onOpenSettings, events, connected, onPauseTeam, onResumeTeam, teamBusy],
  );
  // What the running task is doing and each task's figure, for the cards.
  // Contexts, not node data: a poll re-renders the line and the picture only.
  const workspace = snapshot.session.workdir || snapshot.session.cwd || '';
  const cardNow = useMemo(() => {
    const status = currentWorkStatus(snapshot, snapshot.mission_view, events);
    if (!snapshot.daemon.alive || status.state !== 'running' || !status.taskId) return null;
    const since = currentWorkStartedAt(snapshot, snapshot.mission_view, status.taskId);
    return { taskId: status.taskId, ...taskNow(events, status.taskId, since, locale, workspace) };
  }, [snapshot, events, locale, workspace]);
  const cardFigures = useMemo(() => ({
    sid: snapshot.session.id,
    figures: taskFigures(deliveries ?? [], artifacts ?? [], events, workspace),
  }), [snapshot.session.id, deliveries, artifacts, events, workspace]);
  const switchSource = (value: string) => {
    setSource(value);
    writeLocalStorage("argus.map.source.v1", value);
    const url = new URL(window.location.href);
    url.searchParams.set("dataset", value);
    window.history.replaceState(null, "", url);
  };
  return (
    <MapCardNowContext.Provider value={cardNow}>
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
        <details className="map-more">
          <summary aria-label={zh ? "地图选项" : "Map options"} title={zh ? "地图选项" : "Map options"}>
            <SlidersHorizontal size={16} />
          </summary>
          <div className="map-more-menu">
            <div className="map-dataset-bar">
              <Compass size={15} />
              <select
                aria-label={zh ? "地图数据来源" : "Map data source"}
                value={source}
                onChange={(e) => switchSource(e.target.value)}
              >
                <option value="live">
                  {zh ? "当前会话" : "Current session"} ·{" "}
                  {snapshot.session.display_name || t("sidebar.unnamedSession")}
                </option>
                {!index.data?.datasets.some((d) => d.id === source) &&
                  source !== "live" && <option value={source}>{source}</option>}
                {index.data?.datasets.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.title} · {d.task_count}
                  </option>
                ))}
              </select>
              {data?.captured_at && (
                <span className="map-capture">
                  <Clock3 size={12} />
                  {new Date(data.captured_at).toLocaleDateString()}
                </span>
              )}
            </div>
            {source === "live" && info.data && (
              <button type="button" className="map-scope-button" onClick={() => setChooseHistory(true)}>
                {zh ? "加载范围" : "History range"}
              </button>
            )}
            {!readOnly && onOpenSettings && (
              <button type="button" onClick={onOpenSettings} className="map-scope-button"
                title={zh ? "打开共享模型设置。轻量模型用于前台快速回复和任务复盘，改动会影响这两处。" : "Opens shared model settings. The light model serves front-door quick replies and task reflection; a change affects both."}>
                {zh ? "轻量模型设置（前台 · 复盘）" : "Light model settings (front door · reflection)"}
              </button>
            )}
            {replacementCount > 0 && (
              <button
                type="button"
                className="map-scope-button map-narrow-only"
                aria-pressed={replacementsShown}
                onClick={replacements.toggle}
              >
                {replacementsShown
                  ? zh ? `隐藏计划替代连线 · ${replacementCount}` : `Hide plan changes · ${replacementCount}`
                  : zh ? `显示计划替代连线 · ${replacementCount}` : `Show plan changes · ${replacementCount}`}
              </button>
            )}
          </div>
        </details>
      </header>
      {source === "live" && info.data && (
        <MapHistoryChoice open={chooseHistory || !choice && info.data.requires_choice}
          info={info.data} zh={zh} onChoose={selectHistory} />
      )}
      {source === "live" && info.isError && (
        <div className="map-data-error">
          {zh ? "暂时无法检查历史记录。" : "Could not check session history."}
          <button onClick={() => void info.refetch()}>{zh ? "重试" : "Retry"}</button>
        </div>
      )}
      {approved && data?.history_loading && (
        <div className="map-history-progress" role="status">
          {zh ? "正在分批加载历史记录" : "Loading history in pages"}
          {data.history_progress && ` · ${(data.history_progress.loaded_bytes / 1024 / 1024).toFixed(1)} / ${(data.history_progress.total_bytes / 1024 / 1024).toFixed(1)} MB`}
          <button onClick={() => setChooseHistory(true)}>{zh ? "更改范围" : "Change range"}</button>
        </div>
      )}
      {approved && data && (source === "live" ? live.isError : dataset.isError) && (
        <div className="map-data-error" role="status">
          {zh ? "地图暂时无法更新。" : "The map can't update right now."}
          <button onClick={() => void (source === "live" ? live.refetch() : dataset.refetch())}>{zh ? "重试" : "Retry"}</button>
        </div>
      )}
      {index.isError && (
        <div className="map-data-error">
          {zh
            ? "历史记录列表暂时无法读取，可切换当前会话或重试。"
            : "Historical maps are unavailable. Open the current session or retry."}
          <button onClick={() => void index.refetch()}>
            {zh ? "重试" : "Retry"}
          </button>
        </div>
      )}
      {!approved ? (
        <div className="map-empty">
          <h3>{zh ? "地图尚未开启" : "Map is not enabled"}</h3>
          <p>{info.isPending ? (zh ? "正在检查历史记录规模…" : "Checking history size…") :
            (zh ? "选择加载范围后查看研究进度。" : "Choose a history range to view research progress.")}</p>
          {info.data && <button onClick={() => setChooseHistory(true)}>{zh ? "选择加载范围" : "Choose history range"}</button>}
        </div>
      ) : (source === "live" ? live.isError : dataset.isError) && !data ? (
        <div className="map-empty">
          <h3>{zh ? "地图暂时无法读取" : "Map unavailable"}</h3>
          <p>{String(source === "live" ? live.error : dataset.error)}</p>
          <button
            onClick={() =>
              void (source === "live" ? live.refetch() : dataset.refetch())
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
            currentTaskId={currentTaskId}
            events={events}
            pendingLabel={managerSteps.at(-1)?.detail || managerSteps.at(-1)?.label}
            viewKey={viewKey}
            paused={paused}
            sessionId={snapshot.session.id}
            zh={zh}
            readOnly={readOnly}
            replacements={replacements}
            immersive={immersive}
            onImmersiveChange={onImmersiveChange}
            activePhase={
              source === "live" && !paused
                ? snapshot.roles.find((r) => r.active)?.role
                : undefined
            }
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
    </MapCardNowContext.Provider>
  );
});
