import {
  BaseEdge,
  EdgeLabelRenderer,
  getStraightPath,
  Handle,
  Position,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { FileText, Lightbulb, MoreHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { linkKindLabel, type HumanCard, type LinkKind } from "./canvas";
import { useSteppedZoom } from "./zoomStep";
import "./canvas.css";

export interface HumanCardData extends Record<string, unknown> {
  card: HumanCard;
  zh: boolean;
  /** A card just placed and not saved yet: it opens on its text field. */
  draft?: boolean;
  readOnly: boolean;
  commit: (id: string, text: string) => void;
  cancel: (id: string) => void;
  openFile: (path: string) => void;
  menu: (id: string, point: { x: number; y: number }) => void;
  /** Set by the menu's "edit" so the text opens for editing. */
  editing?: boolean;
}
export type HumanCardNode = Node<HumanCardData, "human">;

const SIDES = [Position.Left, Position.Right, Position.Top, Position.Bottom];

/** Handles every card carries: the hidden side ports lines attach to, and one
 * visible dot a person drags from to draw a link. */
export function CanvasHandles({ connectable }: { connectable: boolean }) {
  return (
    <>
      {(["source", "target"] as const).flatMap((type) => SIDES.map((position) => (
        <Handle key={`${type}-${position}`} id={position} type={type} position={position} isConnectable={false} />
      )))}
      {connectable && (
        <Handle
          id="connect"
          type="source"
          position={Position.Right}
          className="canvas-connect"
          isConnectableEnd={false}
          title="拖到另一张卡上连线 / Drag onto another card to link"
        />
      )}
    </>
  );
}

function sizeLabel(bytes: number) {
  if (bytes >= 1 << 20) return `${(bytes / (1 << 20)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

export function HumanCardView({ data }: NodeProps<HumanCardNode>) {
  const { card, zh, draft, readOnly } = data;
  const [editing, setEditing] = useState(!!draft || !!data.editing);
  const [text, setText] = useState(card.text);
  const field = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (data.editing) setEditing(true); }, [data.editing]);
  useEffect(() => { if (editing) field.current?.focus(); }, [editing]);
  useEffect(() => { if (!editing) setText(card.text); }, [card.text, editing]);
  const finish = (save: boolean) => {
    setEditing(false);
    const value = text.trim();
    if (save && value && value !== card.text) data.commit(card.id, value);
    else if (draft || (save && !value && card.kind === "idea")) data.cancel(card.id);
  };
  const isFile = card.kind === "file";
  return (
    <div
      className={`canvas-card canvas-card-${card.kind}${editing ? " is-editing" : ""}`}
      data-testid={`canvas-card-${card.kind}`}
      onContextMenu={(e) => {
        if (readOnly || draft) return;
        e.preventDefault();
        e.stopPropagation();
        data.menu(card.id, { x: e.clientX, y: e.clientY });
      }}
    >
      <CanvasHandles connectable={!readOnly && !draft} />
      <header>
        {isFile ? <FileText aria-hidden /> : <Lightbulb aria-hidden />}
        <span>{isFile ? (zh ? "资料卡" : "File") : (zh ? "想法卡" : "Idea")}</span>
        {!readOnly && !draft && (
          <button
            type="button"
            className="canvas-card-more nodrag"
            aria-label={zh ? "卡片菜单" : "Card menu"}
            onClick={(e) => { e.stopPropagation(); data.menu(card.id, { x: e.clientX, y: e.clientY }); }}
          >
            <MoreHorizontal aria-hidden />
          </button>
        )}
      </header>
      {isFile && card.attachment && (
        <button
          type="button"
          className="canvas-file nodrag"
          onClick={(e) => { e.stopPropagation(); data.openFile(card.attachment!.relative_path); }}
          title={card.attachment.relative_path}
        >
          <strong>{card.attachment.name}</strong>
          <small>{sizeLabel(card.attachment.size_bytes)}</small>
        </button>
      )}
      {editing ? (
        <textarea
          ref={field}
          className="nodrag nowheel"
          value={text}
          maxLength={2000}
          aria-label={zh ? "卡片内容" : "Card text"}
          placeholder={zh ? "写一句要求、假设、问题或待办…" : "A requirement, assumption, question or to-do…"}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => finish(true)}
          onKeyDown={(e) => {
            e.stopPropagation();
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); finish(true); }
            if (e.key === "Escape") { e.preventDefault(); setText(card.text); finish(false); }
          }}
        />
      ) : (
        (card.text || !isFile) && (
          <p onDoubleClick={(e) => { if (readOnly) return; e.stopPropagation(); setEditing(true); }}>{card.text}</p>
        )
      )}
    </div>
  );
}

export type HumanLinkEdge = Edge<{ kind: LinkKind; zh: boolean; menu?: (id: string, point: { x: number; y: number }) => void }, "human">;

/** A link a person drew: a solid line with its kind written on it, unlike
 * Argus's recorded relations. */
export function HumanLinkView({ id, sourceX, sourceY, targetX, targetY, data, markerEnd }: EdgeProps<HumanLinkEdge>) {
  const zoom = useSteppedZoom((step) => step);
  const [path, labelX, labelY] = getStraightPath({ sourceX, sourceY, targetX, targetY });
  const scale = Math.min(12, 1 / Math.max(zoom, 0.02));
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} className="canvas-link" style={{ strokeWidth: 3, vectorEffect: "non-scaling-stroke" }} />
      <EdgeLabelRenderer>
        <button
          type="button"
          className="canvas-link-label nodrag nopan"
          data-testid="canvas-link-label"
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px) scale(${scale})` }}
          onClick={(e) => { e.stopPropagation(); data?.menu?.(id, { x: e.clientX, y: e.clientY }); }}
        >
          {data ? linkKindLabel(data.kind, data.zh) : ""}
        </button>
      </EdgeLabelRenderer>
    </>
  );
}
