import { useEffect, useRef, useState } from "react";
import { ArrowUp, Square, X } from "lucide-react";
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

export interface MapComposerProps {
  value: string;
  onChange: (text: string) => void;
  onSend: (text: string, attachments?: File[]) => Promise<boolean>;
  attachments: File[];
  onAttachmentsChange: (files: File[]) => void;
  pending: boolean;
  onCancel: () => void;
  focusSignal: number;
  sessionName: string;
  historical: boolean;
  zh: boolean;
  overview?: boolean;
}
export function MapComposer({
  value,
  onChange,
  onSend,
  attachments,
  onAttachmentsChange,
  pending,
  onCancel,
  focusSignal,
  sessionName,
  historical,
  zh,
  overview = false,
}: MapComposerProps) {
  const { t } = useI18n();
  const input = useRef<HTMLTextAreaElement>(null);
  const dock = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const submitting = useRef(false);
  const mounted = useRef(true);
  const sentTimer = useRef<ReturnType<typeof setTimeout>>();
  const [attachmentNotice, setAttachmentNotice] = useState("");
  const [sent, setSent] = useState(false);
  const [focused, setFocused] = useState(false);
  const compact =
    overview &&
    !focused &&
    !value.trim() &&
    !attachments.length &&
    !pending &&
    !attachmentNotice &&
    !sent;
  const { refs, text } = splitDraft(value);
  useEffect(() => {
    mounted.current = true;
    // Removing a focused chip can skip its blur event; the next document focus
    // still needs to release the expanded composer.
    const focus = (event: FocusEvent) =>
      setFocused(
        Boolean(
          dock.current?.contains(
            (event.type === "focusout"
              ? event.relatedTarget
              : event.target) as Node | null,
          ),
        ),
      );
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
  useEffect(() => {
    if (input.current) {
      input.current.style.height = "0px";
      input.current.style.height = `${Math.min(132, Math.max(28, input.current.scrollHeight))}px`;
    }
  }, [text]);
  const submit = async () => {
    if (!text.trim() || pending || submitting.current) return;
    submitting.current = true;
    try {
      if ((await onSend(value, attachments)) && mounted.current) {
        setAttachmentNotice("");
        setSent(true);
        clearTimeout(sentTimer.current);
        sentTimer.current = setTimeout(() => setSent(false), 3500);
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
  return (
    <div ref={dock} className="map-composer-dock" data-compact={compact}>
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
      <form
        className="map-composer"
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
        <button
          type="button"
          className="map-composer-brand map-attach"
          aria-label={t("chat.attach")}
          title={t("chat.attach")}
          disabled={pending}
          onClick={() => fileInput.current?.click()}
        >
          <ArgusMark size={24} />
        </button>
        <textarea
          ref={input}
          rows={1}
          value={text}
          aria-label={zh ? "给 Argus 发送消息" : "Message Argus"}
          placeholder={
            compact
              ? zh
                ? "发送消息…"
                : "Message Argus…"
              : zh
                ? "告诉 Argus 下一步怎么做…"
                : "Tell Argus what to do next…"
          }
          onChange={(e) =>
            onChange(refs.map(referenceText).join("") + e.target.value)
          }
          onPaste={(event) => {
            const files = extractFilesFromDataTransfer(event.clipboardData);
            if (files.length) {
              event.preventDefault();
              addFiles(files);
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape" && !value.trim()) input.current?.blur();
            if (e.key === "Enter" && !e.shiftKey && !isImeComposing(e)) {
              e.preventDefault();
              void submit();
            }
          }}
        />
        {pending ? (
          <button
            type="button"
            onClick={onCancel}
            aria-label={zh ? "停止等待" : "Stop waiting"}
            className="map-send is-pending"
          >
            <Square size={15} />
          </button>
        ) : (
          <button
            type="submit"
            disabled={!text.trim()}
            aria-label={zh ? "发送消息" : "Send message"}
            className="map-send"
          >
            <ArrowUp size={20} />
          </button>
        )}
      </form>
      <span className="map-composer-caption" role="status">
        {pending
          ? zh
            ? "Argus 正在处理…"
            : "Argus is responding…"
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
