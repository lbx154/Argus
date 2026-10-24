import type { DeliveryReceipt, LaunchHold } from "../../../core/src/types";
import type { OperatorDecisionCard } from "../../../core/src/decisions";
import { deliveryFiles } from "../components/deliveryPresentation";
import type { MapEvent, MapTask } from "./model";

/** One delivered, openable thing: the newest receipt for a task. */
export interface OverviewResult {
  key: string;
  title: string;
  path: string;
  /** Absent for a reviewed file whose receipt was held back. */
  receipt?: DeliveryReceipt;
  taskId: string;
  ts: number;
  /** An independent reviewer settled this receipt's check. */
  reviewed: boolean;
}

/** A result that passed an independent review, and the reviewer's own words for why. */
export interface OverviewCheck {
  key: string;
  taskId: string;
  title: string;
  basis: string;
  ts: number;
}

export interface OverviewDecision {
  key: string;
  kind: "launch" | "question";
  title: string;
  taskId?: string;
}

export interface ProjectOverview {
  goal: string;
  results: OverviewResult[];
  checks: OverviewCheck[];
  decisions: OverviewDecision[];
}

const PASSED = new Set(["done", "pass", "passed", "approved", "accepted", "ok"]);
const oneLine = (text: string, size: number) => {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > size ? `${flat.slice(0, size)}…` : flat;
};
const basename = (path: string) => path.replace(/\\/g, "/").split("/").filter(Boolean).at(-1) || path;

/** The four things a person asks first (目标 / 主要成果 / 已验证 / 等你决定),
 * read only from what the records already hold: no model call, no guessing.
 * A result stays a result after later work stops: what was delivered and
 * reviewed does not become undone because another task was aborted. */
export function projectOverview({ goal, tasks, events, receipts, decisions, launchHold, zh }: {
  goal?: string;
  tasks: MapTask[];
  events: MapEvent[];
  receipts: DeliveryReceipt[];
  decisions: OperatorDecisionCard[];
  launchHold?: LaunchHold | null;
  zh: boolean;
}): ProjectOverview {
  const work = tasks.filter((task) => task.turn_kind !== "qa" && task.kind !== "turn");
  const first = [...work].sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0))[0];
  const goalText = oneLine(goal || first?.objective || first?.title || "", 140);
  const titles = new Map(tasks.map((task) => [task.id, task.title]));

  // Newest receipt per task: a re-delivery is the same result's next version.
  const newest = new Map<string, DeliveryReceipt>();
  for (const receipt of [...receipts].sort((a, b) => b.delivered_at - a.delivered_at)) {
    const key = receipt.item_id || receipt.delivery_id;
    if (!newest.has(key) && deliveryFiles(receipt).length) newest.set(key, receipt);
  }
  const results = [...newest.values()].map((receipt): OverviewResult => {
    const file = deliveryFiles(receipt)[0];
    return {
      key: receipt.delivery_id,
      title: oneLine(file.label || basename(file.path), 60),
      path: file.path,
      receipt,
      taskId: receipt.item_id,
      ts: receipt.delivered_at,
      reviewed: receipt.review_status === "done" && receipt.review_source === "reviewer",
    };
  });
  // The latest independent review of each task decides whether it stands.
  const lastReview = new Map<string, MapEvent>();
  for (const event of [...events].sort((a, b) => a.ts - b.ts)) {
    if (event.type !== "round.review.completed" || event.role !== "reviewer") continue;
    if (event.backend_unavailable || event.review_skipped) continue;
    lastReview.set(event.item_id, event);
  }
  // A task the Reviewer accepted produced its files even when the receipt was
  // held back for later work (rule 6: a reviewed result shows as a result,
  // whatever happened to the work after it).
  const delivered = new Set(results.flatMap((result) => [result.taskId, result.path]));
  const accepted = new Map<string, MapEvent>();
  for (const event of events) {
    // Older records attribute the completion to the engineer layer even when
    // the independent reviewer passed the work: the reviewer's verdict on the
    // task is the more reliable source, so it decides.
    const passed = event.role === "reviewer" || PASSED.has(String(lastReview.get(event.item_id)?.status || "").toLowerCase());
    if (event.type === "life.mission.completed" && passed && event.delivery_candidates?.length
      && (accepted.get(event.item_id)?.ts ?? -Infinity) <= event.ts) accepted.set(event.item_id, event);
  }
  for (const [taskId, event] of accepted) {
    if (delivered.has(taskId)) continue;
    for (const path of event.delivery_candidates!) {
      if (delivered.has(path)) continue;
      delivered.add(path);
      results.push({ key: `reviewed:${event.id}:${path}`, title: oneLine(basename(path), 60), path, taskId, ts: event.ts, reviewed: true });
    }
  }
  results.sort((a, b) => b.ts - a.ts);

  const checks: OverviewCheck[] = [];
  for (const [taskId, event] of lastReview) {
    if (!PASSED.has(String(event.status || "").toLowerCase())) continue;
    checks.push({
      key: event.id, taskId, ts: event.ts,
      title: oneLine(titles.get(taskId) || event.title || taskId, 60),
      basis: oneLine(event.text || (zh ? "审查者通过，未写理由" : "Passed; the reviewer gave no reason"), 160),
    });
  }
  // A receipt the reviewer settled counts even when the review's own record
  // fell outside the map's window.
  for (const result of results) {
    if (!result.reviewed || !result.receipt || lastReview.has(result.taskId)) continue;
    checks.push({
      key: `receipt:${result.key}`, taskId: result.taskId, ts: result.ts,
      title: oneLine(titles.get(result.taskId) || result.receipt.title || result.title, 60),
      basis: oneLine(result.receipt.summary || (zh ? "独立审查通过" : "Passed independent review"), 160),
    });
  }
  checks.sort((a, b) => b.ts - a.ts);

  const pending: OverviewDecision[] = [];
  if (launchHold) pending.push({
    key: `launch:${launchHold.item_id}`, kind: "launch", taskId: launchHold.item_id || undefined,
    title: oneLine(launchHold.title || launchHold.request || (zh ? "计划已排好" : "Plan ready"), 80),
  });
  const asked = new Set<string>();
  for (const card of decisions) {
    asked.add(card.item_id);
    pending.push({ key: `decision:${card.id}`, kind: "question", taskId: card.item_id || undefined,
      title: oneLine(card.question || card.title, 80) });
  }
  for (const task of work) {
    if (task.pending_question && !asked.has(task.id))
      pending.push({ key: `question:${task.id}`, kind: "question", taskId: task.id, title: oneLine(task.pending_question, 80) });
  }
  return { goal: goalText, results, checks, decisions: pending };
}
