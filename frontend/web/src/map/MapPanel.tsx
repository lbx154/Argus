import { MapConversation } from './MapConversation';
import { mapStatusSentence } from './status';
import { PendingBanner } from '../components/PendingBanner';
import { Activity, PackageCheck, MessageCircle, SlidersHorizontal } from 'lucide-react';
import { AgentActivity } from '../components/AgentActivity';
import { MapDispatchMotion, type MapDispatchFlight } from './MapDispatchMotion';
import type { MapSend, DispatchObserver } from './submission';
import { attentionReason, splitDraft } from './presentation';
import { useMapGrowth } from './useMapGrowth';
import { stepIdentity } from './growth';
import './motion.css';
import './alive.css';
import { ARRIVAL_WINDOW_MS, arrivalEdgeDelay, attention as spotlight, useSettledPositions } from './alive';
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  type Edge,
  type OnNodesChange,
} from "@xyflow/react";
import {
  ArrowLeft,
  Check,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Compass,
  GitBranch,
  History,
  LocateFixed,
  Maximize2,
  Pause,
  Play,
  RotateCcw,
  Search,
  X,
} from "lucide-react";
import { api, type Snapshot, type MessageRouteOverride } from "../api";
import { readLocalStorage, writeLocalStorage } from "../lib/storage";
import { useI18n } from "../i18n";
import { ACTIVE, TEAM_BRANCH_CAP, attentionTasks, buildMap, connectMap, currentTask, foldTeamBranches, formationWidths, promoteTeamBranches, statusKey, taskDependencies, type Dataset } from "./model";
import { layoutScene } from "./submap";
import { edgeLanes, layoutGraph, relationPorts } from "./graphLayout";
import { MacroTaskNode, MapArtifactContext, MapNotesContext, type MacroData, type MacroNode } from "./MacroTaskNode";
import { groupNotesByNode } from "./notes";
import "./notes.css";
import { BranchNode, BRANCH_FRAME, GROUP_FRAME, type BranchFlowNode } from "./BranchNode";
import { INITIAL_VIEWPORT, useSemanticCamera } from "./useSemanticCamera";
import { useMapCopy } from "./useMapCopy";
import { MapComposer, type MapComposerProps } from "./MapComposer";
import { referenceText, type CardReference } from "./presentation";
import type { ArtifactInfo, DeliveryReceipt, EventMsg } from "../../../core/src/types";
import "@xyflow/react/dist/style.css";
import "./map.css";
import "./submap.css";
import "./atlas.css";
import "./branch.css";
import { MapRelationEdge } from "./MapRelationEdge";
import { MapHistoryChoice } from "./MapHistoryChoice";
import { livePollInterval, mapIsPaused, mergeMapProgress, parseMapSelection, type MapSelection } from "./incremental";
import { recalledView, rememberView } from "./viewMemory";

interface MapWorkspaceActions {
  conversationEvents: EventMsg[];
  connected: boolean;
  artifacts: ArtifactInfo[];
  deliveryCount: number;
  onOpenDelivery: () => void;
  onOpenReceipt: (receipt: DeliveryReceipt) => void;
  onOpenArtifact: (path: string) => void;
  onAnswer: () => void;
}

const NODE_TYPES = { task: MacroTaskNode, branch: BranchNode };
const EDGE_TYPES = { relation: MapRelationEdge };
type AtlasNode = MacroNode | BranchFlowNode;
// Minimap fills echo the card state palette a step lighter, so the overview
// inset reads as a status heatmap instead of undifferentiated confetti.
const MINIMAP_STATUS: Record<string, string> = {
  done: "#a8cfbb",
  running: "#8fb6e4",
  question: "#e2c78e",
  failed: "#dfab97",
  paused: "#d6c6a0",
  superseded: "#c8bdd5",
  aborted: "#c3c5cb",
  skipped: "#c3c5cb",
  missing: "#c3c5cb",
};

/** The subtask tally lives in the map options menu: the cards and the folded
 * group nodes already say how the subtasks stand, so this line is for a
 * reader who wants the numbers in one place. */
export function MapTeamProgress({ events, zh }: { events: Dataset['events']; zh: boolean }) {
  const team = [...new Map(events.filter((event) => event.type === 'team.task').map((event) => [event.id, event])).values()];
  if (!team.length) return null;
  const complete = team.filter((event) => event.status === 'done').length;
  const running = team.filter((event) => ACTIVE.has(event.status || '')).length;
  const failed = team.filter((event) => event.status === 'failed').length;
  return <div className="map-team-progress" role="status" aria-label={zh ? '子任务进度' : 'Subtask progress'}>
    <strong>{zh ? '子任务' : 'Subtasks'}</strong>
    <span><Check size={12} /><b>{complete}/{team.length}</b> {zh ? '已完成' : 'completed'}</span>
    <span><b>{running}</b> {zh ? '进行中' : 'running'}</span>
    <span><b>{failed}</b> {zh ? '失败' : 'failed'}</span>
  </div>;
}

export function MapCanvas({
  data,
  zh,
  composer,
  activePhase,
  snapshot,
  events,
  pendingLabel,
  readOnly,
  sessionId,
  viewKey,
  paused,
  actions,
  replacements,
}: {
  data: Dataset;
  sessionId: string;
  zh: boolean;
  composer: MapComposerProps;
  activePhase?: string;
  snapshot: Snapshot;
  events: EventMsg[];
  pendingLabel?: string;
  readOnly: boolean;
  viewKey: string;
  paused: boolean;
  actions: MapWorkspaceActions;
  /** Whether plan-replacement links are drawn; the panel owns the switch so
   * the options menu can flip it on screens that hide the legend. */
  replacements?: { shown: boolean; toggle: () => void };
}) {
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [conversationOpen, setConversationOpen] = useState(false);
  // Folded subtask groups the reader has unfolded, by group node id.
  const [openGroups, setOpenGroups] = useState<ReadonlySet<string>>(() => new Set());
  const toggleGroup = useCallback((id: string) => {
    setOpenGroups((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);
  const [flight, setFlight] = useState<MapDispatchFlight | null>(null);
  const dispatchSerial = useRef(0);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const graph = useMemo(() => buildMap(data.tasks), [data.tasks]);
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
  const [seenCards] = useState(() => new Set(savedView.current?.scene?.cards.map((card) => card.id)));
  const focusedNode = nodes.find((n) => n.id === camera.focusId);
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
  const { copy, ready: copyReady } = useMapCopy(
    data,
    focusedNode?.data.task.id || null,
    zh,
    !readOnly && !data.history_loading,
    focusedNode?.data.layout.steps,
    sessionId,
    paused,
  );
  const links = useMemo(
    () => connectMap(graph, copy?.relations || [], zh),
    [graph, copy?.relations, zh],
  );
  const sceneCache = useRef<ReturnType<typeof layoutScene> | undefined>(savedView.current.scene);
  const scene = useMemo(() => {
    const next = layoutScene(graph, data.events, zh, sceneCache.current, links);
    sceneCache.current = replaceEqualDeep(sceneCache.current, next);
    return sceneCache.current;
  }, [graph, data.events, zh, links]);
  // Team fan-out promotion: parallel `team.task` work leaves its owning card
  // as small branch pills and returns to it, instead of hiding as steps.
  // Subtasks that share a state are then folded into one sentence-labelled
  // node, so a wide portfolio does not spray two dozen identical pills across
  // the canvas; the fold leaves room for a whole portfolio before the
  // overflow pill takes over.
  const promotedCache = useRef<ReturnType<typeof promoteTeamBranches> | null>(null);
  const promoted = useMemo(() => {
    promotedCache.current = replaceEqualDeep(
      promotedCache.current,
      foldTeamBranches(
        promoteTeamBranches(graph, data.events, zh, TEAM_BRANCH_CAP * 3),
        openGroups,
        zh,
      ),
    );
    return promotedCache.current!;
  }, [graph, data.events, zh, openGroups]);
  // Compose cards and branch pills into one geometry. Same caching discipline
  // as layoutScene: streaming prose/status changes never re-run the search.
  const atlasCache = useRef<{
    structure: string;
    positions: Record<string, { x: number; y: number }>;
  } | null>(null);
  const atlas = useMemo(() => {
    const branches = promoted.tasks.filter((task) => task.branch);
    if (!branches.length)
      return {
        links: scene.links,
        positions: scene.positions,
        frames: scene.frames,
        structure: scene.structure,
        branches,
        branchAnchor: new Map<string, string>(),
      };
    // The fan attaches to the task's last card, matching how layoutScene
    // re-anchors outgoing task links on multi-part cards.
    const anchor = new Map<string, string>();
    for (const card of scene.cards) anchor.set(card.task.id, card.id);
    const branchAnchor = new Map(
      branches.map((task) => [task.id, anchor.get(task.parent_id!) ?? task.parent_id!]),
    );
    const frames: typeof scene.frames = { ...scene.frames };
    for (const task of branches)
      frames[task.id] = { ...(task.group ? GROUP_FRAME : BRANCH_FRAME), scale: 1 };
    const atlasLinks = [
      ...scene.links,
      ...promoted.links
        .filter((link) => link.kind === "fanout" || link.kind === "fanin")
        .map((link) => ({
          ...link,
          source: anchor.get(link.source) ?? link.source,
          target: anchor.get(link.target) ?? link.target,
        })),
    ];
    // Branch pills join the layout right after their owning card, keeping the
    // rank layout's chronological frontier truthful.
    const byCard = new Map<string, string[]>();
    for (const task of branches) {
      const card = branchAnchor.get(task.id)!;
      byCard.set(card, [...(byCard.get(card) ?? []), task.id]);
    }
    const ids = scene.cards.flatMap((card) => [card.id, ...(byCard.get(card.id) ?? [])]);
    const structure = JSON.stringify([
      ids.map((id) => [id, frames[id].width, frames[id].height]),
      atlasLinks.map((link) => [link.source, link.target, link.kind]),
    ]);
    const positions =
      atlasCache.current?.structure === structure
        ? atlasCache.current.positions
        : layoutGraph(ids, atlasLinks, frames);
    atlasCache.current = { structure, positions };
    return { links: atlasLinks, positions, frames, structure, branches, branchAnchor };
  }, [scene, promoted]);
  // Cards glide to a re-laid-out place; relations follow through the store.
  const positions = useSettledPositions(atlas.positions, camera.reducedMotion);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const lit = useMemo(() => spotlight(atlas.links, hoverId), [atlas.links, hoverId]);
  // A first opening composes the map in reading order; a replay walks a live
  // session's history the way the datasets already can.
  const [arriving, setArriving] = useState(false);
  const [replaying, setReplaying] = useState(false);
  const ordinalOf = useMemo(() => new Map(scene.cards.map((card) => [card.id, card.ordinal])), [scene.cards]);
  const growth = useMapGrowth(scene, !!data.history_loading);
  const plannedWidths = useMemo(() => formationWidths(data.events), [data.events]);
  const submitFromMap: MapSend = async (text, files = []) => {
    const id = ++dispatchSerial.current;
    const source = canvasRef.current?.querySelector('.map-composer')?.getBoundingClientRect();
    setFlight({ id, text: splitDraft(text).text.replace(/\s+/g, ' ').slice(0, 180), origin: { x: source?.left ?? 20, y: source?.top ?? innerHeight - 100, width: source?.width ?? 260, height: source?.height ?? 56 } });
    let hasTask = false;
    const observe: DispatchObserver = (result) => {
      if (result.type === 'task') hasTask = true;
      if (alive.current && result.type === 'settled' && result.outcome === 'message' && !hasTask) { setConversationOpen(true); setAgentsOpen(false); }
      if (alive.current) setFlight((current) => current?.id === id ? { ...current, result } : current);
    };
    try {
      const accepted = await composer.onSend(text, files, observe);
      if (accepted && alive.current && splitDraft(text).refs.length > 0) {
        // Referenced questions belong beside their live reply, including the
        // waiting period and streamed text before the final response arrives.
        setConversationOpen(true);
        setAgentsOpen(false);
      }
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
  useEffect(() => {
    if (!nodesReady || initialFit.current || !copyReady) return;
    if (savedView.current?.camera && data.history_loading) return;
    const frame = requestAnimationFrame(() => {
      initialFit.current = true;
      if (savedView.current?.camera) camera.restore(savedView.current.camera);
      else {
        camera.fit();
        if (data.kind === "live" && canvasRef.current!.clientWidth < 640) {
          const task = currentTask(data.tasks);
          const card = scene.cards.filter((card) => card.task.id === task?.id).at(-1);
          if (card) camera.enter(card.id);
        }
      }
      setFitted(true);
    });
    return () => cancelAnimationFrame(frame);
  }, [nodesReady, camera.fit, camera.enter, camera.restore, data.kind, data.tasks, scene.cards, data.history_loading, copyReady]);
  // The ambient light leans a little toward the pointer. A direct style
  // write on the wrapper keeps this off React's render path entirely.
  useEffect(() => {
    const el = canvasRef.current;
    if (!el || camera.reducedMotion) return;
    let frame: number | null = null;
    const lean = (event: PointerEvent) => {
      if (frame != null) return;
      frame = requestAnimationFrame(() => {
        frame = null;
        const rect = el.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        el.style.setProperty("--lean-x", ((event.clientX - rect.left) / rect.width - 0.5).toFixed(3));
        el.style.setProperty("--lean-y", ((event.clientY - rect.top) / rect.height - 0.5).toFixed(3));
      });
    };
    const rest = () => { el.style.setProperty("--lean-x", "0"); el.style.setProperty("--lean-y", "0"); };
    el.addEventListener("pointermove", lean);
    el.addEventListener("pointerleave", rest);
    return () => {
      if (frame != null) cancelAnimationFrame(frame);
      el.removeEventListener("pointermove", lean);
      el.removeEventListener("pointerleave", rest);
    };
  }, [camera.reducedMotion]);
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
  }, [atlas.structure, nodesReady, camera.fitUpdatedScene]);
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
  const openCard = useCallback((id: string) => {
    const card = sceneCache.current?.cards.find((card) => card.id === id);
    if (tracedTask && card) traceTask(card.task.id === tracedTask.id ? null : card.task.id);
    else camera.enter(id);
  }, [camera.enter, tracedTask, traceTask]);
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
          menu: showMenu,
          quote,
          source: data.id,
          readOnly,
          live: data.kind === "live",
          paused,
          seenCards,
          restoring: !!savedView.current?.camera && !initialFit.current,
          layout: scene.layouts[card.id],
          frame: scene.frames[card.id],
          plannedWidth: plannedWidths.get(card.task.id),
          focused: false,
          detailed: false,
        },
      })) as MacroNode[]);
    });
  }, [
    graph,
    scene,
    atlas,
    positions,
    zh,
    setNodes,
    openCard,
    camera.readStep,
    showMenu,
    quote,
    data.id,
    data.kind,
    readOnly,
    paused,
    seenCards,
    plannedWidths,
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
    () => {
      const visible = new Set(
        scene.cards
          .filter(
            (card) => (data.kind === "live" && !replaying) || card.ordinal <= visibleCount,
          )
          .map((card) => card.id),
      );
      // A branch pill appears and disappears with its owning card.
      for (const [id, card] of atlas.branchAnchor)
        if (visible.has(card)) visible.add(id);
      return visible;
    },
    [scene.cards, visibleCount, data.kind, replaying, atlas.branchAnchor],
  );
  const previousDisplay = useRef<MacroNode[]>([]);
  const displayNodes = useMemo(
    () => {
      const next = nodes.map<MacroNode>((n) => ({
        ...n,
        hidden: !visibleIds.has(n.id),
        data: {
          ...n.data,
          copy: copy ? { cards: Object.fromEntries(
            [n.data.task.id, ...n.data.layout.steps.map((s) => s.id)]
              .filter((id) => copy.cards[id]).map((id) => [id, copy.cards[id]]),
          ) } : undefined,
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
      camera.focusId,
      camera.detailed,
      camera.canvasSize,
      query,
      cardSearchText,
      copy,
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
  // Branch pills are display/navigation only; structural sharing keeps their
  // node data identities stable so idle pills never re-render.
  const previousBranchNodes = useRef<BranchFlowNode[]>([]);
  const branchNodes = useMemo(
    () => {
      // Fan ordinals: "3/5" on a pill tells how wide this parallel push is.
      // A folded group is a bracket around pills, not one of them.
      const fanTotal = new Map<string, number>();
      for (const task of atlas.branches) {
        if (task.group) continue;
        const owner = task.parent_id ?? "";
        fanTotal.set(owner, (fanTotal.get(owner) ?? 0) + 1);
      }
      const fanSeen = new Map<string, number>();
      const next = atlas.branches.map<BranchFlowNode>((task) => {
        const owner = task.parent_id ?? "";
        const frame = atlas.frames[task.id];
        const ordinal = task.group ? undefined : (fanSeen.get(owner) ?? 0) + 1;
        if (ordinal !== undefined) fanSeen.set(owner, ordinal);
        return {
          id: task.id,
          type: "branch",
          position: positions[task.id] ?? { x: 0, y: 0 },
          width: frame.width,
          height: frame.height,
          style: {
            width: frame.width, height: frame.height,
            opacity: traceTasks && !traceTasks.has(owner) ? 0.22 : 1,
          },
          hidden: !visibleIds.has(task.id),
          draggable: false,
          selectable: false,
          focusable: false,
          data: {
            task,
            zh,
            parentCardId: atlas.branchAnchor.get(task.id)!,
            open: openCard,
            toggleGroup,
            fanIndex: ordinal,
            fanCount: ordinal === undefined ? undefined : fanTotal.get(owner)!,
          },
        };
      });
      previousBranchNodes.current = replaceEqualDeep(previousBranchNodes.current, next);
      return previousBranchNodes.current;
    },
    [atlas, positions, visibleIds, zh, openCard, toggleGroup, traceTasks],
  );
  const flowNodes = useMemo<AtlasNode[]>(
    () => (branchNodes.length ? [...displayNodes, ...branchNodes] : displayNodes),
    [displayNodes, branchNodes],
  );
  const edges: Edge[] = useMemo(
    () => {
      const visibleLinks = atlas.links.filter(
        (e) =>
          visibleIds.has(e.source) &&
          visibleIds.has(e.target) &&
          (e.kind !== "replacement" || showReplacements),
      );
      // Single pass over the visible links, covering all edge kinds including
      // the promoted fanout/fanin edges; no per-edge rescans.
      const lanes = edgeLanes(visibleLinks);
      const taskByCard = new Map(scene.cards.map((card) => [card.id, card.task]));
      const activeTargets = new Set<string>();
      if (data.kind === "live" && !paused) {
        for (const card of scene.cards)
          if (ACTIVE.has(card.task.status)) activeTargets.add(card.id);
        for (const task of atlas.branches)
          if (ACTIVE.has(task.status)) activeTargets.add(task.id);
      }
      return visibleLinks.map((e, index) => {
        const fan = e.kind === "fanout" || e.kind === "fanin";
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
            { ...positions[e.source], ...atlas.frames[e.source] },
            { ...positions[e.target], ...atlas.frames[e.target] },
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
          },
          className: `map-edge-${e.kind}${lit.edges.has(e.id) ? " is-lit" : ""}`,
          // Fan edges carry no label: the pill itself names the branch.
          label: fan
            ? undefined
            : e.label ||
              (e.kind === "replacement"
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
            // Dependencies stay the strongest line; fan edges are thinner and
            // translucent (branch.css), context is a fainter, sparser dash.
            strokeWidth: highlighted ? 2.4 : e.kind === "dependency" ? 1.55 : fan ? 0.95 : 1.3,
            vectorEffect: "non-scaling-stroke",
            strokeDasharray:
              e.kind === "dependency" || fan
                ? undefined
                : e.kind === "context"
                  ? "3 10"
                  : "4 5",
          },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: e.kind === "replacement" ? "#a48caf" : "#8aa5b8",
            width: 32,
            height: 32,
          },
          ariaLabel:
            e.evidence ||
            (fan
              ? `Team branch: ${e.source} → ${e.target}`
              : e.kind === "dependency"
                ? `Dependency: ${e.source} → ${e.target}`
                : `Plan replacement: ${e.source} → ${e.target_plan_id} (${e.target_count} tasks, representative ${e.target})`),
        };
      });
    },
    [
      atlas,
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
  const replacementCount = graph.links.filter(
    (e) => e.kind === "replacement",
  ).length;
  // One pass, one bucket per task: the strip must partition, not double-count
  // a failed task that also carries a question.
  const tally = useMemo(() => {
    const buckets = { done: 0, running: 0, question: 0, failed: 0, other: 0 };
    for (const task of data.tasks) {
      if (task.status === "done") buckets.done++;
      else if (ACTIVE.has(task.status)) buckets.running++;
      else if (task.pending_question) buckets.question++;
      else if (task.status === "failed") buckets.failed++;
      else buckets.other++;
    }
    return buckets;
  }, [data.tasks]);
  const complete = tally.done;
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
      if (target?.closest("input, textarea, select, [contenteditable]")) return;
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
    <MapNotesContext.Provider value={notesScope}>
    <MapArtifactContext.Provider value={artifactScope}>
      {/* The second header line: one sentence on where the work stands, and,
          when something waits on the reader, a link straight to it. */}
      <div className="map-status-row">
        <p className="map-status-line" role="status">
          {(composer.pending || (!paused && activePhase)) ? <i className="map-live-dot" aria-hidden /> : null}
          <span className="map-status-text">
            {mapStatusSentence({
              total: data.tasks.length,
              complete,
              running: tally.running,
              pending: composer.pending,
              paused,
              hasOpenWork: data.tasks.some((task) => ACTIVE.has(task.status) || task.status === "pending"),
              role: activePhase,
              zh,
            })}
          </span>
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
      </div>
      {data.kind === 'live' && !readOnly && <PendingBanner questions={snapshot.pending_questions ?? []} backlog={snapshot.backlog} onAnswer={actions.onAnswer} onLocate={locateAttention} />}
      {camera.detailed && attentionIndex >= 0 && (
        <div className="map-attention-detail" role="status">
          <strong>{zh ? "待处理" : "Needs attention"} {attentionIndex + 1} / {attention.length}</strong>
          <p tabIndex={0}>{attentionReason(attention[attentionIndex], data.events, zh)}</p>
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
          data-hovering={!!hoverId && !camera.detailed}
        >
          <div className="map-ambient" aria-hidden="true"><i /><i /><i /></div>
          {conversationOpen && <MapConversation events={actions.conversationEvents} connected={actions.connected} pending={composer.pending} artifacts={actions.artifacts} zh={zh} onClose={() => setConversationOpen(false)} onOpenArtifact={actions.onOpenArtifact} onOpenDelivery={actions.onOpenReceipt} />}
          {agentsOpen && data.kind === 'live' && <aside className="map-agent-drawer nowheel nodrag nopan">
            <AgentActivity view={snapshot.mission_view} roles={snapshot.roles} events={events}
              taskId={focusedNode?.data.task.id || snapshot.mission_view?.mission.id || undefined}
              paused={paused && !composer.pending} onClose={() => setAgentsOpen(false)} />
          </aside>}
          {flight && !readOnly && <MapDispatchMotion flight={flight} canvas={canvasRef} zh={zh} historical={composer.historical}
            onReveal={(taskId) => { if (data.tasks.some((task) => task.id === taskId)) camera.fit(); }}
            onLand={(id) => setFlight((current) => current?.id === id ? { ...current, landed: true } : current)}
            onFinish={(id) => setFlight((current) => current?.id === id ? null : current)} />}
          <div className="map-canvas-toolbar nowheel">
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
                    camera.enter(focusedNode.data.previousId)
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
                    camera.enter(focusedNode.data.nextId)
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
                  <Activity size={15} /><i data-active={!!activePhase || composer.pending} aria-hidden /><span>{zh ? 'Agent 动态' : 'Agent activity'}</span>
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
            <ReactFlow<AtlasNode>
              nodes={flowNodes}
              edges={edges}
              nodeTypes={NODE_TYPES}
              edgeTypes={EDGE_TYPES}
              onNodesChange={onNodesChange as OnNodesChange<AtlasNode>}
              onNodeMouseEnter={(_, node) => setHoverId(node.id)}
              onNodeMouseLeave={() => setHoverId(null)}
              onMove={camera.onMove}
              defaultViewport={INITIAL_VIEWPORT}
              minZoom={0.035}
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
              onlyRenderVisibleElements
              proOptions={{ hideAttribution: true }}
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
                  minZoom: 0.035,
                  duration: camera.reducedMotion ? 0 : 320,
                }}
              />
              <MiniMap
                nodeColor={(n) =>
                  n.type === "branch"
                    ? "#c5d4e2"
                    : MINIMAP_STATUS[statusKey((n.data as MacroData).task)] ??
                      "#a7bfd9"
                }
                maskColor="var(--map-minimap-mask)"
                maskStrokeColor="#85aacf"
                maskStrokeWidth={2}
                onClick={(_, point) => camera.navigate(point)}
                pannable
                zoomable
                ariaLabel={zh ? "地图导航预览" : "Map navigation preview"}
              />
            </ReactFlow>
          )}
          {data.kind === "live" && replaying && playbackStrip}
          <div className="map-legend nowheel">
            <span
              title={
                zh
                  ? "同一会话中的时间归属，不是执行依赖"
                  : "Chronological context, not execution dependencies"
              }
            >
              <b className="dashed" />
              {zh ? "内容关联" : "Related work"}
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
          {!readOnly && (
            <MapComposer {...composer} pendingLabel={pendingLabel}
              dispatchStatus={flight?.result?.type === 'task' ? flight.landed || composer.historical ? 'task' : 'launching' : flight?.result?.outcome}
              onSend={submitFromMap} onCancel={cancelFromMap} overview={!camera.detailed} />
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
                {zh ? "引用" : "Reference"}
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
          {(graph.cyclic || graph.missing > 0) && (
            <div className="map-graph-warning">
              {graph.cyclic
                ? zh
                  ? "检测到循环引用，保留原始连线。"
                  : "Cyclic references retained."
                : `${graph.missing} ${zh ? "个依赖不在当前记录范围内" : "dependencies outside the available history"}`}
            </div>
          )}
        </div>
      </div>
      {data.kind !== "live" && playbackStrip}
    </MapArtifactContext.Provider>
    </MapNotesContext.Provider>
  );
}

/** Memoized: the app re-renders on every streamed event and poll; the map
 * only needs to follow its own (throttled) props, not that firehose. */
export const MapPanel = memo(function MapPanel({
  snapshot,
  events,
  managerSteps = [],
  draft,
  onDraftChange,
  onSend,
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
}: {
  snapshot: Snapshot;
  events: EventMsg[];
  managerSteps?: Array<{ label: string; detail?: string }>;
  draft: string;
  onDraftChange: (text: string) => void;
  onSend: MapSend;
  pending: boolean;
  onCancel: () => void;
  focusSignal: number;
  readOnly?: boolean;
  onOpenSettings?: () => void;
  routeOverride?: MessageRouteOverride;
  onRouteOverrideChange?: (route: MessageRouteOverride) => void;
} & MapWorkspaceActions) {
  const { locale } = useI18n();
  const zh = locale === "zh-CN";
  const [attachments, setAttachments] = useState<File[]>([]);
  const currentDraft = useRef(draft);
  currentDraft.current = draft;
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const send: MapSend = useCallback(async (text, files = [], observe) => {
    const accepted = await onSend(text, files, (result) => {
      if (!mounted.current) return;
      if (result.type === 'settled' && result.outcome === 'error' && !currentDraft.current.trim()) {
        onDraftChange(text);
        setAttachments((current) => current.length ? current : files);
      }
      observe?.(result);
    });
    if (accepted && mounted.current) {
      if (currentDraft.current === text) onDraftChange("");
      setAttachments((current) =>
        current.filter((file) => !files.includes(file)),
      );
    }
    return accepted;
  }, [onSend, onDraftChange]);
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
    /^(life\.(mission\.|phase\.|planner\.task_added)|round\.|agent\.message|engineer\.progress|ui\.argus|team\.|idea\.portfolio\.)/.test(String(e.type))).at(-1);
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
  const actions = useMemo<MapWorkspaceActions>(
    () => ({ conversationEvents, connected, artifacts, deliveryCount,
             onOpenDelivery, onOpenReceipt, onOpenArtifact, onAnswer }),
    [conversationEvents, connected, artifacts, deliveryCount,
     onOpenDelivery, onOpenReceipt, onOpenArtifact, onAnswer],
  );
  const composer = useMemo<MapComposerProps>(
    () => ({
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
      sessionName: snapshot.session.display_name || snapshot.session.id,
      historical: source !== "live",
      zh,
    }),
    [routeOverride, onRouteOverrideChange, draft, onDraftChange, send, attachments,
     pending, onCancel, focusSignal, snapshot.session.display_name, snapshot.session.id,
     source, zh],
  );
  const switchSource = (value: string) => {
    setSource(value);
    writeLocalStorage("argus.map.source.v1", value);
    const url = new URL(window.location.href);
    url.searchParams.set("dataset", value);
    window.history.replaceState(null, "", url);
  };
  return (
    <section
      ref={sectionRef}
      className="argus-map"
      aria-label={zh ? "进度地图" : "Progress map"}
    >
      <header className="map-header">
        <div className="map-heading">
          <h1 title={snapshot.session.display_name}>{snapshot.session.display_name}</h1>
        </div>
        <details className="map-more">
          <summary aria-label={zh ? "地图选项" : "Map options"} title={zh ? "地图选项" : "Map options"}>
            <SlidersHorizontal size={16} />
          </summary>
          <div className="map-more-menu">
            {data && <MapTeamProgress events={data.events} zh={zh} />}
            <div className="map-dataset-bar">
              <Compass size={15} />
              <select
                aria-label={zh ? "地图数据来源" : "Map data source"}
                value={source}
                onChange={(e) => switchSource(e.target.value)}
              >
                <option value="live">
                  {zh ? "当前会话" : "Current session"} ·{" "}
                  {snapshot.session.display_name}
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
              <button type="button" onClick={onOpenSettings} className="map-scope-button">
                {zh ? "地图模型设置" : "Map model settings"}
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
          info={info.data} zh={zh} readOnly={readOnly} onChoose={selectHistory} />
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
          {zh ? "暂时无法更新，已保留加载的地图。" : "Updates are unavailable. Your loaded map is preserved."}
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
            events={events}
            pendingLabel={managerSteps.at(-1)?.detail || managerSteps.at(-1)?.label}
            viewKey={viewKey}
            paused={paused}
            sessionId={snapshot.session.id}
            zh={zh}
            readOnly={readOnly}
            replacements={replacements}
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
  );
});
