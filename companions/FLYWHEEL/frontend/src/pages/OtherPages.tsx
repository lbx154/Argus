import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  BellRing,
  Boxes,
  BrainCircuit,
  Check,
  CheckCircle2,
  CircleStop,
  Cloud,
  Code2,
  Cpu,
  Download,
  ExternalLink,
  Eye,
  FileCheck2,
  GitBranch,
  Github,
  KeyRound,
  Laptop,
  LockKeyhole,
  MessageSquareReply,
  Network,
  Palette,
  Plus,
  Radio,
  RefreshCw,
  Save,
  Server,
  Settings2,
  Shield,
  Sparkles,
  Terminal,
  TestTube2,
  TriangleAlert,
  X,
} from "lucide-react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useApp } from "../App";
import {
  apiUrl,
  createCandidateCampaign,
  createOutcome,
  createOutcomeFollowUp,
  downloadOutcomeTrainingSample,
  inspectReleaseCandidate,
  loadIdeationRun,
  loadIdeationRuns,
  loadOutcomes,
  loadReleaseStatus,
  stageReleaseCandidate,
  type OutcomeRecord,
  type ReleaseStatus,
} from "../api/client";
import type { Approval, Idea, IdeationCandidate, IdeationRun } from "../types";
import {
  Button,
  EmptyState,
  Notice,
  PageHeader,
  ScoreRing,
  StatusPill,
} from "../components/ui";
import { AppearancePanel } from "../components/AppearancePanel";
import { useI18n } from "../lib/preferences";

type SeedBaseline = {
  idea: Idea;
  venueId: string;
  venueKey?: string;
  deadlineId?: number;
  venueLabel: string;
};

type ConditionedDirection = {
  idea: Idea;
  candidate: IdeationCandidate;
  run: IdeationRun;
};

const compactResearchValue = (value: unknown): string => {
  if (value === null || value === undefined || value === "") return "";
  if (Array.isArray(value)) return value.map(compactResearchValue).filter(Boolean).join(" · ");
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${key}: ${compactResearchValue(item)}`)
      .filter((item) => !item.endsWith(": "))
      .join(" · ");
  }
  return String(value);
};

const evidenceRef = (
  value: unknown,
  index: number,
): Idea["sources"][number] => {
  if (typeof value === "string") {
    return { kind: "arXiv", label: value, age: `#${index + 1}` };
  }
  const record = value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
  const rawKind = String(record.kind ?? record.source ?? record.type ?? "evidence").toLowerCase();
  const kind = rawKind.includes("github")
    ? "GitHub"
    : rawKind.includes("openreview")
      ? "OpenReview"
      : "arXiv";
  const label = String(
    record.title ?? record.label ?? record.citation ?? record.url ?? record.id ?? `Evidence ${index + 1}`,
  );
  const age = String(record.date ?? record.published_at ?? record.updated_at ?? `#${index + 1}`);
  return { kind, label, age };
};

const humanDimension = (candidate: IdeationCandidate, key: string): number => {
  const values = candidate.labels
    .map((label) => label.dimensions[key])
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  return values.length
    ? Math.round((values.reduce((sum, value) => sum + value, 0) / values.length) * 10) / 10
    : -1;
};

const directionAsIdea = (candidate: IdeationCandidate, run: IdeationRun): Idea => ({
  id: `candidate:${candidate.id}`,
  title: candidate.title,
  thesis: String(
    candidate.candidate.core_hypothesis ??
      candidate.candidate.problem_gap ??
      candidate.candidate.mechanism ??
      "",
  ),
  field: run.venue_name ?? run.venue_key ?? "",
  novelty: humanDimension(candidate, "novelty_evidence"),
  feasibility: humanDimension(candidate, "resource_fit"),
  freshness: "conditioned",
  delta: String(candidate.candidate.differentiation_claim ?? ""),
  sources: candidate.evidence_refs.map(evidenceRef),
  compute: compactResearchValue(candidate.candidate.estimated_resources),
  risk: compactResearchValue(candidate.candidate.risks),
});

export function IdeaRadarPage() {
  const { data, act, mode, refresh, toast } = useApp();
  const { t } = useI18n();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [selected, setSelected] = useState<Idea | undefined>();
  const [collection, setCollection] = useState<"conditioned" | "baseline">("conditioned");
  const [conditionedRuns, setConditionedRuns] = useState<IdeationRun[]>([]);
  const [directionsLoading, setDirectionsLoading] = useState(mode === "live");
  const [directionsError, setDirectionsError] = useState("");
  const [promptBusy, setPromptBusy] = useState(false);
  const [promptError, setPromptError] = useState("");
  const [openReviewVenueId, setOpenReviewVenueId] = useState("");
  const [githubRepository, setGithubRepository] = useState("");
  const baselines = useMemo<SeedBaseline[]>(
    () =>
      Array.from(
        new Map(
          data.conferences
            .flatMap((venue) =>
              venue.ideas.map((idea) => ({
                idea,
                venueId: venue.id,
                venueKey: venue.venueKey,
                deadlineId: venue.deadlineId,
                venueLabel: venue.acronym,
              })),
            )
            .map((baseline) => [baseline.idea.id, baseline]),
        ).values(),
      ),
    [data.conferences],
  );
  const conditionedDirections = useMemo<ConditionedDirection[]>(
    () =>
      conditionedRuns.flatMap((run) =>
        (run.candidates ?? []).map((candidate) => ({
          candidate,
          run,
          idea: directionAsIdea(candidate, run),
        })),
      ),
    [conditionedRuns],
  );
  const allIdeas = useMemo(
    () =>
      collection === "conditioned"
        ? conditionedDirections.map((item) => item.idea)
        : baselines.map((item) => item.idea),
    [baselines, collection, conditionedDirections],
  );
  const selectedDirection = selected?.id.startsWith("candidate:")
    ? conditionedDirections.find((item) => item.idea.id === selected.id)
    : undefined;
  const selectedCampaign = selectedDirection
    ? data.campaigns.find((campaign) => campaign.candidateId === selectedDirection.candidate.id)
    : undefined;
  const selectedBaseline = selected && !selectedDirection
    ? baselines.find((item) => item.idea.id === selected.id)
    : undefined;
  const requestedIdeaId = searchParams.get("idea");
  const requestedCandidateId = searchParams.get("candidate");
  useEffect(() => {
    let active = true;
    if (mode !== "live") {
      setDirectionsLoading(false);
      setConditionedRuns([]);
      return () => {
        active = false;
      };
    }
    setDirectionsLoading(true);
    setDirectionsError("");
    loadIdeationRuns()
      .then(async (runs) => {
        const boundRuns = runs.filter(
          (run) => Boolean(run.candidate_artifact_sha256) || run.state === "awaiting_labels",
        );
        const settled = await Promise.allSettled(
          boundRuns.map((run) => loadIdeationRun(run.id)),
        );
        if (!active) return;
        const details = settled.flatMap((result) =>
          result.status === "fulfilled" ? [result.value] : [],
        );
        const failures = settled.filter((result) => result.status === "rejected");
        setConditionedRuns(details);
        if (failures.length) {
          const first = failures[0] as PromiseRejectedResult;
          setDirectionsError(
            `${t("ideas.load.partial", { count: failures.length })}: ${first.reason instanceof Error ? first.reason.message : String(first.reason)}`,
          );
        }
      })
      .catch((error) => {
        if (active) {
          setConditionedRuns([]);
          setDirectionsError(error instanceof Error ? error.message : String(error));
        }
      })
      .finally(() => {
        if (active) setDirectionsLoading(false);
      });
    return () => {
      active = false;
    };
  }, [mode, t]);
  useEffect(() => {
    const requestedDirection = requestedCandidateId
      ? conditionedDirections.find((item) => item.candidate.id === requestedCandidateId)
      : undefined;
    const requestedBaseline = requestedIdeaId
      ? baselines.find((item) => item.idea.id === requestedIdeaId)
      : undefined;
    if (requestedDirection) {
      setCollection("conditioned");
      setSelected(requestedDirection.idea);
      return;
    }
    if (requestedBaseline) {
      setCollection("baseline");
      setSelected(requestedBaseline.idea);
      return;
    }
    if (!directionsLoading && collection === "conditioned" && !conditionedDirections.length && baselines.length) {
      setCollection("baseline");
      setSelected(baselines[0].idea);
      return;
    }
    if (!allIdeas.length) {
      setSelected(undefined);
      return;
    }
    setSelected((current) =>
      allIdeas.find((idea) => idea.id === requestedIdeaId) ??
      allIdeas.find((idea) => idea.id === current?.id) ??
      allIdeas[0],
    );
  }, [allIdeas, baselines, collection, conditionedDirections, directionsLoading, requestedCandidateId, requestedIdeaId]);
  const syncSources = () => {
    const title = (selected?.title ?? "research agents").replaceAll('"', "");
    const requests: Array<{ kind: string; query: string }> = [
      { kind: "arxiv", query: `all:\"${title}\"` },
    ];
    if (openReviewVenueId.trim())
      requests.push({ kind: "openreview", query: openReviewVenueId.trim() });
    if (githubRepository.trim())
      requests.push({ kind: "github", query: githubRepository.trim() });
    return act("sources/sync", {
      idea_id:
        selectedBaseline?.idea.id && /^\d+$/.test(selectedBaseline.idea.id)
          ? Number(selectedBaseline.idea.id)
          : undefined,
      requests,
    });
  };
  const openPrompt = async () => {
    if (!selected) return;
    setPromptBusy(true);
    setPromptError("");
    try {
      if (selectedCampaign) {
        navigate(`/campaigns/${encodeURIComponent(selectedCampaign.id)}`);
        return;
      }
      if (!selectedDirection) {
        const venue = selectedBaseline?.venueKey ?? selectedBaseline?.venueId;
        const deadline = selectedBaseline?.deadlineId;
        navigate(
          `/context${venue ? `?venue=${encodeURIComponent(venue)}${deadline ? `&deadline=${deadline}` : ""}` : ""}`,
        );
        return;
      }
      if (mode !== "live") throw new Error(t("ideas.inspector.liveRequired"));
      const receipt = await createCandidateCampaign(selectedDirection.candidate.id);
      if (
        !receipt.id ||
        receipt.execution_state !== "idle" ||
        receipt.launch_triggered !== false ||
        !/^[0-9a-f]{64}$/i.test(receipt.candidate_prompt_sha256)
      ) {
        throw new Error(t("ideas.campaign.invalidReceipt"));
      }
      toast(
        t("ideas.campaign.created", {
          id: receipt.id.slice(0, 8),
          sha: receipt.candidate_prompt_sha256.slice(0, 12),
        }),
      );
      await refresh();
      navigate(`/campaigns/${encodeURIComponent(receipt.id)}`);
    } catch (error) {
      setPromptError(
        error instanceof Error
          ? error.message
          : t("ideas.campaign.failed"),
      );
    } finally {
      setPromptBusy(false);
    }
  };
  const selectedFreshness = String(selected?.freshness ?? "").toLowerCase();
  const selectedState = selectedDirection
    ? { tone: "iris" as const, label: t("ideas.state.conditioned") }
    : selectedFreshness.includes("collision")
      ? { tone: "warn" as const, label: t("ideas.state.baselineCollision") }
      : { tone: "neutral" as const, label: t("ideas.state.baseline") };
  const selectedCampaignState = selectedCampaign
    ? selectedCampaign.status === "idle" && selectedCampaign.launchTriggered
      ? t("ideas.campaign.unknown")
      : t(`ideas.campaign.${selectedCampaign.status}`)
    : t("ideas.campaign.none");
  const selectedCandidateRecord = selectedDirection?.candidate.candidate;
  const teamAdvantage = compactResearchValue(selectedCandidateRecord?.team_specific_advantage);
  const collisionTest = compactResearchValue(selectedCandidateRecord?.novelty_collision_test);
  const hasDelta = Boolean(selected?.delta.trim());
  const hasSources = Boolean(selected?.sources.length);
  const hasResource = Boolean(selected?.compute.trim() || selected?.risk.trim());
  return (
    <>
      <PageHeader
        eyebrow={t("page.ideas.eyebrow")}
        title={t("page.ideas.title")}
        actions={
          <>
            <details className="source-settings">
              <summary>
                <Settings2 size={14} />
                {t("ideas.source.settings")}
              </summary>
              <div className="source-query-config">
                <label>
                  <span>{t("ideas.source.openreview")}</span>
                  <input
                    value={openReviewVenueId}
                    onChange={(event) => setOpenReviewVenueId(event.target.value)}
                    placeholder="ICLR.cc/2027/Conference"
                  />
                </label>
                <label>
                  <span>{t("ideas.source.github")}</span>
                  <input
                    value={githubRepository}
                    onChange={(event) => setGithubRepository(event.target.value)}
                    pattern="[^/\s]+/[^/\s]+"
                    placeholder="owner/repository"
                  />
                </label>
              </div>
            </details>
            <Button
              kind="secondary"
              icon={<RefreshCw size={15} />}
              onClick={syncSources}
            >
              {t("action.syncSources")}
            </Button>
          </>
        }
      />
      {directionsError && (
        <Notice tone="warn" title={t("ideas.load.failed")}>{directionsError}</Notice>
      )}
      <div className="radar-grid">
        <section className="radar-feed">
          <div className="feed-toolbar">
            <div className="radar-collection-tabs" role="tablist" aria-label={t("ideas.collection.label")}>
              <button
                type="button"
                role="tab"
                aria-selected={collection === "conditioned"}
                className={collection === "conditioned" ? "active" : ""}
                onClick={() => {
                  setCollection("conditioned");
                  setSelected(conditionedDirections[0]?.idea);
                  setPromptError("");
                }}
              >
                {t("ideas.collection.conditioned")} <span>{conditionedDirections.length}</span>
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={collection === "baseline"}
                className={collection === "baseline" ? "active" : ""}
                onClick={() => {
                  setCollection("baseline");
                  setSelected(baselines[0]?.idea);
                  setPromptError("");
                }}
              >
                {t("ideas.collection.baseline")} <span>{baselines.length}</span>
              </button>
            </div>
            {directionsLoading && <span>{t("common.loading")}</span>}
          </div>
          {!allIdeas.length && !directionsLoading && (
            <EmptyState
              title={collection === "conditioned" ? t("ideas.empty.conditioned") : t("ideas.empty.baseline")}
              action={
                collection === "conditioned" ? (
                  <Link className="button primary" to="/context">
                    {t("ideas.baseline.generate")} <ArrowRight size={14} />
                  </Link>
                ) : undefined
              }
            />
          )}
          {allIdeas.map((idea) => {
            const freshness = String(idea.freshness).toLowerCase();
            const state = collection === "conditioned"
              ? { tone: "iris" as const, label: t("ideas.state.conditioned") }
              : freshness.includes("collision")
                ? { tone: "warn" as const, label: t("ideas.state.baselineCollision") }
                : { tone: "neutral" as const, label: t("ideas.state.baseline") };
            const hasScores = idea.novelty >= 0 || idea.feasibility >= 0;
            return (
              <button
                className={`radar-idea ${selected?.id === idea.id ? "selected" : ""}`}
                key={idea.id}
                onClick={() => {
                  setSelected(idea);
                  setPromptError("");
                }}
              >
                <div className="radar-idea-main">
                  <div className="idea-title-line">
                    <h3>{idea.title}</h3>
                    <StatusPill tone={state.tone}>{state.label}</StatusPill>
                  </div>
                  <p>{idea.thesis}</p>
                  <div className="idea-meta">
                    <span>
                      {collection === "conditioned"
                        ? `${conditionedDirections.find((item) => item.idea.id === idea.id)?.run.team_name ?? "—"} · ${idea.field}`
                        : `${baselines.find((item) => item.idea.id === idea.id)?.venueLabel ?? "—"} · ${idea.field}`}
                    </span>
                    {collection === "conditioned" && hasScores && (
                      <div>
                        {idea.novelty >= 0 && <span>{t("ideas.score.novelty")} <strong>{idea.novelty}</strong></span>}
                        {idea.feasibility >= 0 && <span>{t("ideas.score.feasibility")} <strong>{idea.feasibility}</strong></span>}
                      </div>
                    )}
                  </div>
                </div>
              </button>
            );
          })}
        </section>
        {selected ? (
          <aside className="idea-inspector">
            <div className="inspector-head">
              <StatusPill tone={selectedState.tone}>{selectedState.label}</StatusPill>
              {selectedDirection && <span>{selectedCampaignState}</span>}
            </div>
            <h2>{selected.title}</h2>
            {selectedDirection && (
              <dl className="condition-identity">
                <div><dt>{t("ideas.identity.team")}</dt><dd>{selectedDirection.run.team_name ?? selectedDirection.run.team_profile_id}</dd></div>
                <div><dt>{t("ideas.identity.venue")}</dt><dd>{selectedDirection.run.venue_name ?? selectedDirection.run.venue_key ?? "—"}</dd></div>
                <div><dt>{t("ideas.identity.run")}</dt><dd title={selectedDirection.run.id}>{selectedDirection.run.id.slice(0, 12)}</dd></div>
                <div><dt>{t("ideas.identity.condition")}</dt><dd title={selectedDirection.run.condition_sha256}>{selectedDirection.run.condition_sha256?.slice(0, 16) ?? "—"}</dd></div>
                <div><dt>{t("ideas.identity.objective")}</dt><dd title={selectedDirection.run.objective_sha256}>{selectedDirection.run.objective_sha256.slice(0, 16)}</dd></div>
              </dl>
            )}
            {selectedBaseline && (
              <div className="baseline-venue">
                <span>{t("ideas.identity.venue")}</span>
                <strong>{selectedBaseline.venueLabel}</strong>
              </div>
            )}
            {hasDelta && (
              <div className="delta-block">
                <strong>{t("ideas.inspector.delta")}</strong>
                <p>{selected.delta}</p>
              </div>
            )}
            {selectedDirection && (teamAdvantage || collisionTest) && (
              <dl className="condition-fit">
                {teamAdvantage && <div><dt>{t("ideas.inspector.teamAdvantage")}</dt><dd>{teamAdvantage}</dd></div>}
                {collisionTest && <div><dt>{t("ideas.inspector.collisionTest")}</dt><dd>{collisionTest}</dd></div>}
              </dl>
            )}
            {hasSources && (
              <div className="source-list">
                <span>{t("ideas.inspector.sources")}</span>
                {selected.sources.map((source) => (
                  <div key={`${source.kind}-${source.label}`}>
                    <span
                      className={`source-icon ${source.kind.toLowerCase()}`}
                    >
                      {source.kind === "GitHub" ? (
                        <Github size={12} />
                      ) : (
                        source.kind[0]
                      )}
                    </span>
                    <div>
                      <strong>{source.kind}</strong>
                      <p>{source.label}</p>
                    </div>
                    <time>{source.age}</time>
                  </div>
                ))}
              </div>
            )}
            {!hasDelta && !hasSources && (
              <div className="inspector-empty">
                <p>{t("ideas.inspector.empty")}</p>
                <Button kind="ghost" icon={<RefreshCw size={14} />} onClick={syncSources}>
                  {t("ideas.inspector.sync")}
                </Button>
              </div>
            )}
            {hasResource && (
              <div className="resource-fit">
                <div><Cpu size={14} /><span>{t("ideas.inspector.resource")}</span></div>
                <dl>
                  {selected.compute && <div><dt>{t("ideas.inspector.compute")}</dt><dd>{selected.compute}</dd></div>}
                  {selected.risk && <div><dt>{t("ideas.inspector.risk")}</dt><dd>{selected.risk}</dd></div>}
                </dl>
              </div>
            )}
            {promptError && (
              <Notice tone="warn" title={t("ideas.prompt.unavailable")}>
                {promptError}
              </Notice>
            )}
            <Button
              kind="primary"
              disabled={(Boolean(selectedDirection) && !selectedCampaign && mode !== "live") || promptBusy}
              title={
                selectedDirection && !selectedCampaign && mode !== "live"
                  ? t("ideas.inspector.liveRequired")
                  : undefined
              }
              onClick={openPrompt}
            >
              {promptBusy
                ? t("ideas.campaign.creating")
                : selectedCampaign
                  ? t("ideas.campaign.open")
                : selectedDirection
                  ? t("ideas.campaign.create")
                  : t("ideas.baseline.generate")}{" "}
              <ArrowRight size={14} />
            </Button>
          </aside>
        ) : (
          <EmptyState
            title={t("ideas.select.title")}
          />
        )}
      </div>
    </>
  );
}

export function ViewerPage() {
  const { data, act } = useApp();
  const { t } = useI18n();
  const [reportId, setReportId] = useState(data.viewerReports[0]?.id);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [reviewReason, setReviewReason] = useState("");
  const [reviewApproved, setReviewApproved] = useState(false);
  const [reviewBusy, setReviewBusy] = useState(false);
  const report = data.viewerReports.find((item) => item.id === reportId);
  const campaign = data.campaigns.find(
    (item) => item.id === report?.campaignId,
  );
  return (
    <>
      <PageHeader
        eyebrow="INDEPENDENT REVIEW PROCESS"
        title="Argus Viewer"
        actions={
          <Button
            icon={<RefreshCw size={15} />}
            disabled={!report}
            onClick={() => setReviewOpen(true)}
          >
            {t("action.freshReview")}
          </Button>
        }
      />
      <Notice tone="warn" title="分数不是录用概率">
        Viewer 不共享 Campaign 的隐藏推理或正向结果目标。它评估当前证据包；“Oral
        readiness”仅表示稿件是否呈现出需要重点讨论的潜力，不保证录用或 Oral。
      </Notice>
      <div className="viewer-layout">
        <aside className="viewer-list">
          <span>REVIEW QUEUE</span>
          {data.viewerReports.map((item) => (
            <button
              className={item.id === reportId ? "selected" : ""}
              key={item.id}
              onClick={() => setReportId(item.id)}
            >
              <div>
                <strong>
                  {data.campaigns.find((c) => c.id === item.campaignId)?.title}
                </strong>
                <small>
                  {item.venue} · Updated {item.updated}
                </small>
              </div>
              <strong>{item.overall.toFixed(1)}</strong>
            </button>
          ))}
        </aside>
        {report && (
          <section className="review-sheet">
            <div className="review-masthead">
              <div>
                <span>ANONYMOUS VIEWER REPORT</span>
                <h2>{campaign?.title}</h2>
                <p>
                  {report.venue} rubric · Independent process · {report.updated}
                </p>
              </div>
              <div className="viewer-seal">
                <Eye size={18} />
                <span>VIEWER</span>
                <small>isolated</small>
              </div>
            </div>
            <div className="score-constellation">
              <ScoreRing value={report.overall} label="overall / 10" large />
              {report.confidence >= 0 ? (
                <ScoreRing
                  value={report.confidence * 100}
                  max={100}
                  label="confidence"
                />
              ) : (
                <div className="unreported-score">
                  <strong>—</strong>
                  <span>confidence not reported</span>
                </div>
              )}
              {report.oralReadiness >= 0 ? (
                <ScoreRing
                  value={report.oralReadiness}
                  max={100}
                  label="oral signal"
                />
              ) : (
                <div className="unreported-score">
                  <strong>—</strong>
                  <span>
                    {report.oralReadinessLabel &&
                    report.oralReadinessLabel !== "unknown"
                      ? report.oralReadinessLabel.replaceAll("_", " ")
                      : "oral signal not reported"}
                  </span>
                </div>
              )}
              <div className="verdict">
                <span>CURRENT VERDICT</span>
                <strong>{report.verdict}</strong>
                <p>
                  Reviewer scores should be interpreted with evidence, not as a
                  target to optimize directly.
                </p>
              </div>
            </div>
            <div className="rubric-table">
              <div className="rubric-head">
                <span>VENUE DIMENSION</span>
                <span>SCORE</span>
                <span>VIEWER NOTE</span>
              </div>
              {report.dimensions.map((dimension) => (
                <div className="rubric-row" key={dimension.label}>
                  <strong>{dimension.label}</strong>
                  <div>
                    <span>{dimension.score.toFixed(1)}</span>
                    <i>
                      <em style={{ width: `${dimension.score * 10}%` }} />
                    </i>
                  </div>
                  <p>{dimension.note}</p>
                </div>
              ))}
            </div>
            <div className="blocker-sheet">
              <div>
                <TriangleAlert size={16} />
                <strong>Blocking issues before the next score</strong>
              </div>
              {report.blockers.length ? (
                <ol>
                  {report.blockers.map((blocker) => (
                    <li key={blocker}>{blocker}</li>
                  ))}
                </ol>
              ) : (
                <p>No blockers were reported by the independent evaluator.</p>
              )}
            </div>
          </section>
        )}
      </div>
      {reviewOpen && report && (
        <div
          className="modal-layer"
          role="dialog"
          aria-modal="true"
          aria-labelledby="viewer-review-title"
          onMouseDown={(event) =>
            event.currentTarget === event.target &&
            !reviewBusy &&
            setReviewOpen(false)
          }
        >
          <div className="start-modal review-approval-modal">
            <div className="modal-head">
              <div>
                <span className="eyebrow">ATTRIBUTABLE REVIEW SPEND</span>
                <h2 id="viewer-review-title">Run a fresh isolated review?</h2>
              </div>
              <button
                className="icon-button"
                disabled={reviewBusy}
                onClick={() => setReviewOpen(false)}
                aria-label="关闭"
              >
                <X size={17} />
              </button>
            </div>
            <Notice tone="warn" title="Human approval is required">
              这可能启动新的独立 evaluator
              并产生费用。已有评分不会被当作录用概率。
            </Notice>
            <label className="approval-reason">
              <span>Required approval reason</span>
              <textarea
                rows={3}
                required
                value={reviewReason}
                onChange={(event) => setReviewReason(event.target.value)}
                placeholder="记录为什么需要重新评审及其成本和证据边界。"
              />
            </label>
            <label className="confirm-check contract-approval">
              <input
                type="checkbox"
                checked={reviewApproved}
                onChange={(event) => setReviewApproved(event.target.checked)}
              />
              <span>
                <Check size={12} />
              </span>
              <p>
                我批准 venue_reviewer 使用 fresh context 评审此
                Campaign，并理解它不保证录用。
              </p>
            </label>
            <div className="modal-actions">
              <Button
                disabled={reviewBusy}
                onClick={() => setReviewOpen(false)}
              >
                取消
              </Button>
              <Button
                kind="primary"
                disabled={!reviewApproved || !reviewReason.trim() || reviewBusy}
                onClick={async () => {
                  setReviewBusy(true);
                  try {
                    const accepted = await act(
                      `campaigns/${report.campaignId}/review`,
                      {
                        reviewer_kind: "venue_reviewer",
                        rubric: { independent: true },
                        human_approved: true,
                        actor: "flywheel-ui",
                        approval_reason: reviewReason.trim(),
                      },
                    );
                    if (accepted) {
                      setReviewOpen(false);
                      setReviewReason("");
                      setReviewApproved(false);
                    }
                  } finally {
                    setReviewBusy(false);
                  }
                }}
              >
                {reviewBusy ? "Requesting…" : "Approve & run review"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

type ReviewerDraft = {
  reviewer: string;
  score: string;
  opinion_redacted: string;
};

export function OutcomesPage() {
  const { data, mode, toast } = useApp();
  const { t } = useI18n();
  const [outcomes, setOutcomes] = useState<OutcomeRecord[]>([]);
  const [loading, setLoading] = useState(mode === "live");
  const [error, setError] = useState("");
  const [recording, setRecording] = useState(false);
  const [campaignId, setCampaignId] = useState(data.campaigns[0]?.id ?? "");
  const [submissionVersion, setSubmissionVersion] = useState("");
  const [decision, setDecision] = useState("pending");
  const [reviewers, setReviewers] = useState<ReviewerDraft[]>([
    { reviewer: "Reviewer 1", score: "", opinion_redacted: "" },
  ]);
  const [redactionConfirmed, setRedactionConfirmed] = useState(false);
  const [consent, setConsent] = useState(false);
  const [licenseConfirmed, setLicenseConfirmed] = useState(false);
  const [followUpReasons, setFollowUpReasons] = useState<
    Record<string, string>
  >({});
  const refreshOutcomes = () => {
    if (mode !== "live") {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    loadOutcomes()
      .then(setOutcomes)
      .catch((cause: Error) => setError(cause.message))
      .finally(() => setLoading(false));
  };
  useEffect(refreshOutcomes, [mode]);
  const validReviews = reviewers.every(
    (item) =>
      item.reviewer.trim() &&
      item.opinion_redacted.trim() &&
      (!item.score || !Number.isNaN(Number(item.score))),
  );
  const canRecord =
    mode === "live" &&
    campaignId &&
    submissionVersion.trim() &&
    redactionConfirmed &&
    validReviews;
  const submitOutcome = async () => {
    if (!canRecord) return;
    try {
      await createOutcome({
        campaign_id: campaignId,
        submission_version: submissionVersion.trim(),
        reviewer_feedback: reviewers.map((item) => ({
          reviewer: item.reviewer.trim(),
          ...(item.score ? { score: Number(item.score) } : {}),
          opinion_redacted: item.opinion_redacted.trim(),
        })),
        decision,
        consent_to_training_export: consent,
        review_license_confirmed: licenseConfirmed,
        redaction_confirmed: redactionConfirmed,
      });
      toast("Outcome 已记录；没有自动训练、启动 Argus 或投稿。");
      setRecording(false);
      setSubmissionVersion("");
      setDecision("pending");
      setReviewers([
        { reviewer: "Reviewer 1", score: "", opinion_redacted: "" },
      ]);
      setRedactionConfirmed(false);
      setConsent(false);
      setLicenseConfirmed(false);
      refreshOutcomes();
    } catch (cause) {
      toast(
        `Outcome 记录失败：${cause instanceof Error ? cause.message : "unknown error"}`,
      );
    }
  };
  const createFollowUp = async (outcome: OutcomeRecord) => {
    const reason = (followUpReasons[outcome.id] ?? "").trim();
    if (!reason) return;
    try {
      const created = await createOutcomeFollowUp(outcome.id, reason);
      const createdId = String(created.campaign_id ?? created.id ?? "");
      toast(
        `Rebuttal objective 已生成${createdId ? `，idle Campaign ${createdId.slice(0, 8)} 已创建` : ""}；仍需独立人工 Start gate。`,
      );
      refreshOutcomes();
    } catch (cause) {
      toast(
        `Follow-up 创建失败：${cause instanceof Error ? cause.message : "unknown error"}`,
      );
    }
  };
  return (
    <>
      <PageHeader
        eyebrow="POST-SUBMISSION EVIDENCE"
        title="Outcomes & Rebuttal"
        actions={
          <Button
            kind="primary"
            icon={<Plus size={14} />}
            disabled={mode !== "live"}
            onClick={() => setRecording(true)}
          >
            {t("action.recordOutcome")}
          </Button>
        }
      />
      <Notice tone="info" title="Consent and license are separate gates">
        审稿文本必须匿名化并去除个人信息。只有明确同意训练导出且确认拥有相应许可的记录，后端才可标记为
        training-export eligible；记录 outcome 本身不等于同意训练。
      </Notice>
      {mode === "demo" ? (
        <EmptyState
          title="Live API required"
          detail="Demo 模式不会伪造投稿结果、审稿意见、rebuttal Campaign 或训练样本。"
        />
      ) : loading ? (
        <div className="outcome-loading">
          <RefreshCw size={15} />
          Loading recorded outcomes…
        </div>
      ) : error ? (
        <EmptyState
          title="Outcomes API unavailable"
          detail={error}
          action={<Button onClick={refreshOutcomes}>Retry</Button>}
        />
      ) : outcomes.length === 0 ? (
        <EmptyState
          title="还没有真实 outcome"
          detail="录入投稿版本和已脱敏审稿意见后，系统才会生成可审计的 rebuttal 输入。"
        />
      ) : (
        <div className="outcome-grid">
          {outcomes.map((outcome) => (
            <article className="outcome-card" key={outcome.id}>
              <header>
                <div>
                  <span>{outcome.submission_version}</span>
                  <h2>
                    {outcome.campaign_title ??
                      data.campaigns.find(
                        (item) => item.id === outcome.campaign_id,
                      )?.title ??
                      outcome.campaign_id}
                  </h2>
                </div>
                <StatusPill
                  tone={
                    outcome.decision.toLowerCase().includes("accept")
                      ? "good"
                      : outcome.decision === "pending"
                        ? "neutral"
                        : "warn"
                  }
                >
                  {outcome.decision}
                </StatusPill>
              </header>
              <div className="outcome-reviews">
                {outcome.reviewer_feedback.map((review, index) => (
                  <div key={`${review.reviewer}-${index}`}>
                    <div>
                      <strong>{review.reviewer}</strong>
                      <span>
                        {typeof review.score === "number"
                          ? `Score ${review.score}`
                          : "No numeric score reported"}
                      </span>
                    </div>
                    <p>{review.opinion_redacted}</p>
                  </div>
                ))}
              </div>
              {outcome.rebuttal_objective && (
                <div className="rebuttal-objective">
                  <span>REBUTTAL OBJECTIVE</span>
                  <p>{outcome.rebuttal_objective}</p>
                </div>
              )}
              <div className="training-state">
                <Shield size={14} />
                <div>
                  <strong>
                    {outcome.training_export_eligible
                      ? "Eligible for explicit export"
                      : "Not eligible for training export"}
                  </strong>
                  <p>
                    {outcome.training_export_eligible
                      ? "Consent and license gates were recorded by the backend."
                      : (
                          outcome.training_export_ineligibility_reasons ?? [
                            "Consent and/or license gate is not satisfied.",
                          ]
                        ).join(" · ")}
                  </p>
                </div>
                {outcome.training_export_eligible && (
                  <Button
                    icon={<Download size={13} />}
                    onClick={() =>
                      downloadOutcomeTrainingSample(outcome.id).catch(
                        (cause: Error) => toast(`导出失败：${cause.message}`),
                      )
                    }
                  >
                    Export sample
                  </Button>
                )}
              </div>
              {outcome.follow_up_campaign_id ? (
                <div className="follow-up-created">
                  <CheckCircle2 size={14} />
                  <span>
                    Idle follow-up Campaign:{" "}
                    <Link to={`/campaigns/${outcome.follow_up_campaign_id}`}>
                      {outcome.follow_up_campaign_id}
                    </Link>
                  </span>
                </div>
              ) : (
                <div className="follow-up-form">
                  <label>
                    <span>
                      Required human reason for creating an idle rebuttal
                      Campaign
                    </span>
                    <textarea
                      rows={2}
                      value={followUpReasons[outcome.id] ?? ""}
                      onChange={(event) =>
                        setFollowUpReasons((current) => ({
                          ...current,
                          [outcome.id]: event.target.value,
                        }))
                      }
                      placeholder="说明为什么需要 follow-up、允许使用哪些审稿信息，以及禁止自动投稿。"
                    />
                  </label>
                  <Button
                    icon={<MessageSquareReply size={14} />}
                    disabled={!(followUpReasons[outcome.id] ?? "").trim()}
                    onClick={() => createFollowUp(outcome)}
                  >
                    Create idle follow-up
                  </Button>
                </div>
              )}
            </article>
          ))}
        </div>
      )}
      {recording && (
        <div
          className="modal-layer"
          role="dialog"
          aria-modal="true"
          aria-labelledby="record-outcome-title"
          onMouseDown={(event) =>
            event.currentTarget === event.target && setRecording(false)
          }
        >
          <form
            className="connection-modal outcome-modal"
            onSubmit={(event) => {
              event.preventDefault();
              submitOutcome();
            }}
          >
            <div className="modal-head">
              <div>
                <span className="eyebrow">
                  HUMAN-RECORDED SUBMISSION EVIDENCE
                </span>
                <h2 id="record-outcome-title">Record an outcome</h2>
              </div>
              <button
                type="button"
                className="icon-button"
                onClick={() => setRecording(false)}
                aria-label="关闭 outcome 表单"
              >
                <X size={17} />
              </button>
            </div>
            <label>
              <span>Source Campaign</span>
              <select
                required
                value={campaignId}
                onChange={(event) => setCampaignId(event.target.value)}
              >
                <option value="">Select Campaign</option>
                {data.campaigns.map((campaign) => (
                  <option key={campaign.id} value={campaign.id}>
                    {campaign.title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Submitted version / immutable identifier</span>
              <input
                required
                value={submissionVersion}
                onChange={(event) => setSubmissionVersion(event.target.value)}
                placeholder="e.g. OpenReview submission v3 / artifact SHA"
              />
            </label>
            <label>
              <span>Decision</span>
              <select
                value={decision}
                onChange={(event) => setDecision(event.target.value)}
              >
                <option value="pending">Pending</option>
                <option value="accept">Accept</option>
                <option value="reject">Reject</option>
                <option value="withdrawn">Withdrawn</option>
              </select>
            </label>
            <div className="reviewer-drafts">
              <div>
                <span>ANONYMIZED REVIEWER FEEDBACK</span>
                <Button
                  type="button"
                  icon={<Plus size={13} />}
                  onClick={() =>
                    setReviewers((items) => [
                      ...items,
                      {
                        reviewer: `Reviewer ${items.length + 1}`,
                        score: "",
                        opinion_redacted: "",
                      },
                    ])
                  }
                >
                  Add reviewer
                </Button>
              </div>
              {reviewers.map((review, index) => (
                <fieldset key={index}>
                  <input
                    aria-label={`Reviewer ${index + 1} label`}
                    required
                    value={review.reviewer}
                    onChange={(event) =>
                      setReviewers((items) =>
                        items.map((item, position) =>
                          position === index
                            ? { ...item, reviewer: event.target.value }
                            : item,
                        ),
                      )
                    }
                  />
                  <input
                    aria-label={`Reviewer ${index + 1} score`}
                    type="number"
                    step="0.1"
                    value={review.score}
                    onChange={(event) =>
                      setReviewers((items) =>
                        items.map((item, position) =>
                          position === index
                            ? { ...item, score: event.target.value }
                            : item,
                        ),
                      )
                    }
                    placeholder="Score (optional)"
                  />
                  <textarea
                    aria-label={`Reviewer ${index + 1} redacted opinion`}
                    required
                    rows={4}
                    value={review.opinion_redacted}
                    onChange={(event) =>
                      setReviewers((items) =>
                        items.map((item, position) =>
                          position === index
                            ? { ...item, opinion_redacted: event.target.value }
                            : item,
                        ),
                      )
                    }
                    placeholder="粘贴已匿名、已脱敏的审稿意见。"
                  />
                  {reviewers.length > 1 && (
                    <Button
                      type="button"
                      kind="ghost"
                      onClick={() =>
                        setReviewers((items) =>
                          items.filter((_, position) => position !== index),
                        )
                      }
                    >
                      Remove
                    </Button>
                  )}
                </fieldset>
              ))}
            </div>
            <label className="stage-confirm">
              <input
                type="checkbox"
                checked={redactionConfirmed}
                onChange={(event) =>
                  setRedactionConfirmed(event.target.checked)
                }
              />
              <span>我确认已移除姓名、邮箱、机构线索及其他个人信息。</span>
            </label>
            <label className="stage-confirm">
              <input
                type="checkbox"
                checked={consent}
                onChange={(event) => setConsent(event.target.checked)}
              />
              <span>我明确同意将这条记录列入可导出的训练样本候选。</span>
            </label>
            <label className="stage-confirm">
              <input
                type="checkbox"
                checked={licenseConfirmed}
                onChange={(event) => setLicenseConfirmed(event.target.checked)}
              />
              <span>
                我确认拥有导出/训练所需许可；未勾选时仍可记录
                outcome，但不可导出训练样本。
              </span>
            </label>
            <div className="modal-actions">
              <Button type="button" onClick={() => setRecording(false)}>
                Cancel
              </Button>
              <Button kind="primary" type="submit" disabled={!canRecord}>
                Record only · do not start
              </Button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}

export function ConnectionsPage() {
  const { data, act, mode, toast } = useApp();
  const { t } = useI18n();
  const [adding, setAdding] = useState(false);
  const [showToken, setShowToken] = useState(false);
  const [stageOpen, setStageOpen] = useState(false);
  const [stageAcknowledged, setStageAcknowledged] = useState(false);
  const [releaseBusy, setReleaseBusy] = useState(false);
  const [releaseChannel, setReleaseChannel] = useState<"official" | "preview">(
    "official",
  );
  const [connectionForm, setConnectionForm] = useState({
    name: "",
    kind: "local" as "local" | "remote",
    base_url: "http://127.0.0.1:8799",
    token_env: "ARGUS_SKILL_WEB_TOKEN",
    bearer_token: "",
  });
  const [release, setRelease] = useState<ReleaseStatus | null>(null);
  const releaseSource =
    releaseChannel === "official"
      ? {
          label: "Official release · microsoft/ArgusAgent v0.1.2",
          repository: "https://github.com/microsoft/ArgusAgent.git",
          ref: "refs/tags/v0.1.2",
          referenceSha: "455da6cb…",
        }
      : {
          label: "Preview · lbx154/Argus",
          repository: "https://github.com/lbx154/Argus.git",
          ref: "refs/heads/main",
          referenceSha: "7c04ded7…",
        };
  const releaseRepository = releaseSource.repository;
  const releaseRef = releaseSource.ref;
  const registryMatchesSource =
    String(release?.registry.repository ?? "") === releaseRepository;
  const remoteSha =
    registryMatchesSource &&
    typeof release?.registry.remote_sha === "string" &&
    /^[0-9a-f]{40,64}$/i.test(release.registry.remote_sha)
      ? release.registry.remote_sha
      : "";
  useEffect(() => {
    if (mode === "live")
      loadReleaseStatus()
        .then(setRelease)
        .catch(() => setRelease(null));
  }, [mode]);
  const inspectCandidate = async () => {
    setReleaseBusy(true);
    try {
      const result = await inspectReleaseCandidate(
        releaseRepository,
        releaseRef,
      );
      const persisted = await loadReleaseStatus();
      setRelease({
        ...persisted,
        registry: {
          ...persisted.registry,
          ...result,
          repository: releaseRepository,
        },
      });
      toast(
        `只读检查完成：${String(result.remote_sha ?? "远端 SHA 未返回")}；尚未 checkout、测试或采用。`,
      );
    } catch (error) {
      toast(
        `Release 检查失败：${error instanceof Error ? error.message : "unknown error"}`,
      );
    } finally {
      setReleaseBusy(false);
    }
  };
  const confirmStage = async () => {
    if (!remoteSha || !stageAcknowledged) return;
    setReleaseBusy(true);
    try {
      const result = await stageReleaseCandidate(
        releaseRepository,
        releaseRef,
        remoteSha,
      );
      setRelease(await loadReleaseStatus());
      setStageOpen(false);
      setStageAcknowledged(false);
      toast(
        `候选 ${String(result.sha).slice(0, 12)} 已隔离暂存；尚未测试、canary、采用或启动 Argus。`,
      );
    } catch (error) {
      toast(
        `隔离暂存失败：${error instanceof Error ? error.message : "unknown error"}`,
      );
    } finally {
      setReleaseBusy(false);
    }
  };
  return (
    <>
      <PageHeader
        eyebrow="ARGUS RUNTIMES"
        title="Connections"
        actions={
          <Button
            kind="primary"
            icon={<Plus size={15} />}
            onClick={() => setAdding(true)}
          >
            {t("action.addConnection")}
          </Button>
        }
      />
      {data.connections.length ? (
        <div className="connection-list">
          {data.connections.map((connection) => (
            <article className="connection-row" key={connection.id}>
              <div className={`machine-glyph ${connection.kind}`}>
                {connection.kind === "local" ? (
                  <Laptop size={20} />
                ) : (
                  <Server size={20} />
                )}
                <i />
              </div>
              <div className="connection-main">
                <div>
                  <h3>{connection.name}</h3>
                  {connection.managed && <StatusPill tone="neutral">managed</StatusPill>}
                  <StatusPill
                    tone={connection.state === "connected" ? "good" : "bad"}
                  >
                    {connection.state}
                  </StatusPill>
                </div>
                <p>{connection.address}</p>
                <div>
                  {connection.capabilities.map((item) => (
                    <span key={item}>{item}</span>
                  ))}
                  {connection.backendReady === false && <span>backend not ready</span>}
                </div>
              </div>
              <div className="connection-metric">
                <span>REPORTED RELEASE / REVISION</span>
                <strong>{connection.version}</strong>
                <small>
                  Connection metadata only; each Campaign reports its own pin
                  status
                </small>
              </div>
              <div className="connection-metric">
                <span>LATENCY</span>
                <strong>{connection.latency}</strong>
                <small>
                  {connection.tokenSource === "environment"
                    ? "Token via environment"
                    : connection.state === "connected" ? "Authenticated" : connection.lastError || "Not probed"}
                </small>
              </div>
              <Button
                icon={<TestTube2 size={14} />}
                onClick={() => act(`connections/${connection.id}/test`, {})}
              >
                Test
              </Button>
            </article>
          ))}
        </div>
      ) : (
        <EmptyState
          title="还没有 Argus 连接"
          detail="添加本机或远程 Argus WebAPI；token 只发送给 Flywheel backend，不保存在浏览器。"
          action={
            <Button kind="primary" onClick={() => setAdding(true)}>
              Add connection
            </Button>
          }
        />
      )}
      <section className="release-board">
        <div className="section-line">
          <div>
            <span>SAFE RELEASE CHANNEL</span>
            <small>{releaseSource.label}</small>
          </div>
          <label className="release-source-select">
            <span>Source</span>
            <select
              value={releaseChannel}
              onChange={(event) =>
                setReleaseChannel(event.target.value as "official" | "preview")
              }
            >
              <option value="official">Official · microsoft/ArgusAgent</option>
              <option value="preview">Preview · lbx154/Argus</option>
            </select>
          </label>
        </div>
        <Notice
          tone={releaseChannel === "official" ? "info" : "warn"}
          title={
            releaseChannel === "official"
              ? "Official release source"
              : "Preview source — not the official release repository"
          }
        >
          {releaseRepository} · The two repositories are inspected and staged as
          distinct builds; their SHAs are never combined.
        </Notice>
        {mode === "demo" ? (
          <div className="release-flow">
            <div>
              <span>REFERENCE SHA</span>
              <strong>{releaseSource.referenceSha}</strong>
              <p>Known source reference · not inspected live</p>
            </div>
            <ArrowRight size={16} />
            <div>
              <span>CANARY</span>
              <strong>Not recorded</strong>
              <p>Demo does not claim a canary</p>
            </div>
            <ArrowRight size={16} />
            <div className="stable">
              <span>STABLE</span>
              <strong>Not adopted</strong>
              <p>No live registry in demo</p>
            </div>
          </div>
        ) : (
          <div className="release-flow">
            <div>
              <span>REMOTE CANDIDATE</span>
              <strong>
                {registryMatchesSource
                  ? String(release?.registry.remote_sha ?? "Not inspected")
                  : "Not inspected for this source"}
              </strong>
              <p>
                {registryMatchesSource
                  ? String(release?.registry.status ?? "Use read-only inspect")
                  : "Select Read remote ref"}
              </p>
            </div>
            <ArrowRight size={16} />
            <div>
              <span>CANARY</span>
              <strong>
                {registryMatchesSource
                  ? String(release?.registry.canary_sha ?? "No canary recorded")
                  : "No canary recorded"}
              </strong>
              <p>{String(release?.policy.canary ?? "Required")}</p>
            </div>
            <ArrowRight size={16} />
            <div className="stable">
              <span>STABLE</span>
              <strong>
                {String(
                  registryMatchesSource
                    ? (release?.registry.stable_sha ?? "No stable SHA recorded")
                    : "No stable SHA recorded",
                )}
              </strong>
              <p>
                {String(
                  release?.policy.running_campaigns ??
                    "Running campaigns never mutate",
                )}
              </p>
            </div>
          </div>
        )}
        <Notice tone="info" title="Version isolation">
          Any candidate must be staged in a new immutable directory and pass
          canary review before adoption. Existing Campaigns never change SHA
          mid-run.
        </Notice>
        {mode === "live" && (
          <div className="release-action">
            <Button
              icon={<RefreshCw size={14} />}
              disabled={releaseBusy}
              onClick={inspectCandidate}
            >
              Read remote ref
            </Button>
            <Button
              icon={<Download size={14} />}
              disabled={releaseBusy || !remoteSha}
              onClick={() => setStageOpen(true)}
            >
              Stage candidate
            </Button>
            <span>
              Read uses git ls-remote. Stage writes only a new content-addressed
              Flywheel directory; neither action adopts or starts Argus.
            </span>
          </div>
        )}
      </section>
      {adding && (
        <div
          className="modal-layer"
          role="dialog"
          aria-modal="true"
          aria-labelledby="connection-title"
          onMouseDown={(e) => e.currentTarget === e.target && setAdding(false)}
        >
          <form
            className="connection-modal"
            onSubmit={async (e) => {
              e.preventDefault();
              const created = await act("connections", {
                ...connectionForm,
                token_env: connectionForm.bearer_token
                  ? undefined
                  : connectionForm.token_env || undefined,
                bearer_token: connectionForm.bearer_token || undefined,
              });
              if (created) {
                setAdding(false);
                setConnectionForm({
                  name: "",
                  kind: "local",
                  base_url: "http://127.0.0.1:8799",
                  token_env: "ARGUS_SKILL_WEB_TOKEN",
                  bearer_token: "",
                });
              }
            }}
          >
            <div className="modal-head">
              <div>
                <span className="eyebrow">NEW RUNTIME</span>
                <h2 id="connection-title">Connect an Argus instance</h2>
              </div>
              <button
                type="button"
                className="icon-button"
                onClick={() => setAdding(false)}
                aria-label="关闭"
              >
                <X size={17} />
              </button>
            </div>
            <label>
              <span>Connection kind</span>
              <select
                value={connectionForm.kind}
                onChange={(e) =>
                  setConnectionForm((form) => ({
                    ...form,
                    kind: e.target.value as "local" | "remote",
                  }))
                }
              >
                <option value="local">Local WebAPI</option>
                <option value="remote">Remote HTTPS WebAPI</option>
              </select>
            </label>
            <label>
              <span>Display name</span>
              <input
                required
                value={connectionForm.name}
                onChange={(e) =>
                  setConnectionForm((form) => ({
                    ...form,
                    name: e.target.value,
                  }))
                }
                placeholder="lab-gpu-node-03"
              />
            </label>
            <label>
              <span>Argus WebAPI URL</span>
              <input
                required
                type="url"
                value={connectionForm.base_url}
                onChange={(e) =>
                  setConnectionForm((form) => ({
                    ...form,
                    base_url: e.target.value,
                  }))
                }
                placeholder="https://argus.example.net"
              />
            </label>
            <label>
              <span>Token environment variable</span>
              <input
                value={connectionForm.token_env}
                onChange={(e) =>
                  setConnectionForm((form) => ({ ...form, token_env: e.target.value }))
                }
                pattern="[A-Za-z_][A-Za-z0-9_]*"
                placeholder="ARGUS_SKILL_WEB_TOKEN"
              />
              <small>Recommended. Flywheel and Argus resolve the same server-side variable.</small>
            </label>
            <label>
              <span>One-run token (optional)</span>
              <div className="secret-input">
                <input
                  type={showToken ? "text" : "password"}
                  value={connectionForm.bearer_token}
                  onChange={(e) =>
                    setConnectionForm((form) => ({
                      ...form,
                      bearer_token: e.target.value,
                    }))
                  }
                  autoComplete="new-password"
                  placeholder="Sent once; never stored in the browser"
                />
                <button type="button" onClick={() => setShowToken((v) => !v)}>
                  {showToken ? "Hide" : "Show"}
                </button>
              </div>
              <small>
                仅保存在当前 Flywheel 进程内；重启后失效。
              </small>
            </label>
            <div className="modal-actions">
              <Button type="button" onClick={() => setAdding(false)}>
                Cancel
              </Button>
              <Button kind="primary" type="submit" icon={<Network size={15} />}>
                Save connection
              </Button>
            </div>
          </form>
        </div>
      )}
      {stageOpen && (
        <div
          className="modal-layer"
          role="dialog"
          aria-modal="true"
          aria-labelledby="stage-release-title"
          onMouseDown={(event) =>
            event.currentTarget === event.target && setStageOpen(false)
          }
        >
          <form
            className="connection-modal"
            onSubmit={(event) => {
              event.preventDefault();
              confirmStage();
            }}
          >
            <div className="modal-head">
              <div>
                <span className="eyebrow">ISOLATED RELEASE STAGE</span>
                <h2 id="stage-release-title">Stage this exact Argus commit?</h2>
              </div>
              <button
                type="button"
                className="icon-button"
                onClick={() => setStageOpen(false)}
                aria-label="关闭"
              >
                <X size={17} />
              </button>
            </div>
            <div className="stage-sha">
              <span>Source repository</span>
              <code>{releaseRepository}</code>
              <span>Verified remote SHA</span>
              <code>{remoteSha}</code>
            </div>
            <Notice tone="warn" title="Stage only">
              This creates a new Flywheel-owned checkout below the release
              staging directory. It does not run tests, start a daemon, adopt
              the version, or touch any existing checkout.
            </Notice>
            <label className="stage-confirm">
              <input
                type="checkbox"
                checked={stageAcknowledged}
                onChange={(event) => setStageAcknowledged(event.target.checked)}
              />
              <span>
                I understand that canary and separate human adoption are still
                required.
              </span>
            </label>
            <div className="modal-actions">
              <Button type="button" onClick={() => setStageOpen(false)}>
                Cancel
              </Button>
              <Button
                kind="primary"
                type="submit"
                disabled={!stageAcknowledged || releaseBusy}
                icon={<Download size={15} />}
              >
                Confirm isolated stage
              </Button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}

export function ResourcesPage() {
  const { data, act, mode, toast } = useApp();
  const { t } = useI18n();
  const [tab, setTab] = useState<
    "compute" | "models" | "notifications" | "releases" | "appearance"
  >("compute");
  const [addingResource, setAddingResource] = useState(false);
  const [resourceForm, setResourceForm] = useState({
    name: "",
    resource_type: "gpu_pool",
    gpu_count: 1,
    gpu_model: "",
    gpu_hours: 160,
    api_budget: 30,
    max_parallel_jobs: 1,
  });
  const [concurrency, setConcurrency] = useState(2);
  const [reserve, setReserve] = useState(1);
  const [roleRows, setRoleRows] = useState(
    data.resources.roles.length
      ? data.resources.roles
      : [
          {
            role: "Manager",
            provider: "Pi",
            model: "connection default",
            budget: "explicit cap required",
          },
          {
            role: "Planner",
            provider: "Pi",
            model: "connection default",
            budget: "explicit cap required",
          },
          {
            role: "Engineer",
            provider: "Pi",
            model: "connection default",
            budget: "explicit cap required",
          },
          {
            role: "Reviewer",
            provider: "Codex",
            model: "independent context",
            budget: "explicit cap required",
          },
          {
            role: "Viewer",
            provider: "Custom endpoint",
            model: "separate process",
            budget: "explicit cap required",
          },
        ],
  );
  const [notificationState, setNotificationState] =
    useState<NotificationPermission>(
      typeof Notification === "undefined" ? "denied" : Notification.permission,
    );
  const askNotifications = async () => {
    if (!("Notification" in window)) return;
    const permission = await Notification.requestPermission();
    setNotificationState(permission);
    if (permission === "granted")
      new Notification("Argus Research Data Flywheel", {
        body: "页面打开期间的浏览器提醒已启用；服务器会持久化站内提醒事件。",
      });
  };
  const saveSettings = () =>
    tab === "models"
      ? act("settings", { role_models: roleRows })
      : tab === "compute"
        ? act("settings", {
            max_concurrent_campaigns: concurrency,
            emergency_gpu_reserve: reserve,
          })
        : act("settings", { [`${tab}_policy_reviewed`]: true });
  return (
    <>
      <PageHeader
        eyebrow="OPERATOR CONTROL"
        title="Resources & settings"
        actions={
          tab === "compute" || tab === "models" ? (
            <Button
              kind="primary"
              icon={<Save size={15} />}
              onClick={saveSettings}
            >
              {t("action.saveChanges")}
            </Button>
          ) : undefined
        }
      />
      <div className="settings-layout">
        <aside className="settings-nav">
          {(
            [
              ["compute", Cpu, t("settings.tab.compute")],
              ["models", BrainCircuit, t("settings.tab.models")],
              ["notifications", BellRing, t("settings.tab.notifications")],
              ["releases", GitBranch, t("settings.tab.releases")],
              ["appearance", Palette, t("settings.tab.appearance")],
            ] as const
          ).map(([key, Icon, label]) => (
            <button
              className={tab === key ? "active" : ""}
              onClick={() => setTab(key)}
              key={key}
            >
              <Icon size={15} />
              {label}
            </button>
          ))}
        </aside>
        <section className="settings-sheet">
          {tab === "compute" && (
            <>
              <div className="settings-title">
                <div>
                  <h2>Compute pool</h2>
                  <p>
                    任意添加 GPU、CPU、集群或 API-only
                    节点；调度器只使用明确启用的资源。
                  </p>
                </div>
                <div className="page-actions">
                  <Button
                    icon={<RefreshCw size={14} />}
                    onClick={() => act("resources/probe")}
                  >
                    Detect local GPUs
                  </Button>
                  <Button
                    icon={<Plus size={14} />}
                    onClick={() => setAddingResource(true)}
                  >
                    Add resource
                  </Button>
                </div>
              </div>
              {data.resources.gpus.length ? (
                <div className="gpu-grid">
                  {data.resources.gpus.map((gpu) => (
                    <div key={gpu.id}>
                      <div className="gpu-visual">
                        <Cpu size={18} />
                      </div>
                      <div>
                        <strong>{gpu.label}</strong>
                        <span>
                          {gpu.memory} · {gpu.host}
                        </span>
                      </div>
                      <StatusPill tone={gpu.enabled ? "good" : "neutral"}>
                        {gpu.enabled ? "enabled" : "disabled"}
                      </StatusPill>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState
                  title="还没有检测到 GPU"
                  detail="运行只读资源探测，或手工添加 GPU、CPU、集群和 API-only 资源。"
                />
              )}
              <div className="pool-list">
                {data.resources.pools
                  .filter((pool) => pool.type !== "unconfigured")
                  .map((pool) => (
                    <div key={pool.id}>
                      <Boxes size={14} />
                      <strong>{pool.label}</strong>
                      <span>{pool.type}</span>
                      <StatusPill tone={pool.enabled ? "good" : "neutral"}>
                        {pool.enabled ? "enabled" : "disabled"}
                      </StatusPill>
                    </div>
                  ))}
              </div>
              <div className="form-grid">
                <label>
                  <span>Default concurrency</span>
                  <select
                    value={concurrency}
                    onChange={(event) =>
                      setConcurrency(Number(event.target.value))
                    }
                  >
                    <option value="1">1 campaign</option>
                    <option value="2">2 campaigns</option>
                    <option value="3">3 campaigns</option>
                    <option value="4">4 campaigns</option>
                  </select>
                  <small>Heavy multi-GPU runs still require approval.</small>
                </label>
                <label>
                  <span>Emergency reserve</span>
                  <select
                    value={reserve}
                    onChange={(event) => setReserve(Number(event.target.value))}
                  >
                    <option value="0">None</option>
                    <option value="1">1 GPU</option>
                    <option value="2">2 GPUs</option>
                  </select>
                  <small>
                    Reserved for baseline verification and incident recovery.
                  </small>
                </label>
              </div>
            </>
          )}
          {tab === "models" && (
            <>
              <div className="settings-title">
                <div>
                  <h2>Models by Argus role</h2>
                  <p>
                    这些是 Flywheel 保存的偏好与预算合同；实际 backend 必须由目标
                    Argus connection 明确支持并报告，Flywheel 不会假装切换成功。
                  </p>
                </div>
              </div>
              <div className="role-config">
                <div className="role-config-head">
                  <span>ROLE</span>
                  <span>PROVIDER</span>
                  <span>MODEL</span>
                  <span>BUDGET</span>
                </div>
                {roleRows.map((row, index) => (
                  <div key={row.role}>
                    <strong>{row.role}</strong>
                    <select
                      value={row.provider}
                      onChange={(event) =>
                        setRoleRows((rows) =>
                          rows.map((item, position) =>
                            position === index
                              ? { ...item, provider: event.target.value }
                              : item,
                          ),
                        )
                      }
                    >
                      <option>Pi</option>
                      <option>GitHub Copilot</option>
                      <option>Codex</option>
                      <option>OpenAI API</option>
                      <option>Anthropic API</option>
                      <option>Custom endpoint</option>
                    </select>
                    <input
                      value={row.model}
                      onChange={(event) =>
                        setRoleRows((rows) =>
                          rows.map((item, position) =>
                            position === index
                              ? { ...item, model: event.target.value }
                              : item,
                          ),
                        )
                      }
                    />
                    <input
                      value={row.budget}
                      onChange={(event) =>
                        setRoleRows((rows) =>
                          rows.map((item, position) =>
                            position === index
                              ? { ...item, budget: event.target.value }
                              : item,
                          ),
                        )
                      }
                    />
                  </div>
                ))}
              </div>
              <Notice tone="info" title="推荐拓扑">
                保持 Argus 作为研究状态与证据协议的唯一权威。Pi/Copilot/Codex
                作为可替换执行后端；Viewer 必须使用独立进程和 fresh
                context，最好使用不同 provider，减少自评偏差。
              </Notice>
            </>
          )}
          {tab === "notifications" && (
            <>
              <div className="settings-title">
                <div>
                  <h2>Deadline & incident notifications</h2>
                  <p>
                    服务器会持久化并触发站内提醒事件；浏览器通知只在 Flywheel
                    页面保持打开时可靠。当前版本尚未实现 email/webhook 发送器。
                  </p>
                </div>
              </div>
              <div className="notification-state">
                <div className={`notification-glyph ${notificationState}`}>
                  <BellRing size={20} />
                </div>
                <div>
                  <strong>Browser notification: {notificationState}</strong>
                  <p>
                    {notificationState === "denied"
                      ? "浏览器已拒绝权限。请在站点设置中重新允许；系统不会假装已经后台推送。"
                      : notificationState === "granted"
                        ? "页面打开时可以显示即时通知。"
                        : "尚未请求浏览器权限。"}
                  </p>
                </div>
                <Button
                  onClick={askNotifications}
                  disabled={notificationState === "denied"}
                >
                  Enable browser notifications
                </Button>
              </div>
              <Notice tone="info" title="Built-in rules · read only">
                下列规则来自当前调度器实现，不是可编辑偏好；界面不会把未持久化的开关伪装成设置。自定义提醒请使用提醒
                API，完整会议日历可通过下方 ICS 导出。
              </Notice>
              <div className="notification-rules" aria-label="只读内置提醒规则">
                {[
                  "D−180 · Start novelty scan",
                  "D−90 · Winner decision due",
                  "D−30 · Review sprint starts",
                  "Evidence stalled for 30 minutes",
                  "Approval waits more than 4 hours",
                ].map((rule) => (
                  <label key={rule} aria-disabled="true">
                    <input type="checkbox" checked disabled readOnly />
                    <span>
                      <Check size={11} />
                    </span>
                    <strong>{rule}</strong>
                    <select
                      value="In-app event"
                      aria-label={`${rule} delivery`}
                      disabled
                    >
                      <option>In-app event</option>
                    </select>
                  </label>
                ))}
              </div>
              <Notice tone="info" title="Delivery boundary">
                Email 与 webhook 可作为后续 sender
                adapter；在实现、配置并验证前，界面不会把站内事件显示成外部送达。
              </Notice>
              <Button
                icon={<Download size={14} />}
                onClick={() =>
                  downloadCalendar(data.conferences, mode).catch(
                    (error: Error) => toast(`日历下载失败：${error.message}`),
                  )
                }
              >
                Download conference calendar (.ics)
              </Button>
            </>
          )}
          {tab === "releases" && (
            <>
              <div className="settings-title">
                <div>
                  <h2>Release policy</h2>
                  <p>
                    远端变化先进入隔离 staging、测试与
                    canary；不会向正在运行或存在未提交修改的源码目录执行 pull。
                  </p>
                </div>
              </div>
              <div className="policy-lines">
                <div>
                  <LockKeyhole size={16} />
                  <div>
                    <strong>Campaign SHA immutability</strong>
                    <p>从 Idea lock 到最终证据包，全程固定 Argus commit。</p>
                  </div>
                  <StatusPill tone="good">Enforced</StatusPill>
                </div>
                <div>
                  <TestTube2 size={16} />
                  <div>
                    <strong>Two disposable canaries</strong>
                    <p>
                      恢复、bounded completion、Reviewer 与 schema
                      迁移均需通过。
                    </p>
                  </div>
                  <StatusPill tone="good">Required</StatusPill>
                </div>
                <div>
                  <Shield size={16} />
                  <div>
                    <strong>Mission-boundary upgrades only</strong>
                    <p>只为严重修复开放，且必须人工批准。</p>
                  </div>
                  <StatusPill tone="iris">Approval gate</StatusPill>
                </div>
              </div>
            </>
          )}
          {tab === "appearance" && <AppearancePanel />}
        </section>
      </div>
      {addingResource && (
        <div
          className="modal-layer"
          role="dialog"
          aria-modal="true"
          aria-labelledby="resource-title"
          onMouseDown={(e) =>
            e.currentTarget === e.target && setAddingResource(false)
          }
        >
          <form
            className="connection-modal"
            onSubmit={async (e) => {
              e.preventDefault();
              const saved = await act("resources", {
                name: resourceForm.name,
                resource_type: resourceForm.resource_type,
                capacity: {
                  configured: true,
                  gpu_count: resourceForm.gpu_count,
                  gpu_model: resourceForm.gpu_model,
                  gpu_hours: resourceForm.gpu_hours,
                  api_budget: `USD hard cap: ${resourceForm.api_budget}`,
                  max_parallel_jobs: resourceForm.max_parallel_jobs,
                },
                availability_state: "available",
                enabled: true,
              });
              if (saved) setAddingResource(false);
            }}
          >
            <div className="modal-head">
              <div>
                <span className="eyebrow">RESOURCE CONTRACT</span>
                <h2 id="resource-title">Add a compute or API pool</h2>
              </div>
              <button
                type="button"
                className="icon-button"
                onClick={() => setAddingResource(false)}
                aria-label="关闭"
              >
                <X size={17} />
              </button>
            </div>
            <div className="resource-form-grid">
              <label>
                <span>Name</span>
                <input
                  required
                  value={resourceForm.name}
                  onChange={(e) =>
                    setResourceForm((form) => ({
                      ...form,
                      name: e.target.value,
                    }))
                  }
                  placeholder="Local GPU pool"
                />
              </label>
              <label>
                <span>Type</span>
                <select
                  value={resourceForm.resource_type}
                  onChange={(e) =>
                    setResourceForm((form) => ({
                      ...form,
                      resource_type: e.target.value,
                    }))
                  }
                >
                  <option value="gpu_pool">GPU pool</option>
                  <option value="cpu_pool">CPU pool</option>
                  <option value="cluster">Cluster</option>
                  <option value="api_only">API only</option>
                </select>
              </label>
              <label>
                <span>GPU count</span>
                <input
                  type="number"
                  min="0"
                  value={resourceForm.gpu_count}
                  onChange={(e) =>
                    setResourceForm((form) => ({
                      ...form,
                      gpu_count: Number(e.target.value),
                    }))
                  }
                />
              </label>
              <label>
                <span>GPU model</span>
                <input
                  value={resourceForm.gpu_model}
                  onChange={(e) =>
                    setResourceForm((form) => ({
                      ...form,
                      gpu_model: e.target.value,
                    }))
                  }
                  placeholder="GPU model (optional)"
                />
              </label>
              <label>
                <span>GPU-hour cap</span>
                <input
                  type="number"
                  min="0"
                  value={resourceForm.gpu_hours}
                  onChange={(e) =>
                    setResourceForm((form) => ({
                      ...form,
                      gpu_hours: Number(e.target.value),
                    }))
                  }
                />
              </label>
              <label>
                <span>API budget (USD)</span>
                <input
                  type="number"
                  min="0"
                  value={resourceForm.api_budget}
                  onChange={(e) =>
                    setResourceForm((form) => ({
                      ...form,
                      api_budget: Number(e.target.value),
                    }))
                  }
                />
              </label>
              <label>
                <span>Max parallel jobs</span>
                <input
                  type="number"
                  min="1"
                  value={resourceForm.max_parallel_jobs}
                  onChange={(e) =>
                    setResourceForm((form) => ({
                      ...form,
                      max_parallel_jobs: Number(e.target.value),
                    }))
                  }
                />
              </label>
            </div>
            <div className="modal-actions">
              <Button type="button" onClick={() => setAddingResource(false)}>
                Cancel
              </Button>
              <Button type="submit" kind="primary">
                Save resource pool
              </Button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}

async function downloadCalendar(
  conferences: ReturnType<typeof useApp>["data"]["conferences"],
  mode: "live" | "demo",
) {
  if (mode === "live") {
    const response = await fetch(apiUrl("/calendar.ics"));
    if (!response.ok)
      throw new Error(`Calendar download failed: ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "argus-flywheel-conferences.ics";
    anchor.click();
    URL.revokeObjectURL(url);
    return;
  }
  const date = (value: string) => value.replaceAll("-", "");
  const lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Argus Research Data Flywheel//Conference Calendar//CN",
    "CALSCALE:GREGORIAN",
    ...conferences.flatMap((c) => [
      "BEGIN:VEVENT",
      `UID:${c.id}@argus-flywheel`,
      `DTSTART;VALUE=DATE:${date(c.deadline)}`,
      `SUMMARY:${c.acronym} ${c.track} full paper deadline${c.kind === "forecast" ? " (forecast)" : ""}`,
      `DESCRIPTION:${c.name}\\nStatus: ${c.kind}${c.deadlineEnd ? `\\nForecast window ends: ${c.deadlineEnd}` : ""}`,
      "END:VEVENT",
    ]),
    "END:VCALENDAR",
  ];
  const blob = new Blob([lines.join("\r\n")], {
    type: "text/calendar;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "argus-flywheel-conferences.ics";
  anchor.click();
  URL.revokeObjectURL(url);
}

export function ApprovalPage() {
  const { data, act } = useApp();
  const [selectedId, setSelectedId] = useState(data.approvals[0]?.id);
  const [resolved, setResolved] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const approvals = data.approvals.filter(
    (item) => !resolved.includes(item.id),
  );
  const selected =
    approvals.find((item) => item.id === selectedId) ?? approvals[0];
  const decide = async (approval: Approval, decision: "approve" | "reject") => {
    const accepted = await act(`approvals/${approval.id}`, {
      decision,
      reason: reason.trim(),
    });
    if (accepted) {
      setResolved((items) => [...items, approval.id]);
      setReason("");
    }
  };
  return (
    <>
      <PageHeader
        eyebrow="HUMAN AUTHORITY"
        title="Approval inbox"
      />
      {approvals.length === 0 ? (
        <EmptyState
          title="审批箱已清空"
          detail="Argus 会继续执行已授权的工作；遇到下一道人类闸门时会再次通知你。"
        />
      ) : (
        <div className="approval-layout">
          <aside className="approval-queue">
            <div className="queue-head">
              <strong>{approvals.length} WAITING</strong>
              <span>Ordered by current projection</span>
            </div>
            {approvals.map((approval) => (
              <button
                className={selected?.id === approval.id ? "selected" : ""}
                onClick={() => {
                  setSelectedId(approval.id);
                  setReason("");
                }}
                key={approval.id}
              >
                <span className={`risk-mark ${approval.risk}`} />
                <div>
                  <strong>{approval.title}</strong>
                  <p>{approval.campaign}</p>
                  <small>
                    {approval.kind} · {approval.requested}
                  </small>
                </div>
                <ArrowRight size={14} />
              </button>
            ))}
          </aside>
          {selected && (
            <section className="approval-sheet">
              <div className="approval-head">
                <div>
                  <StatusPill
                    tone={
                      selected.risk === "high"
                        ? "bad"
                        : selected.risk === "medium"
                          ? "warn"
                          : "good"
                    }
                  >
                    {selected.risk} consequence
                  </StatusPill>
                  <h2>{selected.title}</h2>
                  <p>{selected.campaign}</p>
                </div>
                <FileCheck2 size={28} />
              </div>
              <div className="decision-question">
                <span>DECISION REQUIRED</span>
                <p>{selected.detail}</p>
              </div>
              <div className="decision-impact">
                <div>
                  <span>IF APPROVED</span>
                  <strong>
                    The scheduler marks this human gate admitted; it does not
                    submit a paper or silently expand the resource contract.
                  </strong>
                </div>
                <div>
                  <span>IF REJECTED</span>
                  <strong>
                    The campaign remains deferred. Existing evidence and the
                    rejected decision stay in the audit trail.
                  </strong>
                </div>
              </div>
              <Notice tone="info" title="What Argus cannot do">
                它不会因为截止时间接近而绕过本次决策，也不会在批准前访问冻结测试集或扩大预算。
              </Notice>
              <label className="approval-reason">
                <span>Required decision reason</span>
                <textarea
                  required
                  rows={3}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="记录批准或拒绝的证据依据、风险判断与边界。"
                />
                <small>
                  原因会随决定写入不可变审计事件；空白原因不会发送。
                </small>
              </label>
              <div className="approval-actions">
                <Button
                  kind="danger"
                  disabled={!reason.trim()}
                  icon={<CircleStop size={14} />}
                  onClick={() => decide(selected, "reject")}
                >
                  Reject & keep paused
                </Button>
                <Button
                  kind="primary"
                  disabled={!reason.trim()}
                  icon={<CheckCircle2 size={14} />}
                  onClick={() => decide(selected, "approve")}
                >
                  Approve this change
                </Button>
              </div>
            </section>
          )}
        </div>
      )}
    </>
  );
}
