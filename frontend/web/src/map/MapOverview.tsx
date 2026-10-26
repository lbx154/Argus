import { useState, type ReactNode } from "react";
import type { DeliveryReceipt } from "../../../core/src/types";
import { LaunchStartButton } from "../components/LaunchPanel";
import { STEP_STATUS, stepMark, type RouteView } from "./canvas";
import type { ProjectOverview } from "./overview";

const SHOWN = 3;

/** The strip at the top of the canvas: the team's state, then 目标 / 主要成果 /
 * 已验证 / 等你决定. Every item leads somewhere: a result opens, a check or a
 * question finds its card on the canvas. */
export function MapOverview({ overview, zh, team, readOnly, route, onOpenReceipt, onOpenArtifact, onFocusTask, onAnswer, onStartLaunch }: {
  overview: ProjectOverview;
  /** The person's required steps: "必经步骤 2/5" and the next one open. */
  route?: RouteView;
  zh: boolean;
  /** The team status line with 暂停团队 / 继续, shared with the conversation. */
  team?: ReactNode;
  readOnly: boolean;
  onOpenReceipt: (receipt: DeliveryReceipt, path?: string) => void;
  onOpenArtifact: (path: string) => void;
  /** Focuses the task's card; false when the card is not on the canvas. */
  onFocusTask: (taskId: string) => boolean;
  onAnswer: () => void;
  onStartLaunch?: () => Promise<void>;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const more = (key: string, total: number) => total > SHOWN && (
    <button type="button" className="map-overview-more" onClick={() => setOpen((value) => ({ ...value, [key]: !value[key] }))}>
      {open[key] ? (zh ? "收起" : "Less") : zh ? `还有 ${total - SHOWN} 项` : `${total - SHOWN} more`}
    </button>
  );
  const visible = <T,>(key: string, items: T[]) => (open[key] ? items : items.slice(0, SHOWN));
  const { goal, results, checks, decisions } = overview;
  return (
    <section className="map-overview" aria-label={zh ? "项目总览" : "Project overview"} data-testid="map-overview">
      {team}
      <dl>
        <div data-row="goal">
          <dt>{zh ? "目标" : "Goal"}</dt>
          <dd className="map-overview-goal" title={goal}>{goal || (zh ? "还没有目标" : "No goal yet")}</dd>
        </div>
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
        <div data-row="results">
          <dt>{zh ? "主要成果" : "Results"}</dt>
          <dd>{results.length ? <>
            {visible("results", results).map((item) => (
              <button type="button" key={item.key} className="map-overview-item" data-testid="overview-result"
                title={item.receipt ? `${item.receipt.title}\n${item.path}` : item.path}
                onClick={() => item.receipt ? onOpenReceipt(item.receipt, item.path) : onOpenArtifact(item.path)}>
                {item.title}{item.reviewed ? <small className="is-ok">{zh ? "已审查" : "Reviewed"}</small> : null}
              </button>
            ))}{more("results", results.length)}
          </> : <span className="map-overview-empty">{zh ? "还没有交付的成果" : "Nothing delivered yet"}</span>}</dd>
        </div>
        <div data-row="checks">
          <dt>{zh ? "已验证" : "Verified"}</dt>
          <dd>{checks.length ? <>
            {visible("checks", checks).map((item) => (
              <button type="button" key={item.key} className="map-overview-item is-check" data-testid="overview-check"
                title={item.basis} onClick={() => onFocusTask(item.taskId)}>
                <b>{item.title}</b><span>{zh ? "独立审查：" : "Independent review: "}{item.basis}</span>
              </button>
            ))}{more("checks", checks.length)}
          </> : <span className="map-overview-empty">{zh ? "还没有经过独立审查的结果" : "No result has passed independent review yet"}</span>}</dd>
        </div>
        {decisions.length > 0 && (
          <div data-row="decide" className="is-attention">
            <dt>{zh ? "等你决定" : "Your call"}</dt>
            <dd>
              {visible("decide", decisions).map((item) => item.kind === "launch" ? (
                <span key={item.key} className="map-overview-item is-launch" data-testid="overview-decision">
                  <button type="button" className="map-overview-link" onClick={() => item.taskId && onFocusTask(item.taskId)}>
                    {zh ? "计划已排好，等你确认开始：" : "Plan ready, waiting for your go-ahead: "}{item.title}
                  </button>
                  {!readOnly && onStartLaunch ? <LaunchStartButton onStart={onStartLaunch} zh={zh} className="map-overview-start" /> : null}
                </span>
              ) : (
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
