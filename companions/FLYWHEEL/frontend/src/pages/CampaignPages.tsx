import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Box,
  Braces,
  Check,
  CheckCircle2,
  ChevronRight,
  CirclePause,
  Clock3,
  Cpu,
  FileCode2,
  FileText,
  Filter,
  Fingerprint,
  Gauge,
  GitCommitHorizontal,
  HardDrive,
  LockKeyhole,
  Pause,
  Play,
  Radio,
  Search,
  ShieldCheck,
  Sparkles,
  SquareActivity,
  Terminal,
  Waypoints,
  X,
} from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useApp } from "../App";
import { loadCampaign, lockWinnerContract } from "../api/client";
import type {
  Campaign,
  CampaignStatus,
  EvidenceClaim,
  LockedContractRequest,
  ViewerReport,
} from "../types";
import {
  Button,
  EmptyState,
  LoadingState,
  Notice,
  PageHeader,
  StatusPill,
} from "../components/ui";
import { useI18n } from "../lib/preferences";

const statusTone = (status: CampaignStatus) =>
  status === "running" || status === "completed"
    ? "good"
    : status === "attention"
      ? "bad"
      : status === "review"
        ? "iris"
        : "neutral";
const statusLabel: Record<CampaignStatus, string> = {
  running: "Running",
  review: "In review",
  attention: "Needs attention",
  paused: "Paused",
  idle: "Not started",
  ready: "Ready",
  completed: "Completed",
  unknown: "Unknown",
};

export function CampaignsPage() {
  const { data } = useApp();
  const { t } = useI18n();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<"all" | CampaignStatus>("all");
  const [query, setQuery] = useState("");
  const campaigns = useMemo(
    () =>
      data.campaigns.filter(
        (campaign) =>
          (filter === "all" || campaign.status === filter) &&
          `${campaign.title} ${campaign.venue} ${campaign.summary}`
            .toLowerCase()
            .includes(query.toLowerCase()),
      ),
    [data.campaigns, filter, query],
  );
  return (
    <>
      <PageHeader
        eyebrow="ACTIVE RESEARCH"
        title="Campaigns"
        actions={
          <Button kind="primary" icon={<Play size={15} />} onClick={() => navigate('/context')}>
            {t("action.createFromContext")}
          </Button>
        }
      />
      <div className="toolbar">
        <div className="search-field">
          <Search size={15} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索 campaign…"
          />
        </div>
        <div className="filter-tabs">
          <Filter size={14} />
          {(["all", "running", "review", "attention", "paused"] as const).map(
            (item) => (
              <button
                className={filter === item ? "active" : ""}
                onClick={() => setFilter(item)}
                key={item}
              >
                {item === "all" ? "All" : statusLabel[item]}
              </button>
            ),
          )}
        </div>
      </div>
      {campaigns.length === 0 ? (
        <EmptyState
          title="没有符合条件的 Campaign"
          detail="调整搜索或状态筛选，或者从会议的候选 Idea 启动一个有界研究任务。"
        />
      ) : (
        <div className="campaign-ledger">
          <div className="ledger-header">
            <span>CAMPAIGN</span>
            <span>ARGUS IS DOING</span>
            <span>EVIDENCE</span>
            <span>RESOURCE</span>
            <span>STATUS</span>
            <span />
          </div>
          {campaigns.map((campaign) => (
            <Link
              className="campaign-ledger-row"
              to={`/campaigns/${campaign.id}`}
              key={campaign.id}
            >
              <div className="campaign-name">
                <span className={`campaign-state ${campaign.status}`} />
                <div>
                  <strong>{campaign.title}</strong>
                  <small>
                    {campaign.venue} · {campaign.commit}
                  </small>
                </div>
              </div>
              <div className="campaign-doing">
                <strong>{campaign.phase}</strong>
                <span>{campaign.summary}</span>
              </div>
              <div className="campaign-evidence">
                <strong>
                  {campaign.tasksDone} / {campaign.tasksTotal}
                </strong>
                <span>tasks committed</span>
                <div className="progress-line">
                  <i style={{ width: `${campaign.progress}%` }} />
                </div>
              </div>
              <div className="campaign-resource">
                <strong>{campaign.gpuHours.toFixed(1)} GPU·h</strong>
                <span>of {campaign.budgetGpuHours}</span>
              </div>
              <StatusPill tone={statusTone(campaign.status)}>
                {statusLabel[campaign.status]}
              </StatusPill>
              <ChevronRight size={16} />
            </Link>
          ))}
        </div>
      )}
    </>
  );
}

const stages = [
  "Idea lock",
  "Research",
  "Pilot",
  "Evidence",
  "Draft",
  "Integrity",
  "Review",
  "Ready",
];
function stageIndex(phase: string) {
  const p = phase.toLowerCase();
  if (p.includes("review")) return 6;
  if (p.includes("draft")) return 4;
  if (p.includes("evidence") || p.includes("confirm")) return 3;
  if (p.includes("pilot")) return 2;
  if (p.includes("novelty")) return 1;
  return 1;
}

export function CampaignDetailPage() {
  const { campaignId } = useParams();
  const navigate = useNavigate();
  const { data, act, mode, refresh, toast } = useApp();
  const [tab, setTab] = useState<"live" | "protocol" | "evidence" | "review" | "artifacts">(
    "live",
  );
  const [contractOpen, setContractOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const dashboardCampaign = data.campaigns.find(
    (item) => item.id === campaignId,
  );
  const [campaign, setCampaign] = useState<Campaign | undefined>(
    dashboardCampaign,
  );
  const [detailLoading, setDetailLoading] = useState(true);
  const [detailError, setDetailError] = useState("");
  useEffect(() => {
    let current = true;
    setCampaign(dashboardCampaign);
    setDetailLoading(true);
    setDetailError("");
    if (!campaignId || mode !== "live") {
      setDetailLoading(false);
      return () => {
        current = false;
      };
    }
    loadCampaign(campaignId)
      .then((loaded) => {
        if (current) setCampaign(loaded);
      })
      .catch((error: Error) => {
        if (current) setDetailError(error.message);
      })
      .finally(() => {
        if (current) setDetailLoading(false);
      });
    return () => {
      current = false;
    };
  }, [campaignId, mode, dashboardCampaign]);
  if (!campaign && detailLoading) return <LoadingState />;
  if (!campaign)
    return (
      <EmptyState
        title="找不到这个 Campaign"
        detail="它可能已归档，或者当前连接还没有同步它。"
        action={
          <Link to="/campaigns" className="button secondary">
            返回 Campaigns
          </Link>
        }
      />
    );
  const currentStage = stageIndex(campaign.phase);
  const campaignConnectionReady = data.connections.some(
    (connection) =>
      connection.id === campaign.connectionId &&
      connection.state === "connected" &&
      connection.backendReady === true,
  );
  return (
    <>
      <div className="cockpit-head">
        <Link to="/campaigns" className="back-link">
          <ArrowLeft size={14} />
          Campaigns
        </Link>
        <div className="cockpit-title">
          <div>
            <span className={`campaign-state ${campaign.status}`} />
            <div>
              <div className="conference-meta">
                <StatusPill tone={statusTone(campaign.status)}>
                  {statusLabel[campaign.status]}
                </StatusPill>
                <span>{campaign.venue}</span>
                <span>
                  {campaign.releasePinned
                    ? `Pinned ${campaign.releaseReference}`
                    : campaign.releaseReference
                      ? `Runtime reported ${campaign.releaseReference}`
                      : "Release not verified"}
                </span>
              </div>
              <h1>{campaign.title}</h1>
              <p>{campaign.objective}</p>
            </div>
          </div>
          <div className="cockpit-actions">
            {(campaign.canStart || campaign.canRetryStart) && campaignConnectionReady && (
              <Button
                icon={<Play size={15} />}
                onClick={() => act(`campaigns/${campaign.id}/start`)}
              >
                {campaign.canRetryStart
                  ? "Retry Argus start"
                  : "Start Portfolio screen"}
              </Button>
            )}
            <Button
              icon={<LockKeyhole size={15} />}
              disabled={!campaign.canLockContract}
              onClick={() => setContractOpen(true)}
            >
              Lock winner contract
            </Button>
            {campaign.canPause && (
              <Button
                icon={<CirclePause size={15} />}
                onClick={() => act(`campaigns/${campaign.id}/pause`)}
              >
                Pause
              </Button>
            )}
            {campaign.canDrain && (
              <Button
                icon={<Waypoints size={15} />}
                onClick={() => act(`campaigns/${campaign.id}/drain`)}
              >
                Drain at boundary
              </Button>
            )}
            {campaign.canReview && (
              <Button
                kind="primary"
                icon={<ShieldCheck size={15} />}
                onClick={() => setReviewOpen(true)}
              >
                Request review
              </Button>
            )}
          </div>
        </div>
      </div>
      {detailError && (
        <Notice tone="warn" title="详情刷新失败">
          当前显示的是最近一次仪表盘快照：{detailError}
        </Notice>
      )}
      {campaign.status === "attention" && (
        <Notice tone="warn" title="需要人工判断">
          {campaign.summary ||
            "Flywheel 收到 needs-attention 状态；请检查最新事件、连接与证据，而不要把它自动解释为 novelty collision。"}
        </Notice>
      )}
      <div className="runtime-truth">
        <div>
          <span
            className={
              campaign.processAlive ? "truth-dot good" : "truth-dot bad"
            }
          />
          <p>
            <strong>Process</strong>
            {campaign.processAlive ? "alive" : "not alive"}
          </p>
        </div>
        <div>
          <span
            className={
              campaign.makingProgress ? "truth-dot good" : "truth-dot warn"
            }
          />
          <p>
            <strong>Evidence progress</strong>
            {campaign.makingProgress ? "advancing" : "not advancing"}
          </p>
        </div>
        <div>
          <span
            className={
              campaign.snapshotStale ? "truth-dot bad" : "truth-dot good"
            }
          />
          <p>
            <strong>Snapshot</strong>
            {campaign.snapshotStale ? "stale" : "current"}
          </p>
        </div>
        <small>这三个信号彼此独立；进程存活不等于科研正在推进。</small>
      </div>
      <section className="phase-rail">
        <div className="phase-label">
          <span>CAMPAIGN TRAJECTORY</span>
          <small>{campaign.elapsed} elapsed</small>
        </div>
        <div className="phase-track">
          {stages.map((stage, index) => (
            <div
              className={`${index < currentStage ? "done" : index === currentStage ? "current" : ""}`}
              key={stage}
            >
              <i>
                {index < currentStage ? <CheckCircle2 size={13} /> : index + 1}
              </i>
              <span>{stage}</span>
            </div>
          ))}
        </div>
      </section>
      <div className="cockpit-metrics">
        <div>
          <Activity size={16} />
          <span>Current work</span>
          <strong>{campaign.phase}</strong>
          <p>
            {campaign.roles.find((r) => r.state === "active")?.task ??
              "Waiting at a gate"}
          </p>
        </div>
        <div>
          <SquareActivity size={16} />
          <span>Committed work</span>
          <strong>
            {campaign.tasksDone} / {campaign.tasksTotal}
          </strong>
          <p>{campaign.progress}% of campaign contract</p>
        </div>
        <div>
          <Cpu size={16} />
          <span>Compute</span>
          <strong>{campaign.gpuHours.toFixed(1)} GPU·h</strong>
          <p>
            {campaign.budgetGpuHours > 0
              ? `${Math.round((campaign.gpuHours / campaign.budgetGpuHours) * 100)}% of approved budget`
              : "Budget telemetry not reported"}
          </p>
        </div>
        <div>
          <GitCommitHorizontal size={16} />
          <span>Runtime source</span>
          <strong>{campaign.releaseReference ?? "not reported"}</strong>
          <p>
            {campaign.source} ·{" "}
            {campaign.releasePinned
              ? "campaign release pinned"
              : campaign.releaseReference
                ? "runtime reference only; pin unverified"
                : "not pinned / unverified"}
          </p>
        </div>
      </div>
      <div className="cockpit-grid">
        <section className="cockpit-main">
          <div className="tab-bar" role="tablist">
            {(["live", "protocol", "evidence", "review", "artifacts"] as const).map(
              (item) => (
                <button
                  role="tab"
                  aria-selected={tab === item}
                  className={tab === item ? "active" : ""}
                  onClick={() => setTab(item)}
                  key={item}
                >
                  {item === "live"
                    ? "Live activity"
                    : item === "protocol"
                      ? "Protocol & prompt"
                    : item === "evidence"
                      ? "Claim ↔ evidence"
                      : item === "review"
                        ? "Review"
                      : item === "artifacts"
                        ? "Paper & artifacts"
                        : item}
                </button>
              ),
            )}
          </div>
          {tab === "live" && <LiveActivity campaign={campaign} />}
          {tab === "protocol" && <ResearchProtocolView campaign={campaign} />}
          {tab === "evidence" && <EvidenceView claims={campaign.claims} />}
          {tab === "review" && <ProjectReviewView reports={data.viewerReports.filter((item) => item.campaignId === campaign.id)} onRequest={() => setReviewOpen(true)} />}
          {tab === "artifacts" && <ArtifactView campaign={campaign} />}
        </section>
        <aside className="role-rail">
          <div className="panel-title">
            <span>ARGUS ROLES</span>
            <Radio size={14} />
          </div>
          {campaign.roles.map((role) => (
            <div className="role-row" key={role.name}>
              <span className={`role-dot ${role.state}`} />
              <div>
                <strong>{role.name}</strong>
                <p>{role.task}</p>
              </div>
              <small>{role.state}</small>
            </div>
          ))}
          <div className="source-lock">
            <HardDrive size={16} />
            <div>
              <strong>
                {campaign.releasePinned
                  ? "Source release pinned"
                  : "Source release not pinned"}
              </strong>
              <p>
                {campaign.releasePinned
                  ? `Campaign config fixes ${campaign.releaseReference}; updates require a safe boundary.`
                  : campaign.releaseReference
                    ? `${campaign.releaseReference} is runtime-reported metadata only; immutability is not verified.`
                    : "No immutable Argus release reference has been recorded for this Campaign."}
              </p>
            </div>
          </div>
        </aside>
      </div>
      {contractOpen && (
        <LockContractModal
          campaign={campaign}
          live={mode === "live"}
          onClose={() => setContractOpen(false)}
          onLocked={async (result) => {
            const hash =
              result.locked_contract?.contract_sha256 ?? result.contract_hash;
            const suffix = hash ? ` · ${hash.slice(0, 12)}` : "";
            const promotedId =
              result.id && result.id !== campaign.id ? result.id : undefined;
            toast(
              promotedId
                ? `Winner 已冻结并晋级为 Locked Campaign${suffix}。Argus 未启动，也未投稿。`
                : `科研合同已冻结${suffix}。Argus 未启动，也未投稿。`,
            );
            setContractOpen(false);
            await refresh();
            if (promotedId) navigate(`/campaigns/${promotedId}`);
          }}
        />
      )}
      {reviewOpen && (
        <ReviewApprovalModal
          campaign={campaign}
          onClose={() => setReviewOpen(false)}
          onApprove={async (reason) => {
            const reviewerKinds = [
              "novelty_reviewer",
              "methods_reviewer",
              "resource_reviewer",
              "venue_reviewer",
              "integrity_reviewer",
            ];
            const accepted = await act(`campaigns/${campaign.id}/review-panel`, {
              reviewer_kinds: reviewerKinds,
              rubrics: Object.fromEntries(
                reviewerKinds.map((kind) => [
                  kind,
                  { independent: true, preserve_disagreement: true },
                ]),
              ),
              human_approved: true,
              actor: "flywheel-ui",
              approval_reason: reason,
            });
            if (accepted) setReviewOpen(false);
          }}
        />
      )}
    </>
  );
}

function ReviewApprovalModal({
  campaign,
  onClose,
  onApprove,
}: {
  campaign: Campaign;
  onClose: () => void;
  onApprove: (reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [approved, setApproved] = useState(false);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!approved || !reason.trim() || busy) return;
    setBusy(true);
    try {
      await onApprove(reason.trim());
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      className="modal-layer"
      role="dialog"
      aria-modal="true"
      aria-labelledby="review-approval-title"
      onMouseDown={(event) =>
        event.currentTarget === event.target && !busy && onClose()
      }
    >
      <div className="start-modal review-approval-modal">
        <div className="modal-head">
          <div>
            <span className="eyebrow">ATTRIBUTABLE REVIEW SPEND</span>
            <h2 id="review-approval-title">Approve a five-reviewer panel?</h2>
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            disabled={busy}
            aria-label="关闭评审确认窗口"
          >
            <X size={17} />
          </button>
        </div>
        <Notice tone="warn" title="This may start five paid evaluators">
          novelty、methods、resource、venue 与 integrity 五位评审会读取同一份不可变
          evidence snapshot，但保持独立上下文和分歧；可能产生 API 或计算费用。评分只是
          readiness 信号，不是录用保证。
        </Notice>
        <div className="contract-identity">
          <ShieldCheck size={18} />
          <div>
            <span>CAMPAIGN</span>
            <strong>{campaign.title}</strong>
            <small>
              {campaign.venue} · {campaign.id}
            </small>
          </div>
        </div>
        <label className="approval-reason">
          <span>Required approval reason</span>
          <textarea
            required
            rows={3}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="说明为什么此刻需要新评审、期望挑战的主张与可接受成本边界。"
          />
        </label>
        <label className="confirm-check contract-approval">
          <input
            type="checkbox"
            checked={approved}
            onChange={(event) => setApproved(event.target.checked)}
          />
          <span>
            <Check size={12} />
          </span>
          <p>
            我批准以 flywheel-ui 身份请求五个独立 Reviewer，并理解缺失证据返回 null、
            分歧不会被平均掉，而且这不会保证录用。
          </p>
        </label>
        <div className="modal-actions">
          <Button onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            kind="primary"
            icon={<ShieldCheck size={15} />}
            disabled={!approved || !reason.trim() || busy}
            onClick={submit}
          >
            {busy ? "Requesting…" : "Approve & request panel"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function LockContractModal({
  campaign,
  live,
  onClose,
  onLocked,
}: {
  campaign: Campaign;
  live: boolean;
  onClose: () => void;
  onLocked: (
    result: Awaited<ReturnType<typeof lockWinnerContract>>,
  ) => Promise<void>;
}) {
  const [primaryClaim, setPrimaryClaim] = useState("");
  const [primaryMetric, setPrimaryMetric] = useState("");
  const [minimumEffect, setMinimumEffect] = useState("");
  const [dataSplit, setDataSplit] = useState("");
  const [confirmatorySeeds, setConfirmatorySeeds] = useState("13, 42, 101");
  const [strongestBaselines, setStrongestBaselines] = useState("");
  const [approvalReason, setApprovalReason] = useState("");
  const [approved, setApproved] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const seedTokens = confirmatorySeeds
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  const seedsValid =
    seedTokens.length > 0 && seedTokens.every((item) => /^\d+$/.test(item));
  const baselines = strongestBaselines
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  const ready =
    live &&
    approved &&
    seedsValid &&
    baselines.length > 0 &&
    [
      primaryClaim,
      primaryMetric,
      minimumEffect,
      dataSplit,
      approvalReason,
    ].every((value) => value.trim().length > 0);
  const submit = async () => {
    if (!ready || submitting) return;
    const payload: LockedContractRequest = {
      primary_claim: primaryClaim.trim(),
      primary_metric: primaryMetric.trim(),
      minimum_effect: minimumEffect.trim(),
      data_split: dataSplit.trim(),
      confirmatory_seeds: [...new Set(seedTokens.map(Number))],
      strongest_baselines: [...new Set(baselines)],
      human_approved: true,
      approval_reason: approvalReason.trim(),
    };
    setSubmitting(true);
    setError("");
    try {
      await onLocked(await lockWinnerContract(campaign.id, payload));
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "合同冻结失败；请检查人工门禁和服务器日志。",
      );
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <div
      className="modal-layer"
      role="dialog"
      aria-modal="true"
      aria-labelledby="lock-contract-title"
      onMouseDown={(event) =>
        event.currentTarget === event.target && !submitting && onClose()
      }
    >
      <div className="start-modal contract-modal">
        <div className="modal-head">
          <div>
            <span className="eyebrow">
              HUMAN SCIENCE GATE · IMMUTABLE VERSION
            </span>
            <h2 id="lock-contract-title">Lock winner contract</h2>
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            disabled={submitting}
            aria-label="关闭合同冻结窗口"
          >
            <X size={17} />
          </button>
        </div>
        <Notice tone="warn" title="冻结的是确认性评测协议，不是录用承诺">
          系统会创建内容寻址的不可变合同版本；不会覆盖旧版本，不会启动
          Argus，也不会触发投稿。后续修改必须生成新版本并留下审计记录。
        </Notice>
        <div className="contract-identity">
          <Fingerprint size={18} />
          <div>
            <span>CAMPAIGN TO FREEZE</span>
            <strong>{campaign.title}</strong>
            <small>
              {campaign.venue} · {campaign.id}
            </small>
          </div>
        </div>
        <div className="lock-contract-form">
          <label className="wide">
            <span>Primary falsifiable claim</span>
            <textarea
              required
              rows={3}
              value={primaryClaim}
              onChange={(event) => setPrimaryClaim(event.target.value)}
              placeholder="写成能够被预先规定的证据推翻的一条主张。"
            />
          </label>
          <label>
            <span>Primary metric</span>
            <input
              required
              value={primaryMetric}
              onChange={(event) => setPrimaryMetric(event.target.value)}
              placeholder="Metric + direction"
            />
          </label>
          <label>
            <span>Minimum effect</span>
            <input
              required
              value={minimumEffect}
              onChange={(event) => setMinimumEffect(event.target.value)}
              placeholder="例如 ≥ 2.0 absolute points"
            />
          </label>
          <label className="wide">
            <span>Frozen data split / evaluation protocol</span>
            <textarea
              required
              rows={2}
              value={dataSplit}
              onChange={(event) => setDataSplit(event.target.value)}
              placeholder="数据版本、train/validation/test 划分、禁止 test tuning 的规则。"
            />
          </label>
          <label>
            <span>Confirmatory seeds</span>
            <input
              required
              spellCheck={false}
              aria-invalid={!seedsValid}
              value={confirmatorySeeds}
              onChange={(event) => setConfirmatorySeeds(event.target.value)}
              placeholder="13, 42, 101"
            />
            <small>仅接受逗号或空格分隔的非负整数。</small>
          </label>
          <label>
            <span>Strongest baselines</span>
            <textarea
              required
              rows={2}
              value={strongestBaselines}
              onChange={(event) => setStrongestBaselines(event.target.value)}
              placeholder={"每行一个，或用逗号分隔"}
            />
            <small>必须包含最强公开基线，而非只选容易击败的对照。</small>
          </label>
          <label className="wide">
            <span>Human approval reason</span>
            <textarea
              required
              rows={2}
              value={approvalReason}
              onChange={(event) => setApprovalReason(event.target.value)}
              placeholder="说明为何该候选胜出、剩余风险，以及为何现在可以冻结。"
            />
          </label>
        </div>
        {!live && (
          <Notice tone="info" title="Demo 模式不会写入合同">
            连接 Live API 后才能执行不可变冻结；当前表单仅用于检查所需信息。
          </Notice>
        )}
        {error && (
          <div className="contract-error" role="alert">
            <AlertTriangle size={15} />
            <span>{error}</span>
          </div>
        )}
        <label className="confirm-check contract-approval">
          <input
            type="checkbox"
            checked={approved}
            onChange={(event) => setApproved(event.target.checked)}
          />
          <span>
            <Check size={12} />
          </span>
          <p>
            我已人工核验以上主张、指标、最小效应、数据划分、确认性 seeds
            与最强基线，并明确批准生成不可变合同版本。我理解此动作本身不会启动
            Argus 或投稿。
          </p>
        </label>
        <div className="modal-actions">
          <Button onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button
            kind="primary"
            icon={<LockKeyhole size={15} />}
            disabled={!ready || submitting}
            onClick={submit}
          >
            {submitting ? "正在冻结…" : "Freeze immutable contract"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function LiveActivity({ campaign }: { campaign: Campaign }) {
  return (
    <div className="activity-stream">
      <div className="stream-head">
        <div>
          <span className="pulse-dot" />
          <strong>Evidence events</strong>
        </div>
        <span>Auto-following</span>
      </div>
      {campaign.events.map((event, index) => (
        <div
          className={`event-row ${event.level === "warn" ? "warn" : ""}`}
          key={`${event.time}-${index}`}
        >
          <time>{event.time}</time>
          <span className="event-line">
            <i />
          </span>
          <div>
            <strong>{event.actor}</strong>
            <p>{event.text}</p>
          </div>
          {event.level === "warn" && <AlertTriangle size={15} />}
        </div>
      ))}
      <div className="stream-tail">
        <Terminal size={14} />
        <span>
          Waiting for the next evidence-path change. PID liveness alone does not
          advance this stream.
        </span>
      </div>
    </div>
  );
}

function EvidenceView({ claims }: { claims: EvidenceClaim[] }) {
  return (
    <div className="evidence-spine">
      <div className="evidence-head">
        <span>CLAIM</span>
        <span>LINK</span>
        <span>EVIDENCE</span>
      </div>
      {claims.map((claim) => (
        <div className={`evidence-row ${claim.strength}`} key={claim.id}>
          <div>
            <span>{claim.id.toUpperCase()}</span>
            <strong>{claim.claim}</strong>
          </div>
          <div className="evidence-link">
            <i />
            <span>{claim.strength}</span>
            <i />
          </div>
          <div>
            <strong>{claim.evidence}</strong>
            <span>Updated {claim.updated}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function ArtifactView({ campaign }: { campaign: Campaign }) {
  return (
    <div className="artifact-table">
      <div className="artifact-head">
        <span>ARTIFACT</span>
        <span>TYPE</span>
        <span>SIZE</span>
        <span>STATE</span>
      </div>
      {campaign.artifacts.map((artifact) => (
        <div className="artifact-row" key={artifact.name}>
          <div>
            {artifact.type === "figure" ? (
              <FileCode2 size={15} />
            ) : artifact.type === "bundle" ? (
              <Box size={15} />
            ) : (
              <FileText size={15} />
            )}
            <strong>{artifact.name}</strong>
          </div>
          <span>{artifact.type}</span>
          <span>{artifact.size}</span>
          <StatusPill
            tone={
              artifact.state === "verified" || artifact.state === "frozen"
                ? "good"
                : artifact.state.includes("needs")
                  ? "warn"
                  : "neutral"
            }
          >
            {artifact.state}
          </StatusPill>
        </div>
      ))}
    </div>
  );
}

function ResearchProtocolView({ campaign }: { campaign: Campaign }) {
  const roles = [
    { label: "BUILDER", title: "Construct the strongest testable direction", detail: "Builds mechanisms, baselines, decisive experiments, and a bounded execution plan." },
    { label: "BREAKER", title: "Try to kill the direction early", detail: "Searches closest work, leakage, confounds, resource traps, and simpler explanations." },
    { label: "ARBITER", title: "Preserve disagreement and decide", detail: "Selects a survivor only from evidence; NO_WINNER and negative results remain valid." },
  ]
  return <div className="protocol-workbench">
    <Notice tone="info" title="协议属于这个项目，不是全局评审开关">
      自由目标、团队条件、会议标准、Builder / Breaker / Arbiter、五位独立 Reviewer、预算与停止条件共同编译成版本化 Prompt。模型和 provider 仍由目标 Argus connection 配置。
    </Notice>
    <div className="protocol-roles">{roles.map((role, index) => <article key={role.label}>
      <i>{String(index + 1).padStart(2, "0")}</i><span>{role.label}</span><strong>{role.title}</strong><p>{role.detail}</p>
    </article>)}</div>
    <div className="protocol-gates">
      <div><span>G0–G3</span><strong>条件、来源、Idea 与 Protocol 人工确认</strong></div>
      <ArrowRight size={14} />
      <div><span>G4–G7</span><strong>Argus 执行、证据、双阶段评审与最终诚信</strong></div>
      <ArrowRight size={14} />
      <div><span>G8–G9</span><strong>外部评审确认与 Dataset seal</strong></div>
    </div>
    <PromptView prompt={campaign.prompt} />
  </div>
}

function ProjectReviewView({ reports, onRequest }: { reports: ViewerReport[]; onRequest: () => void }) {
  if (!reports.length) return <EmptyState
    title="还没有本项目的独立评审"
    detail="Reviewer 会使用 fresh context 和冻结证据包，不共享 Campaign 的隐藏推理。没有证据时返回缺口，不制造评分。"
    action={<Button kind="primary" icon={<ShieldCheck size={14} />} onClick={onRequest}>Request five-reviewer panel</Button>}
  />
  return <div className="project-review-list">{reports.map((report) => <article key={report.id}>
    <header><div><span>{report.venue} · INDEPENDENT EVIDENCE REVIEW</span><h3>{report.verdict}</h3></div><div><strong>{report.overall.toFixed(1)}</strong><small>overall</small></div></header>
    <div className="project-review-dimensions">{report.dimensions.map((dimension) => <div key={dimension.label}>
      <div><span>{dimension.label}</span><strong>{dimension.score.toFixed(1)}</strong></div><i><b style={{ width: `${Math.max(0, Math.min(100, dimension.score * 10))}%` }} /></i><p>{dimension.note}</p>
    </div>)}</div>
    {report.blockers.length > 0 && <div className="review-blockers"><AlertTriangle size={15} /><div><strong>Blocking concerns</strong><ul>{report.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul></div></div>}
  </article>)}</div>
}

function PromptView({ prompt }: { prompt: string }) {
  return (
    <div className="prompt-view">
      <div className="prompt-banner">
        <Braces size={16} />
        <div>
          <strong>Compiled campaign objective</strong>
          <p>
            由全局诚信、会议、领域、Idea
            与资源合同五层合成。修改会生成新版本，不会覆盖原文。
          </p>
        </div>
      </div>
      <pre>{prompt}</pre>
    </div>
  );
}
