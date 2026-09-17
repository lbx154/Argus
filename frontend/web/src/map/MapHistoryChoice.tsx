import { Modal } from "../components/Modal";
import type { MapHistoryInfo, MapSelection } from "./incremental";

export function MapHistoryChoice({ open, info, zh, onChoose, readOnly = false }: {
  open: boolean;
  info: MapHistoryInfo;
  zh: boolean;
  readOnly?: boolean;
  onChoose: (selection: MapSelection) => void;
}) {
  return (
    <Modal open={open} onClose={() => onChoose({ mode: "off" })}
      label={zh ? "选择地图加载范围" : "Choose map history"} width="max-w-lg">
      <div className="map-history-choice">
        <h2>{zh ? "选择地图加载范围" : "Choose map history"}</h2>
        <p>{zh
          ? `本会话有 ${info.task_count} 个任务，历史记录约 ${(info.event_bytes / 1024 / 1024).toFixed(1)} MB。`
          : `This session has ${info.task_count} tasks and about ${(info.event_bytes / 1024 / 1024).toFixed(1)} MB of history.`}</p>
        <p>{readOnly ? (zh ? "只读模式只加载历史记录，不调用模型。较长历史需要一些加载时间。" : "Read-only mode loads records without model calls. Long histories take time to load.") : zh
          ? "加载较长历史需要一些时间。生成卡片摘要与关系说明会调用已配置的模型，并消耗额外 Token。已有摘要会优先复用。"
          : "Loading a long history takes time. Card summaries and relationship descriptions use your configured model and consume additional tokens. Existing summaries are reused."}</p>
        <div className="map-history-options">
          <button type="button" onClick={() => onChoose({ mode: "current", since: info.current_task_ts, eventSince: info.current_event_ts, taskId: info.current_task_id || undefined })}>
            <strong>{zh ? "从当前进度加载" : "Start at current progress"}</strong>
            <span>{zh ? "推荐 · 加载当前任务及后续进度，保留相关连接" : "Recommended · Load current and future work with its connections"}</span>
          </button>
          <button type="button" onClick={() => onChoose({ mode: "full" })}>
            <strong>{zh ? "从头加载" : "Load from the beginning"}</strong>
            <span>{readOnly
              ? (zh ? "分批加载历史记录，查看已有说明" : "Load history in pages and view existing explanations")
              : (zh ? "分批加载历史记录；打开卡片阅读时，再整理它的说明" : "Load history in pages; prepare an explanation when you open a card to read")}</span>
          </button>
          <button type="button" onClick={() => onChoose({ mode: "off" })}>
            <strong>{zh ? "不开启地图模式" : "Keep map mode off"}</strong>
            <span>{zh ? "不加载地图，也不生成摘要" : "Do not load the map or generate summaries"}</span>
          </button>
        </div>
        <small>{zh ? "选择仅用于本会话，可随时更改加载范围。" : "This choice applies to this session and can be changed later."}</small>
      </div>
    </Modal>
  );
}
