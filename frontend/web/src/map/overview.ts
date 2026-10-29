import type { OperatorDecisionCard } from "../../../core/src/decisions";
import type { MapTask } from "./model";

export interface OverviewDecision {
  key: string;
  title: string;
  taskId?: string;
}

export interface ProjectOverview {
  decisions: OverviewDecision[];
}

const oneLine = (text: string, size: number) => {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > size ? `${flat.slice(0, size)}…` : flat;
};

/** What waits on the person (等你决定), read only from what the records already hold. */
export function projectOverview({ tasks, decisions }: {
  tasks: MapTask[];
  decisions: OperatorDecisionCard[];
}): ProjectOverview {
  const work = tasks.filter((task) => task.turn_kind !== "qa" && task.kind !== "turn");
  const pending: OverviewDecision[] = [];
  const asked = new Set<string>();
  for (const card of decisions) {
    asked.add(card.item_id);
    pending.push({ key: `decision:${card.id}`, taskId: card.item_id || undefined,
      title: oneLine(card.question || card.title, 80) });
  }
  for (const task of work) {
    if (task.pending_question && !asked.has(task.id))
      pending.push({ key: `question:${task.id}`, taskId: task.id, title: oneLine(task.pending_question, 80) });
  }
  return { decisions: pending };
}
