import type { MessageRouteOverride } from '../api';
import type { MapSend } from './submission';
import { useEffect, useId, useRef, useState, type CSSProperties } from "react";
import { ArrowUp, Check, ChevronDown, CornerDownLeft, Square, X } from "lucide-react";
import { ArgusMark } from "../components/Wordmark";
import { referenceText, splitDraft } from "./presentation";
import { useI18n } from "../i18n";
import {
  addComposerFiles,
  extractFilesFromDataTransfer,
  MESSAGE_ATTACHMENT_ACCEPT,
} from "../lib/attachments";
import { formatBytes } from "../lib/format";
import { ComposerAttachmentChip } from "../components/ComposerAttachmentChip";
import { isImeComposing } from "../lib/ime";
import "./composerMotion.css";

export interface MapComposerProps {
  value: string;
  onChange: (text: string) => void;
  onSend: MapSend;
  attachments: File[];
  onAttachmentsChange: (files: File[]) => void;
  pending: boolean;
  pendingLabel?: string;
  dispatchStatus?: "launching" | "task" | "message" | "error" | "cancelled";
  onCancel: () => void;
  focusSignal: number;
  sessionName: string;
  historical: boolean;
  zh: boolean;
  overview?: boolean;
  routeOverride?: MessageRouteOverride;
  onRouteOverrideChange?: (route: MessageRouteOverride) => void;
}
export function MapComposer({
  value,
  onChange,
  onSend,
  attachments,
  onAttachmentsChange,
  pending,
  pendingLabel,
  dispatchStatus,
  onCancel,
  focusSignal,
  sessionName,
  historical,
  zh,
  overview = true,
  routeOverride = 'auto',
  onRouteOverrideChange,
}: MapComposerProps) {
  const { t } = useI18n();
  const editorId = useId();
  const input = useRef<HTMLTextAreaElement>(null);
  const dock = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const submitting = useRef(false);
  const mounted = useRef(true);
  const sentTimer = useRef<ReturnType<typeof setTimeout>>();
  const [attachmentNotice, setAttachmentNotice] = useState("");
  const [sent, setSent] = useState(false);
  const [focused, setFocused] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [inputHeight, setInputHeight] = useState(44);
  const currentValue = useRef(value);
  currentValue.current = value;
  const compact =
    overview &&
    !focused &&
    !hovered &&
    !value.trim() &&
    !attachments.length &&
    !attachmentNotice;
  const { refs, text } = splitDraft(value);
  useEffect(() => {
    mounted.current = true;
    // Removing a focused chip can skip its blur event; the next document focus
    // still needs to release the expanded composer.
    const focus = (event: FocusEvent) => {
      const target = (event.type === "focusout" ? event.relatedTarget : event.target) as Element | null;
      // Tabbing to the closed island should announce its button, not open it.
      // The brand button also keeps the editor open while picking a file.
      if (target?.closest?.(".map-island-launch, .map-island-stop, .map-composer-brand")) return;
      setFocused(Boolean(dock.current?.contains(target)));
    };
    document.addEventListener("focusin", focus);
    document.addEventListener("focusout", focus);
    return () => {
      mounted.current = false;
      clearTimeout(sentTimer.current);
      document.removeEventListener("focusin", focus);
      document.removeEventListener("focusout", focus);
    };
  }, []);
  useEffect(() => {
    if (focusSignal) input.current?.focus();
  }, [focusSignal]);
  const resizeInput = () => {
    if (!input.current) return;
    input.current.style.height = "0px";
    const height = Math.min(156, Math.max(44, input.current.scrollHeight));
    input.current.style.height = `${height}px`;
    setInputHeight(height);
  };
  useEffect(resizeInput, [text]);
  useEffect(() => {
    window.addEventListener("resize", resizeInput);
    return () => window.removeEventListener("resize", resizeInput);
  }, []);
  const open = () => {
    setFocused(true);
    // Keep focus in the user gesture so iOS opens the software keyboard.
    input.current?.focus();
  };
  const collapse = () => {
    setFocused(false);
    setHovered(false);
    if (dock.current?.contains(document.activeElement))
      (document.activeElement as HTMLElement | null)?.blur();
  };
  const submit = async () => {
    if (!text.trim() || pending || submitting.current) return;
    submitting.current = true;
    try {
      if ((await onSend(value, attachments)) && mounted.current) {
        setAttachmentNotice("");
        setSent(true);
        if (!currentValue.current.trim() || currentValue.current === value) collapse();
        clearTimeout(sentTimer.current);
        sentTimer.current = setTimeout(() => setSent(false), 1800);
      }
    } finally {
      submitting.current = false;
    }
  };
  const addFiles = (files: File[]) => {
    if (pending || submitting.current || !files.length) return;
    const { accepted, issues } = addComposerFiles(attachments, files);
    onAttachmentsChange([...attachments, ...accepted]);
    setAttachmentNotice(
      issues
        .map((issue) => {
          if (issue.code === "unsupported")
            return t("chat.attachUnsupported", { name: issue.fileName });
          if (issue.code === "too-large")
            return t("chat.attachTooLarge", {
              name: issue.fileName,
              size: formatBytes(issue.limitBytes),
            });
          if (issue.code === "too-many")
            return t("chat.attachTooMany", { count: issue.limitCount });
          return t("chat.attachTotalTooLarge", {
            size: formatBytes(issue.limitBytes),
          });
        })
        .join(" "),
    );
  };
  const feedback = dispatchStatus
    ? {
        launching: [zh ? "任务已接收" : "Task accepted", zh ? "正在放入地图…" : "Adding it to your map…"],
        task: [zh ? "任务已进入地图" : "Your task is on the map", zh ? "跟随地图，查看执行进展" : "Follow its progress on the map"],
        message: [zh ? "Argus 已回复" : "Argus replied", zh ? "在对话中查看回复" : "Open the conversation to read it"],
        error: [zh ? "发送没有成功" : "Message could not be sent", zh ? "草稿已保留，可以重试" : "Your draft is ready to retry"],
        cancelled: [zh ? "已停止等待" : "Waiting stopped", zh ? "随时继续对话" : "Continue whenever you are ready"],
      }[dispatchStatus]
    : undefined;
  const headline = feedback?.[0] || (pending
    ? (zh ? "Argus 正在处理" : "Argus is working")
    : sent ? (zh ? "已发送给 Argus" : "Sent to Argus") : (zh ? "交给 Argus" : "Ask Argus"));
  const detail = feedback?.[1] || (pending
    ? pendingLabel || (zh ? "正在处理你的消息…" : "Processing your message…")
    : sent ? (zh ? "点此继续对话" : "Tap to keep the conversation going")
      : (zh ? "描述目标，看它变成成果" : "Turn your next idea into a result"));
  const state = dispatchStatus || (pending ? "working" : sent ? "sent" : "idle");
  return (
    <div
      ref={dock}
      className="map-composer-dock map-island-dock"
      data-compact={compact}
      data-state={state}
      data-pending={pending}
      onPointerEnter={(event) => {
        if (event.pointerType === "mouse") setHovered(true);
      }}
      onPointerLeave={() => setHovered(false)}
      style={{ "--map-editor-height": `${inputHeight}px` } as CSSProperties}
      onTransitionEnd={(event) => {
        if (event.target === dock.current && event.propertyName === "width") resizeInput();
      }}
    >
      {refs.length > 0 && (
        <div className="map-reference-chips">
          {refs.map((ref, i) => (
            <span
              key={`${ref.task_id}:${ref.step_id}:${i}`}
              title={`${ref.source} · ${ref.task_id} ${ref.step_id || ""}`}
            >
              <span>
                {zh ? "引用" : "Reference"} · {ref.step_title || ref.task_title}
              </span>
              <button
                aria-label={zh ? "移除引用" : "Remove reference"}
                onClick={() =>
                  onChange(
                    refs
                      .filter((_, n) => i !== n)
                      .map(referenceText)
                      .join("") + text,
                  )
                }
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
      {!!attachments.length && (
        <div
          className="map-attachment-tray nowheel"
          role="group"
          aria-label={zh ? "待发送附件" : "Selected attachments"}
        >
          {attachments.map((file, i) => (
            <ComposerAttachmentChip
              key={`${file.name}:${file.lastModified}:${i}`}
              file={file}
              disabled={pending}
              removeLabel={t("chat.attachRemove", { name: file.name })}
              onRemove={() => {
                onAttachmentsChange(attachments.filter((f) => f !== file));
                setAttachmentNotice("");
              }}
            />
          ))}
        </div>
      )}
      {attachmentNotice && (
        <div className="map-attachment-notice nowheel" role="alert">
          {attachmentNotice}
        </div>
      )}
      <div className="map-composer map-island-surface">
        <button
          type="button"
          className="map-composer-brand map-attach"
          aria-label={t("chat.attach")}
          title={t("chat.attach")}
          aria-hidden={compact}
          tabIndex={compact ? -1 : 0}
          disabled={pending && !compact}
          onClick={() => fileInput.current?.click()}
        >
          <ArgusMark size={25} />
        </button>
        <button
          type="button"
          className="map-island-launch"
          aria-label={zh ? "打开消息输入" : "Open message composer"}
          aria-expanded={!compact}
          aria-controls={editorId}
          aria-hidden={!compact}
          tabIndex={compact ? 0 : -1}
          onClick={open}
        >
          <span className="map-island-copy">
            <strong>{headline}</strong>
            <small title={detail}>{detail}</small>
          </span>
          <span className="map-island-indicator" aria-hidden="true">
            {state === "working" || state === "launching"
              ? <span className="map-island-wave"><i /><i /><i /></span>
              : state === "sent" || state === "task" || state === "message"
                ? <Check size={16} /> : <CornerDownLeft size={15} />}
          </span>
        </button>
        {compact && pending && (
          <button type="button" className="map-island-stop" onClick={onCancel}
            aria-label={zh ? "停止等待" : "Stop waiting"}>
            <Square size={13} />
          </button>
        )}
        <form
          id={editorId}
          className="map-composer-editor"
          aria-hidden={compact}
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <input
            ref={fileInput}
            type="file"
            multiple
            accept={MESSAGE_ATTACHMENT_ACCEPT}
            hidden
            disabled={pending}
            onChange={(event) => {
              addFiles(Array.from(event.target.files || []));
              event.target.value = "";
            }}
          />
          <textarea
            ref={input}
            rows={1}
            tabIndex={compact ? -1 : 0}
            value={text}
            aria-label={zh ? "给 Argus 发送消息" : "Message Argus"}
            placeholder={zh ? "告诉 Argus，你想完成什么…" : "What would you like Argus to do?"}
            onFocus={() => setFocused(true)}
            onChange={(e) => {
              setSent(false);
              onChange(refs.map(referenceText).join("") + e.target.value);
            }}
            onPaste={(event) => {
              const files = extractFilesFromDataTransfer(event.clipboardData);
              if (files.length) {
                event.preventDefault();
                addFiles(files);
              }
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape" && !isImeComposing(e) && !value.trim() && !attachments.length) {
                e.preventDefault();
                e.stopPropagation();
                collapse();
              }
              if (e.key === "Enter" && !e.shiftKey && !isImeComposing(e)) {
                e.preventDefault();
                void submit();
              }
            }}
          />
          <div className="map-island-toolbar">
            {overview && !value.trim() && !attachments.length && <button type="button" className="map-island-collapse" tabIndex={compact ? -1 : 0} onClick={collapse} aria-label={zh ? "收起消息输入" : "Collapse message composer"}><ChevronDown size={15} /></button>}
            <span className="map-island-key-hint" aria-hidden="true">{zh ? "Enter 发送" : "Enter to send"}</span>
            {onRouteOverrideChange && <select className="map-route-select" tabIndex={compact ? -1 : 0} aria-label={t('chat.routeLabel')} title={t('chat.routeHint')} value={routeOverride} disabled={pending} onChange={(event) => onRouteOverrideChange(event.target.value as MessageRouteOverride)}>
              <option value="auto">{t('chat.routeAuto')}</option><option value="task">{t('chat.routeTask')}</option><option value="chat">{t('chat.routeChat')}</option>
            </select>}
            {pending ? (
              <button
                type="button"
                onClick={(event) => {
                  // Cancellation can turn this same DOM button into Submit
                  // before the click's default action runs.
                  event.preventDefault();
                  onCancel();
                }}
                tabIndex={compact ? -1 : 0}
                aria-label={zh ? "停止等待" : "Stop waiting"}
                className="map-send is-pending"
              >
                <Square size={15} />
              </button>
            ) : (
              <button
                type="submit"
                tabIndex={compact ? -1 : 0}
                disabled={!text.trim()}
                aria-label={zh ? "发送消息" : "Send message"}
                className="map-send"
              >
                <ArrowUp size={20} />
              </button>
            )}
          </div>
        </form>
      </div>
      <span className="map-composer-caption" role="status">
        {feedback
          ? `${feedback[0]} · ${feedback[1]}`
          : pending
            ? pendingLabel || (zh ? "Argus 正在处理…" : "Argus is responding…")
          : sent
            ? zh
              ? "已发送"
              : "Sent"
            : historical
              ? `${zh ? "发送至" : "Send to"} ${sessionName}`
              : ""}
      </span>
    </div>
  );
}
