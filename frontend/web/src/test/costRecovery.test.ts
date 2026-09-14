import { describe, expect, it } from "vitest";
import { renderLine } from "../../../core/src/eventRender";
import { outcomeLabels, statusLabel } from "../lib/enumLabels";
import { mergeMapCopy, type MapCopy } from "../map/presentation";

const renderEvent = (event: Parameters<typeof renderLine>[0]) => renderLine(event, {
  locale: "en", density: "full", showReasoning: false, unknownEventPolicy: "hide",
});

describe("unknown cost recovery presentation", () => {
  it("distinguishes pending provider usage from an exhausted monetary cap", () => {
    const waiting = renderEvent({ type: "budget.reservation.denied", reason: "unresolved provider cost: call=map" });
    expect(waiting?.text).toContain("awaits reconciliation");
    expect(waiting?.text).not.toContain("budget denied");
    const exhausted = renderEvent({ type: "budget.reservation.denied", reason: "global daily budget exhausted" });
    expect(exhausted?.text).toContain("budget denied");
    expect(statusLabel("paused_cost", (key) => key)).toBe("label.outcome.costUnreconciled");
    expect(outcomeLabels({ execution_status: "paused", interruption_kind: "cost_unreconciled" }, (key) => key))
      .toContain("label.outcome.costUnreconciled");
  });

  it("does not describe an acknowledged risk provision as a settled invoice", () => {
    expect(renderEvent({ type: "budget.unpriced.acknowledged", liability_usd: 5 })?.text)
      .toContain("original usage remains pending");
  });

  it("retains published cards on generation failure and clears a later resolved error", () => {
    const previous: MapCopy = {
      cards: { a: { title: "Verified record", summary: "Evidence", detail: "Details", generated_at: 1 } },
      relations: [], cache_revision: 1,
    };
    const failed = mergeMapCopy(previous, {
      cards: {}, relations: [], cache_revision: 1,
      generation_error: { code: "map_timeout", message: "Fallback" }, retry_after: 300,
    });
    expect(failed.cards.a).toEqual(previous.cards.a);
    expect(failed.generation_error?.code).toBe("map_timeout");
    expect(mergeMapCopy(failed, { ...previous, generation_error: null }).generation_error).toBeNull();
  });
});
