import { describe, expect, it } from "vitest";
import { renderLine } from "../../../core/src/eventRender";
import { statusLabel } from "../lib/enumLabels";

const renderEvent = (event: Parameters<typeof renderLine>[0]) => renderLine(event, {
  locale: "en", density: "full", showReasoning: false, unknownEventPolicy: "hide",
});

describe("unknown cost recovery presentation", () => {
  it("distinguishes pending provider usage from an exhausted monetary cap", () => {
    const waiting = renderEvent({ type: "budget.unpriced.blocked", call_id: "map", reason: "1 call awaiting usage" });
    expect(waiting?.text).toContain("awaits reconciliation");
    expect(waiting?.text).not.toContain("budget denied");
    const exhausted = renderEvent({ type: "budget.reservation.denied", reason: "global daily budget exhausted" });
    expect(exhausted?.text).toContain("budget denied");
    // A park reads as paused whatever its reason; the reason has its own label.
    expect(statusLabel("parked", (key) => key)).toBe("label.status.paused");
    expect(statusLabel("paused_cost", (key) => key)).toBe("label.status.paused");
  });

  it("does not describe an acknowledged risk provision as a settled invoice", () => {
    expect(renderEvent({ type: "budget.unpriced.acknowledged", liability_usd: 5 })?.text)
      .toContain("original usage remains pending");
  });
});
