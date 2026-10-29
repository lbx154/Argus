import { useState, type ReactNode } from "react";
import { STEP_STATUS, stepMark, type RouteView } from "./canvas";
import type { ProjectOverview } from "./overview";

const SHOWN = 3;

/** The strip at the top of the canvas: the team's state, the required steps, and
 * what waits on the person (等你决定), each finding its card on the canvas. */
export function MapOverview({ overview, zh, team, route, onFocusTask, onAnswer }: {
  overview: ProjectOverview;
  /** The person's required steps: "必经步骤 2/5" and the next one open. */
  route?: RouteView;
  zh: boolean;
  /** The team status line with 暂停团队 / 继续, shared with the conversation. */
  team?: ReactNode;
  /** Focuses the task's card; false when the card is not on the canvas. */
  onFocusTask: (taskId: string) => boolean;
  onAnswer: () => void;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const more = (key: string, total: number) => total > SHOWN && (
    <button type="button" className="map-overview-more" onClick={() => setOpen((value) => ({ ...value, [key]: !value[key] }))}>
      {open[key] ? (zh ? "收起" : "Less") : zh ? `还有 ${total - SHOWN} 项` : `${total - SHOWN} more`}
    </button>
  );
  const visible = <T,>(key: string, items: T[]) => (open[key] ? items : items.slice(0, SHOWN));
  const { decisions } = overview;
  return (
    <section className="map-overview" aria-label={zh ? "项目总览" : "Project overview"} data-testid="map-overview">
      {team}
      <dl>
        {route && route.total > 0 && (() => {
          const next = route.steps.find((step) => step.status !== "done");
          return (
            <div data-row="steps">
              <dt>{zh ? "必经步骤" : "Required steps"}</dt>
              <dd className="map-overview-steps" data-testid="overview-steps">
                <b>{route.done}/{route.total}</b>
                {next && (
                  <span title={next.text}>
                    {zh ? "下一步 " : "Next "}{stepMark(next.number) || "·"} {next.text}
                    <small> · {STEP_STATUS[next.status][zh ? 0 : 1]}</small>
                  </span>
                )}
              </dd>
            </div>
          );
        })()}
        {decisions.length > 0 && (
          <div data-row="decide" className="is-attention">
            <dt>{zh ? "等你决定" : "Your call"}</dt>
            <dd>
              {visible("decide", decisions).map((item) => (
                <button type="button" key={item.key} className="map-overview-item" data-testid="overview-decision"
                  title={item.title} onClick={() => { if (!item.taskId || !onFocusTask(item.taskId)) onAnswer(); }}>
                  {item.title}
                </button>
              ))}{more("decide", decisions.length)}
            </dd>
          </div>
        )}
      </dl>
    </section>
  );
}
