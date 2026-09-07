import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
} from "@xyflow/react";
import {
  ArrowLeft,
  Check,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Compass,
  GitBranch,
  LocateFixed,
  Maximize2,
  Pause,
  Play,
  RotateCcw,
  Search,
  Settings2,
} from "lucide-react";
import { api, type Snapshot } from "../api";
import { readLocalStorage, writeLocalStorage } from "../lib/storage";
import { useI18n } from "../i18n";
import { ACTIVE, buildMap, connectMap, statusKey, type Dataset } from "./model";
import { layoutScene } from "./submap";
import { relationPorts } from "./graphLayout";
import { MacroTaskNode, type MacroData, type MacroNode } from "./MacroTaskNode";
import { INITIAL_VIEWPORT, useSemanticCamera } from "./useSemanticCamera";
import { useMapCopy } from "./useMapCopy";
import { MapComposer, type MapComposerProps } from "./MapComposer";
import { referenceText, type CardReference } from "./presentation";
import type { EventMsg } from "../../../core/src/types";
import "@xyflow/react/dist/style.css";
import "./map.css";
import "./submap.css";
import { MapRelationEdge } from "./MapRelationEdge";
import { MapHistoryChoice } from "./MapHistoryChoice";
import { mapIsPaused, mergeMapProgress, parseMapSelection, type MapSelection } from "./incremental";
import { recalledView, rememberView } from "./viewMemory";

const NODE_TYPES = { task: MacroTaskNode };
const EDGE_TYPES = { relation: MapRelationEdge };
function MapCanvas({
  data,
  zh,
  composer,
  activePhase,
  readOnly,
  sessionId,
  viewKey,
  paused,
}: {
  data: Dataset;
  sessionId: string;
  zh: boolean;
  composer: MapComposerProps;
  activePhase?: string;
  readOnly: boolean;
  viewKey: string;
  paused: boolean;
}) {
  const graph = useMemo(() => buildMap(data.tasks), [data.tasks]);
  const [nodes, setNodes, onNodesChange] = useNodesState<MacroNode>([]);
  const canvasRef = useRef<HTMLDivElement>(null);
  const camera = useSemanticCamera(canvasRef, !readOnly);
  const nodesReady = useNodesInitialized();
  const initialFit = useRef(false);
  const savedView = useRef<ReturnType<typeof recalledView> | null>(null);
  if (!savedView.current) savedView.current = recalledView(viewKey);
  const [seenCards] = useState(() => new Set(savedView.current?.scene?.cards.map((card) => card.id)));
  const focusedNode = nodes.find((n) => n.id === camera.focusId);
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
  useEffect(() => {
    if (!nodesReady || initialFit.current || !copyReady) return;
    if (savedView.current?.camera && data.history_loading) return;
    const frame = requestAnimationFrame(() => {
      initialFit.current = true;
      if (savedView.current?.camera) camera.restore(savedView.current.camera);
      else camera.fit();
    });
    return () => cancelAnimationFrame(frame);
  }, [nodesReady, camera.fit, camera.restore, data.history_loading, copyReady]);
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
  const showMenu = useCallback(
    (ref: CardReference, point: { x: number; y: number }) => {
      if (readOnly) return;
      setMenu({
        ref,
        x: Math.min(window.innerWidth - 180, point.x),
        y: Math.min(window.innerHeight - 70, point.y),
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
  const [visibleCount, setVisibleCount] = useState(graph.tasks.length);
  const [playing, setPlaying] = useState(false);
  const [showReplacements, setShowReplacements] = useState(false);
  const [focusFeedback, setFocusFeedback] = useState("");
  useEffect(() => {
    setNodes((previous) => {
      return replaceEqualDeep(previous, scene.cards.map((card) => ({
        id: card.id,
        type: "task",
        position: scene.positions[card.id],
        width: scene.frames[card.id].width,
        height: scene.frames[card.id].height,
        style: {
          width: scene.frames[card.id].width,
          height: scene.frames[card.id].height,
        },
        data: {
          ...card,
          zh,
          open: camera.enter,
          readStep: camera.readStep,
          menu: showMenu,
          quote,
          source: data.id,
          readOnly,
          live: data.kind === "live",
          paused,
          seenCards,
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
    zh,
    setNodes,
    camera.enter,
    camera.readStep,
    showMenu,
    quote,
    data.id,
    data.kind,
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
  const visibleIds = useMemo(
    () =>
      new Set(
        scene.cards
          .filter(
            (card) => data.kind === "live" || card.ordinal <= visibleCount,
          )
          .map((card) => card.id),
      ),
    [scene.cards, visibleCount, data.kind],
  );
  const previousDisplay = useRef<MacroNode[]>([]);
  const displayNodes = useMemo(
    () => {
      const next = nodes.map((n) => ({
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
        },
        style: {
          ...n.style,
          opacity:
            query &&
            !`${n.data.task.title} ${n.data.task.objective} ${copy?.cards[n.data.task.id]?.title || ""} ${copy?.cards[n.data.task.id]?.summary || ""} ${n.data.part > 1 ? (zh ? `续篇 ${n.data.part - 1}` : `Continued ${n.data.part - 1}`) : ""}`
              .toLowerCase()
              .includes(query.toLowerCase())
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
      copy,
      zh,
    ],
  );
  const edges: Edge[] = useMemo(
    () =>
      scene.links
        .filter(
          (e) =>
            visibleIds.has(e.source) &&
            visibleIds.has(e.target) &&
            (e.kind !== "replacement" || showReplacements),
        )
        .map((e, index, visibleLinks) => ({
          id: e.id,
          source: e.source,
          target: e.target,
          ...relationPorts(
            { ...scene.positions[e.source], ...scene.frames[e.source] },
            { ...scene.positions[e.target], ...scene.frames[e.target] },
          ),
          type: "relation",
          data: {
            lane: visibleLinks
              .slice(0, index)
              .filter((l) => l.source === e.source || l.target === e.target)
              .length,
          },
          className: `map-edge-${e.kind}`,
          label:
            e.label ||
            (e.kind === "replacement"
              ? zh
                ? "转入新计划"
                : "New plan"
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
            stroke: e.cycle
              ? "#dc6648"
              : e.kind === "replacement"
                ? "#a48caf"
                : e.kind === "dependency"
                  ? "#527fa7"
                  : "#7594ad",
            strokeWidth: e.kind === "dependency" ? 1.55 : 1.3,
            vectorEffect: "non-scaling-stroke",
            strokeDasharray: e.kind === "dependency" ? undefined : "5 6",
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
        })),
    [
      scene.links,
      visibleIds,
      showReplacements,
      zh,
      scene.positions,
      scene.frames,
    ],
  );
  const replacementCount = graph.links.filter(
    (e) => e.kind === "replacement",
  ).length;
  const complete = data.tasks.filter((t) => t.status === "done").length;
  const attention = data.tasks.filter(
    (t) => t.pending_question || t.status === "failed",
  ).length;
  const focus = (id: string) => {
    setVisibleCount((c) =>
      Math.max(c, scene.cards.find((card) => card.id === id)?.ordinal || 1),
    );
    camera.enter(id);
  };
  const locateCurrent = () => {
    const target =
      data.tasks.find((t) => ACTIVE.has(t.status)) ??
      data.tasks.find((t) => t.pending_question) ??
      data.tasks.find((t) => t.status === "pending");
    if (target) {
      focus(
        scene.cards.filter((card) => card.task.id === target.id).at(-1)!.id,
      );
      setFocusFeedback("");
    } else
      setFocusFeedback(
        zh ? "没有进行中或待开始的任务" : "No active or planned tasks",
      );
  };
  return (
    <>
      <div className="map-summary">
        <div>
          <span className="map-summary-value">{data.tasks.length}</span>
          <span>{zh ? "个任务" : "tasks"}</span>
          {scene.cards.length > graph.tasks.length && (
            <span className="map-card-count">
              · {scene.cards.length} {zh ? "张卡片" : "cards"}
            </span>
          )}
          <i />
          <Check size={13} />
          <strong>{complete}</strong>
          <span>{zh ? "已完成" : "completed"}</span>
          {attention > 0 && (
            <>
              <i />
              <span className="map-attention-dot" />
              <strong>{attention}</strong>
              <span>{zh ? "值得关注" : "need attention"}</span>
            </>
          )}
        </div>
        {paused ? (
          <span className="map-paused-label"><Pause size={13} />{zh ? "已暂停" : "Paused"}</span>
        ) : activePhase && (
          <span className="map-live-phase">
            <i />
            {(
              {
                planner: zh ? "规划中" : "Planning",
                manager: zh ? "统筹中" : "Coordinating",
                engineer: zh ? "执行中" : "Running",
                reviewer: zh ? "审查中" : "Reviewing",
              } as Record<string, string>
            )[activePhase] || activePhase}
          </span>
        )}
        <span className="map-summary-note">
          {zh
            ? "滚轮缩放 · 拖动画布 · 点击任务深入"
            : "Scroll to zoom · drag to pan · select a task to explore"}
        </span>
      </div>
      <div className="map-workspace">
        <div
          ref={canvasRef}
          className="map-canvas-wrap"
          data-focused={!!camera.focusId}
        >
          <div className="map-canvas-toolbar nowheel">
            <label className="map-search">
              <Search size={14} />
              <input
                aria-label={zh ? "搜索地图任务" : "Search map tasks"}
                placeholder={zh ? "搜索任务…" : "Find a task…"}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    const match = scene.cards.find((card) =>
                      `${card.task.title} ${card.task.objective} ${copy?.cards[card.task.id]?.title || ""} ${copy?.cards[card.task.id]?.summary || ""} ${card.part > 1 ? (zh ? `续篇 ${card.part - 1}` : `Continued ${card.part - 1}`) : ""}`
                        .toLowerCase()
                        .includes(query.toLowerCase()),
                    );
                    if (match) focus(match.id);
                  }
                }}
              />
            </label>
            <button
              onClick={locateCurrent}
              title={zh ? "定位当前任务" : "Locate current task"}
            >
              <LocateFixed size={15} />
              <span>{zh ? "定位当前" : "Locate current"}</span>
            </button>
            <button
              onClick={camera.fit}
              title={zh ? "适配全图" : "Fit map"}
              aria-label="Fit map"
            >
              <Maximize2 size={15} />
            </button>
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
          </div>
          {focusFeedback && (
            <div className="map-feedback" role="status">
              {focusFeedback}
            </div>
          )}
          {camera.detailed && focusedNode && focusedNode.data.partCount > 1 && (
            <nav
              className="map-part-switcher nowheel"
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
          {graph.tasks.length === 0 ? (
            <div className="map-empty">
              <GitBranch size={36} />
              <h3>{zh ? "暂无任务" : "No tasks yet"}</h3>
              <p>
                {readOnly
                  ? zh
                    ? "尚无任务记录。"
                    : "No task records are available."
                  : zh
                    ? "在下方输入研究目标，开始新的任务。"
                    : "Enter a research goal below to start a task."}
              </p>
            </div>
          ) : (
            <ReactFlow<MacroNode>
              nodes={displayNodes}
              edges={edges}
              nodeTypes={NODE_TYPES}
              edgeTypes={EDGE_TYPES}
              onNodesChange={onNodesChange}
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
                onFitView={camera.fit}
                fitViewOptions={{
                  padding: 0.16,
                  maxZoom: 0.27,
                  minZoom: 0.035,
                  duration: camera.reducedMotion ? 0 : 320,
                }}
              />
              <MiniMap
                nodeColor={(n) =>
                  statusKey((n.data as MacroData).task) === "done"
                    ? "#b5d6c7"
                    : "#a7bfd9"
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
              onClick={() => setShowReplacements((v) => !v)}
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
            <MapComposer {...composer} overview={!camera.detailed} />
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
      {data.kind !== "live" && (
        <div className="map-playback">
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
              camera.back();
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
        </div>
      )}
    </>
  );
}

export function MapPanel({
  snapshot,
  events,
  draft,
  onDraftChange,
  onSend,
  pending,
  onCancel,
  focusSignal,
  readOnly = false,
  onOpenSettings,
}: {
  snapshot: Snapshot;
  events: EventMsg[];
  draft: string;
  onDraftChange: (text: string) => void;
  onSend: (text: string, attachments?: File[]) => Promise<boolean>;
  pending: boolean;
  onCancel: () => void;
  focusSignal: number;
  readOnly?: boolean;
  onOpenSettings?: () => void;
}) {
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
  const send = async (text: string, files: File[] = []) => {
    const accepted = await onSend(text, files);
    if (accepted && mounted.current) {
      if (currentDraft.current === text) onDraftChange("");
      setAttachments((current) =>
        current.filter((file) => !files.includes(file)),
      );
    }
    return accepted;
  };
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
    refetchInterval: (query) => approved && query.state.data?.history_loading ? 400 : false,
  });
  const paused = source === "live" && mapIsPaused(snapshot);
  const updateKey = JSON.stringify([
    events.filter((e) => e.run_label !== "map-summary" &&
      /^(life\.(mission\.|phase\.|planner\.task_added)|round\.|agent\.message)/.test(String(e.type))).at(-1)?.ts,
    snapshot.backlog,
    paused,
  ]);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (source !== "live" || !approved || refreshTimer.current) return;
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null;
      void client.invalidateQueries({
        queryKey: ["map-live", snapshot.session.id],
      });
    }, 650);
  }, [updateKey, source, snapshot.session.id, client, approved]);
  useEffect(
    () => () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      refreshTimer.current = null;
    },
    [source, snapshot.session.id, approved],
  );

  const data = source === "live" ? live.data : dataset.data;
  const switchSource = (value: string) => {
    setSource(value);
    writeLocalStorage("argus.map.source.v1", value);
    const url = new URL(window.location.href);
    url.searchParams.set("dataset", value);
    window.history.replaceState(null, "", url);
  };
  return (
    <section
      className="argus-map"
      aria-label={zh ? "研究进度地图" : "Research progress map"}
    >
      <header className="map-header">
        <div className="map-heading-icon">
          <GitBranch size={20} />
        </div>
        <div className="map-heading">
          <div className="map-eyebrow">ARGUS / RESEARCH MAP</div>
          <h1>{zh ? "研究地图" : "Research map"}</h1>
        </div>
        <div className="map-header-actions">
        {!readOnly && onOpenSettings && (
          <button type="button" onClick={onOpenSettings} className="map-settings"
            aria-label={zh ? "地图模型设置" : "Map model settings"} title={zh ? "地图模型设置" : "Map model settings"}>
            <Settings2 size={16} />
          </button>
        )}
        {source === "live" && info.data && (
          <button type="button" className="map-scope-button" onClick={() => setChooseHistory(true)}>
            {zh ? "加载范围" : "History range"}
          </button>
        )}
        <span className="map-source-badge">
          <span />
          {source === "live"
            ? zh
              ? "当前会话"
              : "Current session"
            : data?.kind === "synthetic"
              ? zh
                ? "人工示例"
                : "Synthetic example"
              : data?.kind === "demo"
                ? zh
                  ? "历史演示"
                  : "Recorded demo"
                : zh
                  ? "历史记录"
                  : "Historical records"}
        </span>
        </div>
      </header>
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
            viewKey={viewKey}
            paused={paused}
            sessionId={snapshot.session.id}
            zh={zh}
            readOnly={readOnly}
            activePhase={
              source === "live" && !paused
                ? snapshot.roles.find((r) => r.active)?.role
                : undefined
            }
            composer={{
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
            }}
          />
        </ReactFlowProvider>
      ) : (
        <div className="map-empty">{zh ? "正在载入地图…" : "Loading map…"}</div>
      )}
    </section>
  );
}
