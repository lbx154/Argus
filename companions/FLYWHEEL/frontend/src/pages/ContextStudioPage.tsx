import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeftRight,
  Beaker,
  Check,
  CheckCircle2,
  ChevronRight,
  Database,
  Download,
  Fingerprint,
  FlaskConical,
  GitCommitHorizontal,
  Import,
  Layers3,
  Plus,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  UsersRound,
  X,
} from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { useApp } from "../App";
import {
  createIdeationRun,
  createEpisode,
  confirmTeamIntake,
  downloadIdeationTrainingDataset,
  extractTeamIntake,
  importIdeationCandidates,
  labelIdeationCandidate,
  loadIdeationRun,
  loadIdeationRuns,
  loadTeamProfiles,
  savePairwisePreference,
  saveTeamProfile,
} from "../api/client";
import type { IdeationCandidate, IdeationRun, TeamIntakeDraft, TeamProfile } from "../types";
import {
  Button,
  EmptyState,
  Notice,
  PageHeader,
  StatusPill,
} from "../components/ui";
import { useI18n } from "../lib/preferences";

type StudioTab = "profiles" | "runs" | "dataset";
const dimensions = [
  "novelty_evidence",
  "falsifiability",
  "resource_fit",
  "venue_fit",
  "methodological_soundness",
  "integrity_risk",
  "expected_information_gain",
] as const;
const domainPreflight: Record<string, readonly [key: string, label: string]> = {
  HI: [
    "human_subjects_and_ethics_path_reviewed",
    "已确定不涉及真人研究，或已记录参与者招募、知情同意与 IRB/伦理路径。",
  ],
  SC: [
    "dual_use_and_disclosure_path_reviewed",
    "已审查双重用途、隔离测试环境与负责任披露路径。",
  ],
  CT: [
    "proof_expertise_and_checker_plan_reviewed",
    "已核验理论证明所需专家能力、证明检查器或独立 proof review 路径。",
  ],
};
const splitList = (value: string) =>
  value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
const pretty = (value: unknown) => JSON.stringify(value ?? {}, null, 2);
const canonicalJson = (value: unknown): string => {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(",")}}`;
};
const candidateArtifactSha256 = async (candidates: unknown[]): Promise<string> => {
  const bytes = new TextEncoder().encode(`${canonicalJson(candidates)}\n`);
  const digest = await window.crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
};
const parseObject = (value: string, label: string) => {
  const parsed = JSON.parse(value || "{}");
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object")
    throw new Error(`${label} 必须是 JSON object`);
  return parsed as Record<string, unknown>;
};

const blankProfile = {
  name: "",
  expertise: "",
  methods: "",
  dataAccess: "",
  constraints:
    '{\n  "team_size": 2,\n  "person_months": 3,\n  "private_data": false\n}',
  goals:
    '{\n  "contribution": "mechanistic method",\n  "artifact": "reproducible code"\n}',
  policy:
    '{\n  "human_subjects": "not authorized",\n  "positive_result_required": false\n}',
  trainingConsent: false,
  licenseBasis: "",
  enabled: true,
};

type ProfileDraft = typeof blankProfile;

function profileToDraft(profile: TeamProfile): ProfileDraft {
  return {
    name: profile.name,
    expertise: profile.expertise.join(", "),
    methods: profile.methods.join(", "),
    dataAccess: profile.data_access.join("\n"),
    constraints: pretty(profile.constraints),
    goals: pretty(profile.goals),
    policy: pretty(profile.policy),
    trainingConsent: profile.training_consent,
    licenseBasis: profile.license_basis,
    enabled: profile.enabled,
  };
}

export function ContextStudioPage() {
  const { data, mode, toast, refresh } = useApp();
  const { t } = useI18n();
  const [searchParams] = useSearchParams();
  const requestedVenue = searchParams.get("venue");
  const [tab, setTab] = useState<StudioTab>(requestedVenue ? "runs" : "profiles");
  const [profiles, setProfiles] = useState<TeamProfile[]>([]);
  const [runs, setRuns] = useState<IdeationRun[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<string>("");
  const [selectedRunId, setSelectedRunId] = useState<string>("");
  const [runDetail, setRunDetail] = useState<IdeationRun | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (requestedVenue) setTab("runs");
  }, [requestedVenue]);

  const reload = async () => {
    if (mode !== "live") return;
    setLoading(true);
    setError("");
    try {
      const [nextProfiles, nextRuns] = await Promise.all([
        loadTeamProfiles(),
        loadIdeationRuns(),
      ]);
      setProfiles(nextProfiles);
      setRuns(nextRuns);
      if (!selectedProfileId && nextProfiles[0])
        setSelectedProfileId(nextProfiles[0].id);
      if (!selectedRunId && nextRuns[0]) setSelectedRunId(nextRuns[0].id);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Context Studio API unavailable",
      );
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void reload();
  }, [mode]);
  useEffect(() => {
    if (mode === "live" && selectedRunId) {
      const listRecord = runs.find((run) => run.id === selectedRunId);
      loadIdeationRun(selectedRunId)
        .then((detail) => setRunDetail({ ...listRecord, ...detail }))
        .catch((cause: Error) => setError(cause.message));
    } else setRunDetail(null);
  }, [mode, selectedRunId, runs]);

  if (mode !== "live")
    return (
      <>
        <PageHeader
          eyebrow="CONDITIONED RESEARCH"
          title="Context Studio"
        />
        <Notice tone="warn" title="Live API required">
          Demo 模式不会伪造团队画像、个性化
          Idea、条件快照、偏好标签或训练数据资格。连接 Flywheel backend
          后才能创建这些记录。
        </Notice>
        <EmptyState
          title="默认 290 条只是种子"
          detail="它们用于启动调研，不代表任何团队的个性化最终 Idea。Context Studio 会冻结真实条件并生成内容寻址 objective。"
        />
      </>
    );

  return (
    <>
      <PageHeader
        eyebrow="CONDITIONED RESEARCH"
        title="Context Studio"
        actions={
          <div className="studio-tabs">
            {(
              [
                ["profiles", UsersRound, t("context.tab.profiles")],
                ["runs", Sparkles, t("context.tab.runs")],
                ["dataset", Database, t("context.tab.bench")],
              ] as const
            ).map(([key, Icon, label]) => (
              <button
                key={key}
                className={tab === key ? "active" : ""}
                onClick={() => setTab(key)}
              >
                <Icon size={14} />
                {label}
              </button>
            ))}
          </div>
        }
      />
      <Notice tone="info" title="Seed catalog ≠ personalized generation">
        290 个会议候选只是默认种子。个性化 run 会冻结完整条件快照并生成新的
        SHA-256 objective；候选只有在 Argus 或人工以带哈希 artifact
        导入后才存在。
      </Notice>
      {error && (
        <Notice tone="warn" title="Context Studio needs attention">
          {error}
        </Notice>
      )}
      {tab === "profiles" && (
        <ProfilesStudio
          profiles={profiles}
          selectedId={selectedProfileId}
          onSelect={setSelectedProfileId}
          onSaved={async (profile) => {
            setSelectedProfileId(profile.id);
            await reload();
          }}
        />
      )}
      {tab === "runs" && (
        <RunsStudio
          profiles={profiles}
          runs={runs}
          selectedId={selectedRunId}
          detail={runDetail}
          onSelect={setSelectedRunId}
          onRefresh={async (runId) => {
            await reload();
            const detail = await loadIdeationRun(runId);
            setRunDetail({
              ...runs.find((run) => run.id === runId),
              ...detail,
            });
            setSelectedRunId(runId);
            await refresh();
          }}
          data={data}
          toast={toast}
        />
      )}
      {tab === "dataset" && (
        <DatasetStudio profiles={profiles} runs={runs} toast={toast} />
      )}
      {loading && (
        <div className="studio-sync">
          <span className="pulse-dot" />
          Syncing condition records…
        </div>
      )}
    </>
  );
}

function ProfilesStudio({
  profiles,
  selectedId,
  onSelect,
  onSaved,
}: {
  profiles: TeamProfile[];
  selectedId: string;
  onSelect: (id: string) => void;
  onSaved: (profile: TeamProfile) => Promise<void>;
}) {
  const selected = profiles.find((profile) => profile.id === selectedId);
  const [creating, setCreating] = useState(profiles.length === 0);
  const [draft, setDraft] = useState<ProfileDraft>(blankProfile);
  const [saving, setSaving] = useState(false);
  const [intakeText, setIntakeText] = useState("");
  const [intake, setIntake] = useState<TeamIntakeDraft | null>(null);
  const [extracting, setExtracting] = useState(false);
  const [error, setError] = useState("");
  const clearIntakeDraft = () => {
    setIntake(null);
    setIntakeText("");
  };
  useEffect(() => {
    if (!creating) setDraft(selected ? profileToDraft(selected) : blankProfile);
  }, [selected, creating]);
  const save = async () => {
    setError("");
    setSaving(true);
    try {
      if (!draft.name.trim()) throw new Error("团队名称不能为空");
      if (draft.trainingConsent && !draft.licenseBasis.trim())
        throw new Error("允许训练导出时必须填写许可证依据");
      const profilePayload = {
        name: draft.name.trim(),
        expertise: splitList(draft.expertise),
        methods: splitList(draft.methods),
        data_access: splitList(draft.dataAccess),
        constraints: parseObject(draft.constraints, "Constraints"),
        goals: parseObject(draft.goals, "Goals"),
        policy: parseObject(draft.policy, "Policy"),
        training_consent: draft.trainingConsent,
        license_basis: draft.licenseBasis.trim(),
        enabled: draft.enabled,
        metadata: {},
      };
      let profile: TeamProfile;
      if (intake) {
        const confirmed = await confirmTeamIntake(intake.id, profilePayload);
        const profiles = await loadTeamProfiles(true);
        const created = profiles.find((item) => item.id === confirmed.team_profile_id);
        if (!created) throw new Error("团队条件已确认，但无法读取新画像");
        profile = created;
        setIntake(null);
        setIntakeText("");
      } else {
        profile = await saveTeamProfile(
          profilePayload,
          creating ? undefined : selected?.id,
        );
      }
      setCreating(false);
      await onSaved(profile);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Profile save failed");
    } finally {
      setSaving(false);
    }
  };
  const extract = async () => {
    if (!intakeText.trim()) return setError("请先用一句话描述团队条件、资源和目标");
    setError("");
    setExtracting(true);
    try {
      const result = await extractTeamIntake(intakeText.trim());
      const value = result.extracted;
      setIntake(result);
      setCreating(true);
      setDraft({
        name: value.name?.trim() || "待确认团队",
        expertise: (value.expertise ?? []).join(", "),
        methods: (value.methods ?? []).join(", "),
        dataAccess: (value.data_access ?? []).join("\n"),
        constraints: pretty(value.constraints ?? {}),
        goals: pretty(value.goals ?? {}),
        policy: pretty(value.policy ?? { positive_result_required: false }),
        trainingConsent: Boolean(value.training_consent),
        licenseBasis: value.license_basis ?? "",
        enabled: value.enabled ?? true,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "团队条件解析失败");
    } finally {
      setExtracting(false);
    }
  };
  return (
    <div className="studio-layout">
      <aside className="profile-list">
        <div className="studio-list-head">
          <span>TEAM PROFILES</span>
          <button
            disabled={extracting}
            onClick={() => {
              clearIntakeDraft();
              setCreating(true);
              setDraft(blankProfile);
            }}
          >
            <Plus size={13} />
            New
          </button>
        </div>
        {profiles.length === 0 ? (
          <p className="studio-list-empty">还没有团队画像。</p>
        ) : (
          profiles.map((profile) => (
            <button
              disabled={extracting}
              key={profile.id}
              className={
                !creating && profile.id === selectedId ? "selected" : ""
              }
              onClick={() => {
                clearIntakeDraft();
                setCreating(false);
                onSelect(profile.id);
              }}
            >
              <div className="profile-monogram">
                {profile.name.slice(0, 2).toUpperCase()}
              </div>
              <div>
                <strong>{profile.name}</strong>
                <small>
                  {profile.expertise.slice(0, 2).join(" · ") ||
                    "Expertise not specified"}
                </small>
              </div>
              <StatusPill tone={profile.enabled ? "good" : "neutral"}>
                {profile.enabled ? "active" : "disabled"}
              </StatusPill>
            </button>
          ))
        )}
      </aside>
      <section className="profile-editor">
        <div className="studio-section-title">
          <div>
            <span>
              {creating ? "NEW CONDITION SOURCE" : "VERSIONED TEAM CONTEXT"}
            </span>
            <h2>
              {creating
                ? "Create a team profile"
                : (selected?.name ?? "Select a profile")}
            </h2>
            <p>画像只描述真实能力与授权边界，不用于承诺正结果。</p>
          </div>
          <Fingerprint size={26} />
        </div>
        <div className="natural-intake">
          <div className="natural-intake-copy">
            <span><Sparkles size={13} /> 一句话配置</span>
            <strong>先描述真实条件，再由系统提取成可编辑草稿</strong>
            <p>例如：我们 3 人做多模态与系统，有 4×A100、三个月、约 2 亿 token，只能用公开数据，目标是有机制解释且可复现的工作。</p>
          </div>
          <textarea
            rows={3}
            value={intakeText}
            onChange={(event) => setIntakeText(event.target.value)}
            placeholder="用中文或英文描述人员、专长、GPU / token、时间、数据权限、目标与禁区…"
          />
          <Button kind="primary" icon={<Sparkles size={14} />} onClick={extract} disabled={extracting || !intakeText.trim()}>
            {extracting ? "正在提取…" : "提取为团队条件"}
          </Button>
          {intake && <div className="intake-result">
            <StatusPill tone="warn">等待人工确认</StatusPill>
            <span>以下内容只是草稿；保存前可以逐项修正。{intake.uncertainties?.length ? ` 未确定：${intake.uncertainties.join("、")}` : " 未声明的字段保持为空，不会猜测。"}</span>
          </div>}
        </div>
        <div className="profile-form">
          <label className="wide">
            <span>Profile name</span>
            <input
              value={draft.name}
              onChange={(event) =>
                setDraft({ ...draft, name: event.target.value })
              }
              placeholder="Tiny systems team"
            />
          </label>
          <TokenField
            label="Expertise"
            value={draft.expertise}
            onChange={(value) => setDraft({ ...draft, expertise: value })}
            placeholder="compiler runtime, distributed systems"
          />
          <TokenField
            label="Methods"
            value={draft.methods}
            onChange={(value) => setDraft({ ...draft, methods: value })}
            placeholder="causal inference, systems measurement"
          />
          <label className="wide">
            <span>Authorized data access · one per line</span>
            <textarea
              rows={3}
              value={draft.dataAccess}
              onChange={(event) =>
                setDraft({ ...draft, dataAccess: event.target.value })
              }
              placeholder="Public datasets only&#10;Team-owned traces under Apache-2.0"
            />
          </label>
          <JsonField
            label="Constraints"
            value={draft.constraints}
            onChange={(value) => setDraft({ ...draft, constraints: value })}
          />
          <JsonField
            label="Goals"
            value={draft.goals}
            onChange={(value) => setDraft({ ...draft, goals: value })}
          />
          <JsonField
            label="Policy"
            value={draft.policy}
            onChange={(value) => setDraft({ ...draft, policy: value })}
          />
          <div className="consent-panel wide">
            <label className="confirm-check">
              <input
                type="checkbox"
                checked={draft.trainingConsent}
                onChange={(event) =>
                  setDraft({ ...draft, trainingConsent: event.target.checked })
                }
              />
              <span>
                <Check size={12} />
              </span>
              <p>
                <strong>Allow explicit training export</strong> · This only
                marks future, separately redacted human labels as potentially
                eligible. It does not train automatically.
              </p>
            </label>
            <label>
              <span>License basis {draft.trainingConsent && "· required"}</span>
              <input
                value={draft.licenseBasis}
                onChange={(event) =>
                  setDraft({ ...draft, licenseBasis: event.target.value })
                }
                placeholder="Team-owned annotations released for research training"
              />
            </label>
          </div>
          <label className="profile-enabled">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(event) =>
                setDraft({ ...draft, enabled: event.target.checked })
              }
            />
            <span>Enabled for new ideation runs</span>
          </label>
        </div>
        {error && <p className="inline-error">{error}</p>}
        <div className="editor-actions">
          <Button
            kind="primary"
            icon={<Save size={14} />}
            disabled={saving}
            onClick={save}
          >
            {saving
              ? "Saving…"
              : creating
                ? "Create profile"
                : "Save new version"}
          </Button>
        </div>
      </section>
    </div>
  );
}

function TokenField({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}) {
  return (
    <label>
      <span>{label} · comma separated</span>
      <textarea
        rows={3}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
    </label>
  );
}
function JsonField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      <span>{label} · JSON</span>
      <textarea
        className="json-input"
        rows={6}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        spellCheck={false}
      />
    </label>
  );
}

function RunsStudio({
  profiles,
  runs,
  selectedId,
  detail,
  onSelect,
  onRefresh,
  data,
  toast,
}: {
  profiles: TeamProfile[];
  runs: IdeationRun[];
  selectedId: string;
  detail: IdeationRun | null;
  onSelect: (id: string) => void;
  onRefresh: (id: string) => Promise<void>;
  data: ReturnType<typeof useApp>["data"];
  toast: (message: string) => void;
}) {
  const [searchParams] = useSearchParams();
  const requestedVenue = searchParams.get("venue");
  const requestedDeadline = searchParams.get("deadline");
  const targetOptions = useMemo(
    () =>
      data.conferences
        .filter((item) => item.venueKey)
        .sort((a, b) => a.deadline.localeCompare(b.deadline)),
    [data.conferences],
  );
  const [creating, setCreating] = useState(runs.length === 0);
  const [form, setForm] = useState({
    profileId: profiles[0]?.id ?? "",
    targetId:
      targetOptions.find(
        (item) =>
          (item.id === requestedVenue || item.venueKey === requestedVenue) &&
          (!requestedDeadline || String(item.deadlineId ?? "") === requestedDeadline),
      )?.id ?? targetOptions[0]?.id ?? "",
    resourceId:
      data.resources.pools.find(
        (item) => item.enabled && item.type !== "unconfigured",
      )?.id ?? "",
    connectionId:
      data.connections.find((item) => item.state === "connected")?.id ?? "",
    candidateCount: 10,
    finalistCount: 5,
    completionTarget:
      "Produce two resource-feasible survivors or a documented NO_WINNER.",
    sourceRef: "",
    sourceSha: "",
    dataRights: false,
    resourceChecked: false,
    nonComputeChecked: false,
    domainChecked: false,
    humanStart: false,
  });
  const selectedTarget = targetOptions.find(
    (item) => item.id === form.targetId,
  );
  const domainAttestation = domainPreflight[selectedTarget?.area ?? ""];
  const [creatingRun, setCreatingRun] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!form.profileId && profiles[0])
      setForm((current) => ({ ...current, profileId: profiles[0].id }));
  }, [profiles]);
  useEffect(() => {
    if (!requestedVenue) return;
    const target = targetOptions.find(
      (item) =>
        (item.id === requestedVenue || item.venueKey === requestedVenue) &&
        (!requestedDeadline || String(item.deadlineId ?? "") === requestedDeadline),
    );
    if (target) setForm((current) => current.targetId === target.id ? current : { ...current, targetId: target.id, domainChecked: false });
  }, [requestedDeadline, requestedVenue, targetOptions]);
  const create = async () => {
    const target = targetOptions.find((item) => item.id === form.targetId);
    setError("");
    if (!target) return setError("请选择会议与 deadline");
    if (!form.profileId) return setError("请先创建并选择团队画像");
    const sourceRef = form.sourceRef.trim();
    const sourceSha = form.sourceSha.trim().toLowerCase();
    if (Boolean(sourceRef) !== Boolean(sourceSha))
      return setError(
        "Source snapshot reference 与 SHA-256 必须同时填写或同时留空",
      );
    if (sourceSha && !/^[0-9a-f]{64}$/.test(sourceSha))
      return setError("Source SHA-256 必须是恰好 64 位十六进制摘要");
    if (
      !form.dataRights ||
      !form.resourceChecked ||
      !form.nonComputeChecked ||
      (domainAttestation && !form.domainChecked) ||
      !form.humanStart
    )
      return setError("所有适用的 preflight 确认都必须由人完成");
    setCreatingRun(true);
    try {
      const run = await createIdeationRun({
        team_profile_id: form.profileId,
        venue_key: target.venueKey,
        deadline_id: target.deadlineId,
        resource_id: form.resourceId || undefined,
        connection_id: form.connectionId || undefined,
        candidate_count: form.candidateCount,
        finalist_count: form.finalistCount,
        completion_target: form.completionTarget,
        source_snapshot_ref: sourceRef,
        source_snapshot_sha256: sourceSha,
        create_campaign: true,
        preflight_attestations: {
          compute_inventory_and_capacity_verified: form.resourceChecked,
          data_access_and_license_reviewed: form.dataRights,
          non_compute_prerequisites_reviewed: form.nonComputeChecked,
          ...(domainAttestation
            ? { [domainAttestation[0]]: form.domainChecked }
            : {}),
          separate_start_understood: form.humanStart,
        },
      });
      setCreating(false);
      await onRefresh(run.id);
      toast(
        "条件快照和 objective 已冻结；只创建了 idle Campaign，Argus 尚未启动。",
      );
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Ideation run creation failed",
      );
    } finally {
      setCreatingRun(false);
    }
  };
  return (
    <div className="studio-layout run-layout">
      <aside className="profile-list run-list">
        <div className="studio-list-head">
          <span>IDEATION RUNS</span>
          <button onClick={() => setCreating(true)}>
            <Plus size={13} />
            New
          </button>
        </div>
        {runs.length === 0 ? (
          <p className="studio-list-empty">还没有条件化 run。</p>
        ) : (
          runs.map((run) => (
            <button
              key={run.id}
              className={!creating && run.id === selectedId ? "selected" : ""}
              onClick={() => {
                setCreating(false);
                onSelect(run.id);
              }}
            >
              <div className="run-state">
                <i />
              </div>
              <div>
                <strong>{run.venue_name ?? run.venue_key ?? "Venue"}</strong>
                <small>
                  {run.team_name ?? run.team_profile_id} · {run.state}
                </small>
              </div>
              <ChevronRight size={14} />
            </button>
          ))
        )}
      </aside>
      <section className="run-studio">
        {creating ? (
          <div className="run-create">
            <div className="studio-section-title">
              <div>
                <span>FREEZE A CONDITION SNAPSHOT</span>
                <h2>Compile a personalized ideation objective</h2>
                <p>
                  不同画像与资源会生成不同 objective SHA。点击创建不会启动
                  Argus。
                </p>
              </div>
              <SlidersHorizontal size={25} />
            </div>
            <div className="run-form">
              <label>
                <span>Team profile</span>
                <select
                  value={form.profileId}
                  onChange={(event) =>
                    setForm({ ...form, profileId: event.target.value })
                  }
                >
                  <option value="">Select profile</option>
                  {profiles
                    .filter((item) => item.enabled)
                    .map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                <span>Venue / deadline target</span>
                <select
                  value={form.targetId}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      targetId: event.target.value,
                      domainChecked: false,
                    })
                  }
                >
                  {targetOptions.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.acronym} ·{" "}
                      {item.rolling ? "rolling" : item.deadline} · {item.kind}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>Resource contract</span>
                <select
                  value={form.resourceId}
                  onChange={(event) =>
                    setForm({ ...form, resourceId: event.target.value })
                  }
                >
                  <option value="">No resource selected</option>
                  {data.resources.pools
                    .filter(
                      (item) => item.enabled && item.type !== "unconfigured",
                    )
                    .map((item) => (
                      <option value={item.id} key={item.id}>
                        {item.label} · {item.type}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                <span>Argus connection</span>
                <select
                  value={form.connectionId}
                  onChange={(event) =>
                    setForm({ ...form, connectionId: event.target.value })
                  }
                >
                  <option value="">No runtime selected</option>
                  {data.connections
                    .filter((item) => item.state === "connected")
                    .map((item) => (
                      <option value={item.id} key={item.id}>
                        {item.name} · {item.version}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                <span>Candidate count</span>
                <input
                  type="number"
                  min="3"
                  max="20"
                  value={form.candidateCount}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      candidateCount: Number(event.target.value),
                      finalistCount: Math.min(
                        form.finalistCount,
                        Number(event.target.value),
                      ),
                    })
                  }
                />
              </label>
              <label>
                <span>Finalist target</span>
                <input
                  type="number"
                  min="1"
                  max={form.candidateCount}
                  value={form.finalistCount}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      finalistCount: Number(event.target.value),
                    })
                  }
                />
              </label>
              <label className="wide">
                <span>Completion target</span>
                <textarea
                  rows={3}
                  value={form.completionTarget}
                  onChange={(event) =>
                    setForm({ ...form, completionTarget: event.target.value })
                  }
                />
              </label>
              <label>
                <span>Source snapshot reference · optional pair</span>
                <input
                  value={form.sourceRef}
                  onChange={(event) =>
                    setForm({ ...form, sourceRef: event.target.value })
                  }
                  placeholder="s3://… or artifact URI"
                />
              </label>
              <label>
                <span>Source SHA-256 · optional pair</span>
                <input
                  className="mono-input"
                  minLength={64}
                  maxLength={64}
                  pattern="[0-9a-fA-F]{64}"
                  value={form.sourceSha}
                  onChange={(event) =>
                    setForm({ ...form, sourceSha: event.target.value.trim() })
                  }
                  placeholder="64 hex characters"
                />
              </label>
            </div>
            <div className="preflight">
              <span>PREFLIGHT ATTESTATIONS</span>
              <CheckRow
                checked={form.dataRights}
                onChange={(value) => setForm({ ...form, dataRights: value })}
              >
                我核对了数据访问、隐私和许可证边界。
              </CheckRow>
              <CheckRow
                checked={form.resourceChecked}
                onChange={(value) =>
                  setForm({ ...form, resourceChecked: value })
                }
              >
                我核对了计算资源清单、容量和时间窗口。
              </CheckRow>
              <CheckRow
                checked={form.nonComputeChecked}
                onChange={(value) =>
                  setForm({ ...form, nonComputeChecked: value })
                }
              >
                我核对了非计算前置条件（人员、设备、审批与依赖）。
              </CheckRow>
              {domainAttestation && (
                <CheckRow
                  checked={form.domainChecked}
                  onChange={(value) =>
                    setForm({ ...form, domainChecked: value })
                  }
                >
                  {domainAttestation[1]}
                </CheckRow>
              )}
              <CheckRow
                checked={form.humanStart}
                onChange={(value) => setForm({ ...form, humanStart: value })}
              >
                我理解创建 run 只产生 idle Campaign；之后仍需单独 Start。
              </CheckRow>
              <p className="preflight-warning">
                这只冻结 ideation 条件，不代表
                execution-ready。当前会议的适用领域证明已明确列出；Campaign
                Start 会再次检查资源合同和所有证明并阻止缺项启动。
              </p>
            </div>
            {error && <p className="inline-error">{error}</p>}
            <div className="editor-actions">
              <Button
                kind="primary"
                icon={<Fingerprint size={14} />}
                disabled={creatingRun}
                onClick={create}
              >
                {creatingRun
                  ? "Freezing…"
                  : "Freeze objective & create idle Campaign"}
              </Button>
            </div>
          </div>
        ) : detail ? (
          <RunDetail
            run={detail}
            onRefresh={() => onRefresh(detail.id)}
            toast={toast}
          />
        ) : (
          <EmptyState
            title="选择一个 ideation run"
            detail="查看条件快照、内容哈希、idle Campaign 与候选评审。"
          />
        )}
      </section>
    </div>
  );
}

function CheckRow({
  checked,
  onChange,
  children,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  children: React.ReactNode;
}) {
  return (
    <label className="confirm-check">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>
        <Check size={12} />
      </span>
      <p>{children}</p>
    </label>
  );
}

function RunDetail({
  run,
  onRefresh,
  toast,
}: {
  run: IdeationRun;
  onRefresh: () => Promise<void>;
  toast: (message: string) => void;
}) {
  const candidates = run.candidates ?? [];
  const [section, setSection] = useState<
    "snapshot" | "candidates" | "pairwise"
  >("snapshot");
  const [importOpen, setImportOpen] = useState(false);
  const [importJson, setImportJson] = useState("[]");
  const [artifactSha, setArtifactSha] = useState("");
  const [episodeId, setEpisodeId] = useState("");
  const [creatingEpisode, setCreatingEpisode] = useState(false);
  const [episodeCandidate, setEpisodeCandidate] =
    useState<IdeationCandidate | null>(null);
  const [selectedCandidate, setSelectedCandidate] =
    useState<IdeationCandidate | null>(null);
  useEffect(() => {
    setEpisodeCandidate(null);
    setEpisodeId("");
  }, [run.id]);
  const captureEpisode = async () => {
    setCreatingEpisode(true);
    try {
      if (!run.objective?.trim()) {
        throw new Error("冻结的 Argus Objective 不可读取；请先校验该 ideation run，不能用泛化文本替代");
      }
      if (!episodeCandidate) {
        throw new Error("请先从候选列表明确选择一个 Idea");
      }
      const venueSnapshot = run.condition_snapshot.venue && typeof run.condition_snapshot.venue === "object"
        ? run.condition_snapshot.venue as Record<string, unknown>
        : {};
      const episode = await createEpisode({
        title: `${run.venue_name ?? run.venue_key ?? "Research"} · ${episodeCandidate.title}`,
        objective: run.objective,
        team_profile_id: run.team_profile_id,
        venue_id: typeof venueSnapshot.id === "number" ? venueSnapshot.id : undefined,
        deadline_id: run.deadline_id,
        ideation_run_id: run.id,
        candidate_id: episodeCandidate.id,
        campaign_id: run.campaign_id,
        training_consent: run.training_consent,
        license_basis: run.license_basis,
        metadata: {
          phase: "IDEA_SELECTED",
          research_protocol_version: run.condition_snapshot.research_protocol_version ?? "v2",
          selected_candidate_key: episodeCandidate.candidate_key,
          selected_candidate_title: episodeCandidate.title,
        },
      });
      setEpisodeId(episode.id);
      toast("Research Episode 已建立；后续版本只追加，不覆盖。 ");
    } catch (cause) {
      toast(`Episode 创建失败：${cause instanceof Error ? cause.message : "unknown error"}`);
    } finally {
      setCreatingEpisode(false);
    }
  };
  return (
    <>
      <div className="run-header">
        <div>
          <div className="conference-meta">
            <StatusPill tone="iris">{run.state}</StatusPill>
            <span>{run.team_name ?? run.team_profile_id}</span>
            <span>{run.venue_name ?? run.venue_key}</span>
          </div>
          <h2>Conditioned ideation</h2>
          <p>
            {run.campaign_id
              ? "Objective frozen · idle Campaign created · Argus not started"
              : "Objective frozen · no Campaign created"}
          </p>
        </div>
        <div className="run-launch-state">
          <span className="idle-beacon" />
          <div>
            <strong>NOT STARTED</strong>
            <small>Explicit Start is still required</small>
          </div>
          {run.campaign_id && (
            <Link
              className="button secondary"
              to={`/campaigns/${run.campaign_id}`}
            >
              <FlaskConical size={13} />
              Review idle Campaign
            </Link>
          )}
          {episodeId ? (
            <Link className="button primary" to={`/data-vault?episode=${encodeURIComponent(episodeId)}`}>
              <Database size={13} />
              Open Data Episode
            </Link>
          ) : (
            <Button
              kind="primary"
              icon={<Database size={13} />}
              onClick={captureEpisode}
              disabled={creatingEpisode || !episodeCandidate || !run.objective?.trim()}
            >
              {creatingEpisode ? "Capturing…" : episodeCandidate ? "Create Episode from selection" : "Select an Idea below"}
            </Button>
          )}
        </div>
      </div>
      <div className="objective-spine">
        <div>
          <Fingerprint size={16} />
          <span>CONDITION SHA</span>
          <strong>
            {run.condition_sha256 ?? "Not returned by detail endpoint"}
          </strong>
        </div>
        <i />
        <div>
          <GitCommitHorizontal size={16} />
          <span>OBJECTIVE SHA-256</span>
          <strong>{run.objective_sha256}</strong>
        </div>
        <i />
        <div>
          <Layers3 size={16} />
          <span>SCHEMA</span>
          <strong>v{run.condition_schema_version}</strong>
        </div>
      </div>
      <div className="run-tabs">
        {(["snapshot", "candidates", "pairwise"] as const).map((item) => (
          <button
            key={item}
            className={section === item ? "active" : ""}
            onClick={() => setSection(item)}
          >
            {item === "snapshot"
              ? "Condition snapshot"
              : item === "candidates"
                ? `Candidates (${candidates.length})`
                : `Pairwise (${run.pairwise_preferences?.length ?? 0})`}
          </button>
        ))}
      </div>
      {section === "snapshot" && <ConditionSnapshot run={run} />}
      {section === "candidates" && (
        <div className="candidate-workbench">
          {candidates.length === 0 ? (
            <EmptyState
              title="还没有生成候选"
              detail="这是诚实的空状态：Flywheel 已冻结 objective，但不会伪装 Argus 已完成 ideation。先在 Campaign cockpit 人工 Start；完成后从 Argus artifact 导入带 SHA-256 的候选，或手工录入完整候选 JSON。"
              action={
                <Button
                  icon={<Import size={14} />}
                  onClick={() => setImportOpen(true)}
                >
                  Import candidate artifact
                </Button>
              }
            />
          ) : (
            <div className="candidate-list">
              {candidates.map((candidate, index) => (
                <article key={candidate.id}>
                  <header>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <div>
                      <h3>{candidate.title}</h3>
                      <p>
                        {String(
                          candidate.candidate.core_hypothesis ??
                            candidate.candidate.problem_gap ??
                            "No hypothesis reported",
                        )}
                      </p>
                    </div>
                    <StatusPill
                      tone={candidate.labels.length ? "good" : "neutral"}
                    >
                      {candidate.labels.length} labels
                    </StatusPill>
                  </header>
                  <div className="candidate-facts">
                    <div>
                      <span>DIFFERENTIATION</span>
                      <p>
                        {String(
                          candidate.candidate.differentiation_claim ??
                            "Not reported",
                        )}
                      </p>
                    </div>
                    <div>
                      <span>FALSIFIER</span>
                      <p>
                        {String(
                          candidate.candidate.falsifier ?? "Not reported",
                        )}
                      </p>
                    </div>
                    <div>
                      <span>RESOURCE ESTIMATE</span>
                      <p>{pretty(candidate.candidate.estimated_resources)}</p>
                    </div>
                  </div>
                  <footer>
                    <span>
                      {candidate.imported_from} ·{" "}
                      {candidate.evidence_refs.length} evidence refs
                    </span>
                    <Button
                      kind={episodeCandidate?.id === candidate.id ? "primary" : "secondary"}
                      icon={<CheckCircle2 size={13} />}
                      onClick={() => setEpisodeCandidate(candidate)}
                    >
                      {episodeCandidate?.id === candidate.id ? "Selected for Episode" : "Use this Idea"}
                    </Button>
                    <Button
                      icon={<Beaker size={13} />}
                      onClick={() => setSelectedCandidate(candidate)}
                    >
                      Label candidate
                    </Button>
                  </footer>
                </article>
              ))}
            </div>
          )}
        </div>
      )}
      {section === "pairwise" && (
        <PairwisePanel run={run} onSaved={onRefresh} />
      )}
      {importOpen && (
        <div
          className="modal-layer"
          role="dialog"
          aria-modal="true"
          aria-label="导入候选"
        >
          <form
            className="start-modal"
            onSubmit={async (event) => {
              event.preventDefault();
              try {
                const parsed = JSON.parse(importJson);
                if (!Array.isArray(parsed) || parsed.length === 0)
                  throw new Error("Candidates 必须是非空 JSON array");
                const computedSha = await candidateArtifactSha256(parsed);
                if (artifactSha && artifactSha.toLowerCase() !== computedSha) {
                  throw new Error(`输入 SHA 与 canonical candidate array 不匹配；应为 ${computedSha}`);
                }
                setArtifactSha(computedSha);
                if (!run.condition_sha256 || !/^[0-9a-f]{64}$/i.test(run.condition_sha256)) {
                  throw new Error("Ideation run 缺少可验证的 condition SHA-256");
                }
                if (!/^[0-9a-f]{64}$/i.test(run.objective_sha256)) {
                  throw new Error("Ideation run 缺少可验证的 objective SHA-256");
                }
                await importIdeationCandidates(run.id, {
                  candidates: parsed,
                  imported_from: "human_entered",
                  artifact_sha256: computedSha,
                  manifest: {
                    schema_version: "flywheel.ideation-candidates/1",
                    condition_sha256: run.condition_sha256,
                    objective_sha256: run.objective_sha256,
                    candidates_sha256: computedSha,
                    candidate_count: parsed.length,
                  },
                });
                setImportOpen(false);
                await onRefresh();
                toast("候选 artifact 已按哈希导入，后续不可覆盖。");
              } catch (cause) {
                toast(
                  `导入失败：${cause instanceof Error ? cause.message : "invalid artifact"}`,
                );
              }
            }}
          >
            <div className="modal-head">
              <div>
                <span className="eyebrow">IMMUTABLE CANDIDATE IMPORT</span>
                <h2>Import a complete candidate array</h2>
              </div>
              <button
                type="button"
                className="icon-button"
                onClick={() => setImportOpen(false)}
              >
                <X size={15} />
              </button>
            </div>
            <Notice tone="warn" title="Import once">
              候选导入后不可覆盖。需要另一个版本时请创建新的 ideation run。
            </Notice>
            <label className="modal-field">
              <span>Canonical candidate array SHA-256 · 留空则由浏览器计算，服务端会独立复算</span>
              <input
                maxLength={64}
                value={artifactSha}
                onChange={(event) => setArtifactSha(event.target.value.trim())}
                placeholder="optional 64-hex assertion"
              />
            </label>
            <label className="modal-field">
              <span>Complete candidates JSON</span>
              <textarea
                className="json-input"
                rows={15}
                value={importJson}
                onChange={(event) => setImportJson(event.target.value)}
              />
            </label>
            <div className="modal-actions">
              <Button type="button" onClick={() => setImportOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" kind="primary">
                Validate & import
              </Button>
            </div>
          </form>
        </div>
      )}
      {selectedCandidate && (
        <CandidateLabelModal
          candidate={selectedCandidate}
          onClose={() => setSelectedCandidate(null)}
          onSaved={async () => {
            setSelectedCandidate(null);
            await onRefresh();
          }}
        />
      )}
    </>
  );
}

function ConditionSnapshot({ run }: { run: IdeationRun }) {
  const snapshot = run.condition_snapshot ?? {};
  const entries = Object.entries(snapshot).filter(
    ([key]) => key !== "schema_version",
  );
  return (
    <div className="condition-map">
      <div className="condition-origin">
        <Fingerprint size={21} />
        <div>
          <span>FROZEN INPUT</span>
          <strong>{run.objective_sha256.slice(0, 16)}…</strong>
        </div>
      </div>
      <div className="condition-branches">
        {entries.length ? (
          entries.map(([key, value]) => (
            <article key={key}>
              <span>{key.replaceAll("_", " ").toUpperCase()}</span>
              <pre>{pretty(value)}</pre>
            </article>
          ))
        ) : (
          <EmptyState
            title="条件快照为空"
            detail="后端未返回 condition_snapshot；系统不会依据 profile 名称猜测条件。"
          />
        )}
      </div>
      {run.objective && (
        <details className="objective-preview">
          <summary>Compiled objective preview</summary>
          <pre>{run.objective}</pre>
        </details>
      )}
      <div className="snapshot-source">
        <span>SOURCE SNAPSHOT</span>
        <strong>
          {run.source_snapshot_ref || "No external source snapshot bound"}
        </strong>
        <code>{run.source_snapshot_sha256 || "No source SHA reported"}</code>
      </div>
    </div>
  );
}

function CandidateLabelModal({
  candidate,
  onClose,
  onSaved,
}: {
  candidate: IdeationCandidate;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [form, setForm] = useState({
    alias: "",
    decision: "shortlist",
    rationale: "",
    redacted: false,
    consent: false,
    license: "",
    dimensions: Object.fromEntries(
      dimensions.map((item) => [item, item === "integrity_risk" ? 3 : 7]),
    ) as Record<string, number>,
  });
  const [error, setError] = useState("");
  return (
    <div
      className="modal-layer"
      role="dialog"
      aria-modal="true"
      aria-label="候选标注"
    >
      <form
        className="start-modal candidate-label-modal"
        onSubmit={async (event) => {
          event.preventDefault();
          setError("");
          try {
            if (form.consent && !form.license.trim())
              throw new Error("训练导出同意需要许可证依据");
            await labelIdeationCandidate(candidate.id, {
              labeler_alias: form.alias,
              decision: form.decision,
              dimensions: form.dimensions,
              rationale_redacted: form.rationale,
              redaction_confirmed: form.redacted,
              training_consent: form.consent,
              license_basis: form.license,
            });
            await onSaved();
          } catch (cause) {
            setError(
              cause instanceof Error ? cause.message : "Label save failed",
            );
          }
        }}
      >
        <div className="modal-head">
          <div>
            <span className="eyebrow">HUMAN EVIDENCE LABEL</span>
            <h2>{candidate.title}</h2>
          </div>
          <button type="button" className="icon-button" onClick={onClose}>
            <X size={15} />
          </button>
        </div>
        <div className="label-grid">
          {dimensions.map((dimension) => (
            <label key={dimension}>
              <span>{dimension.replaceAll("_", " ")}</span>
              <input
                type="number"
                min="0"
                max="10"
                step="0.5"
                value={form.dimensions[dimension]}
                onChange={(event) =>
                  setForm({
                    ...form,
                    dimensions: {
                      ...form.dimensions,
                      [dimension]: Number(event.target.value),
                    },
                  })
                }
              />
            </label>
          ))}
        </div>
        <div className="form-grid">
          <label>
            <span>Labeler alias</span>
            <input
              required
              value={form.alias}
              onChange={(event) =>
                setForm({ ...form, alias: event.target.value })
              }
              placeholder="Reviewer 1"
            />
          </label>
          <label>
            <span>Decision</span>
            <select
              value={form.decision}
              onChange={(event) =>
                setForm({ ...form, decision: event.target.value })
              }
            >
              <option>shortlist</option>
              <option>revise</option>
              <option>reject</option>
              <option>abstain</option>
            </select>
          </label>
        </div>
        <label className="modal-field">
          <span>Redacted rationale</span>
          <textarea
            required
            rows={4}
            value={form.rationale}
            onChange={(event) =>
              setForm({ ...form, rationale: event.target.value })
            }
          />
        </label>
        <CheckRow
          checked={form.redacted}
          onChange={(value) => setForm({ ...form, redacted: value })}
        >
          我确认 rationale 已脱敏且不包含个人信息。
        </CheckRow>
        <CheckRow
          checked={form.consent}
          onChange={(value) => setForm({ ...form, consent: value })}
        >
          允许这条人工标注进入明确导出的训练数据集；不代表自动训练。
        </CheckRow>
        {form.consent && (
          <label className="modal-field">
            <span>License basis</span>
            <input
              required
              value={form.license}
              onChange={(event) =>
                setForm({ ...form, license: event.target.value })
              }
            />
          </label>
        )}
        {error && <p className="inline-error">{error}</p>}
        <div className="modal-actions">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button kind="primary" type="submit" disabled={!form.redacted}>
            Save label
          </Button>
        </div>
      </form>
    </div>
  );
}

function PairwisePanel({
  run,
  onSaved,
}: {
  run: IdeationRun;
  onSaved: () => Promise<void>;
}) {
  const candidates = run.candidates ?? [];
  const [form, setForm] = useState({
    left: candidates[0]?.id ?? "",
    right: candidates[1]?.id ?? "",
    winner: "left",
    alias: "",
    rationale: "",
    redacted: false,
    consent: false,
    license: "",
  });
  const [error, setError] = useState("");
  if (candidates.length < 2)
    return (
      <EmptyState
        title="至少需要两个候选"
        detail="先从真实 Argus artifact 导入候选，再记录两两偏好。系统不会从固定 290 种子自动构造比较对。"
      />
    );
  const candidateName = (id: string) =>
    candidates.find((item) => item.id === id)?.title ?? "Select candidate";
  return (
    <div className="pairwise-panel">
      <div className="pairwise-stage">
        <div>
          <span>LEFT</span>
          <select
            value={form.left}
            onChange={(event) => setForm({ ...form, left: event.target.value })}
          >
            {candidates.map((item) => (
              <option value={item.id} key={item.id}>
                {item.title}
              </option>
            ))}
          </select>
          <strong>{candidateName(form.left)}</strong>
        </div>
        <div className="versus">
          <ArrowLeftRight size={20} />
          <span>PAIRWISE</span>
        </div>
        <div>
          <span>RIGHT</span>
          <select
            value={form.right}
            onChange={(event) =>
              setForm({ ...form, right: event.target.value })
            }
          >
            {candidates.map((item) => (
              <option value={item.id} key={item.id}>
                {item.title}
              </option>
            ))}
          </select>
          <strong>{candidateName(form.right)}</strong>
        </div>
      </div>
      <div className="pairwise-form">
        <label>
          <span>Winner</span>
          <select
            value={form.winner}
            onChange={(event) =>
              setForm({ ...form, winner: event.target.value })
            }
          >
            <option value="left">Left</option>
            <option value="right">Right</option>
            <option value="tie">Tie</option>
            <option value="abstain">Abstain</option>
          </select>
        </label>
        <label>
          <span>Labeler alias</span>
          <input
            value={form.alias}
            onChange={(event) =>
              setForm({ ...form, alias: event.target.value })
            }
          />
        </label>
        <label className="wide">
          <span>Redacted rationale</span>
          <textarea
            rows={4}
            value={form.rationale}
            onChange={(event) =>
              setForm({ ...form, rationale: event.target.value })
            }
          />
        </label>
        <CheckRow
          checked={form.redacted}
          onChange={(value) => setForm({ ...form, redacted: value })}
        >
          Rationale 已脱敏。
        </CheckRow>
        <CheckRow
          checked={form.consent}
          onChange={(value) => setForm({ ...form, consent: value })}
        >
          允许显式训练集导出。
        </CheckRow>
        {form.consent && (
          <label className="wide">
            <span>License basis</span>
            <input
              value={form.license}
              onChange={(event) =>
                setForm({ ...form, license: event.target.value })
              }
            />
          </label>
        )}
      </div>
      {error && <p className="inline-error">{error}</p>}
      <div className="editor-actions">
        <Button
          kind="primary"
          icon={<ArrowLeftRight size={14} />}
          disabled={
            !form.alias ||
            !form.rationale ||
            !form.redacted ||
            form.left === form.right
          }
          onClick={async () => {
            setError("");
            try {
              if (form.consent && !form.license.trim())
                throw new Error("训练导出同意需要许可证依据");
              await savePairwisePreference(run.id, {
                left_candidate_id: form.left,
                right_candidate_id: form.right,
                winner: form.winner,
                labeler_alias: form.alias,
                rationale_redacted: form.rationale,
                redaction_confirmed: form.redacted,
                training_consent: form.consent,
                license_basis: form.license,
              });
              await onSaved();
            } catch (cause) {
              setError(
                cause instanceof Error ? cause.message : "Pairwise save failed",
              );
            }
          }}
        >
          Save comparison
        </Button>
      </div>
      <div className="preference-ledger">
        <span>RECORDED PREFERENCES</span>
        {(run.pairwise_preferences ?? []).length ? (
          (run.pairwise_preferences ?? []).map((item, index) => (
            <div key={String(item.id ?? index)}>
              <strong>{candidateName(String(item.left_candidate_id))}</strong>
              <StatusPill tone="iris">{String(item.winner)}</StatusPill>
              <strong>{candidateName(String(item.right_candidate_id))}</strong>
              <small>{String(item.labeler_alias)}</small>
            </div>
          ))
        ) : (
          <p>No pairwise preference has been recorded.</p>
        )}
      </div>
    </div>
  );
}

function DatasetStudio({
  profiles,
  runs,
  toast,
}: {
  profiles: TeamProfile[];
  runs: IdeationRun[];
  toast: (message: string) => void;
}) {
  const { t } = useI18n();
  const consentProfiles = profiles.filter(
    (profile) => profile.training_consent && profile.license_basis,
  );
  return (
    <div className="dataset-sheet">
      <div className="dataset-hero">
        <div className="dataset-mark">
          <Database size={28} />
          <i />
        </div>
        <div>
          <span>CONSENT-GATED JSONL</span>
          <h2>{t("context.bench.title")}</h2>
          <p>
            导出只包含分别满足同意、许可证与脱敏确认的 scalar labels 和 pairwise
            preferences。每个 ideation run 被保持在同一个 group-safe
            split，避免条件泄漏。
          </p>
        </div>
        <Button
          kind="primary"
          icon={<Download size={14} />}
          onClick={() =>
            downloadIdeationTrainingDataset()
              .then((meta) =>
                toast(
                  `已导出 ${meta.count} 条记录；automatic-training=${meta.automatic}`,
                ),
              )
              .catch((cause: Error) => toast(`导出失败：${cause.message}`))
          }
        >
          Export eligible JSONL
        </Button>
      </div>
      <div className="dataset-metrics">
        <div>
          <span>ELIGIBLE PROFILES</span>
          <strong>{consentProfiles.length}</strong>
          <p>of {profiles.length} profiles</p>
        </div>
        <div>
          <span>IDEATION RUNS</span>
          <strong>{runs.length}</strong>
          <p>record counts are verified at export</p>
        </div>
        <div>
          <span>ELIGIBLE RECORDS</span>
          <strong>SERVER</strong>
          <p>the download response reports the exact count</p>
        </div>
        <div>
          <span>AUTOMATIC TRAINING</span>
          <strong>OFF</strong>
          <p>export does not start training</p>
        </div>
      </div>
      <div className="eligibility-rules">
        <span>RECORD ELIGIBILITY</span>
        {[
          "Human labeler uses a pseudonymous alias",
          "Rationale is explicitly confirmed as redacted",
          "Training-export consent is true for that record",
          "A non-empty license basis is recorded",
          "Left/right candidates remain grouped by ideation run",
        ].map((rule) => (
          <div key={rule}>
            <CheckCircle2 size={14} />
            <strong>{rule}</strong>
          </div>
        ))}
      </div>
      <Notice tone="warn" title="Export is not model training">
        下载数据集不会调用 Pi、Copilot、Codex、Argus
        或任何训练服务。是否训练、用什么模型与如何审计，需要另一个明确授权流程。
      </Notice>
    </div>
  );
}
