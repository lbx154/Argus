import type { MapEvent } from "./model";

export function completionScope(event: MapEvent | undefined, zh: boolean): string {
  if (event?.type !== "life.mission.completed" ||
    !(event.overall_complete === false || event.campaign_continues === true)) return "";
  if (event.outcome?.execution_status === "completed" && event.outcome.review_status === "done") return "";
  return zh
    ? `当次执行已结束；当时总体目标尚未完成${event.campaign_continues === true ? "，仍需后续工作" : ""}。`
    : `This execution ended; the overall goal was not complete at that time${event.campaign_continues === true ? " and further work remained" : ""}.`;
}
