import { useState } from "react";
import { api, type ConfigSnapshot } from "../api";
import { useI18n } from "../i18n";

const EFFORT_OPTIONS = [["low", "低"], ["medium", "中"], ["high", "高"], ["xhigh", "很高"], ["max", "最高"]] as const;

export function MapModelSettings({
  sid, config, onSaved,
}: {
  sid: string;
  config: ConfigSnapshot;
  onSaved: () => Promise<unknown>;
}) {
  const { locale } = useI18n();
  const zh = locale === "zh-CN";
  const research = config.roles.find((role) => role.role === "engineer");
  const values = new Map(config.operator_knobs.map((knob) => [knob.name, knob.value]));
  const selectedModel = values.get("ARGUS_SKILL_MAP_MODEL") || "auto";
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const modelOptions = (config.model_options ?? []).map((option) => option.model);
  if (selectedModel !== "auto" && !modelOptions.includes(selectedModel)) modelOptions.unshift(selectedModel);
  const save = async (name: string, value: string) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await api.setConfig(sid, name, value);
      await onSaved();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };
  const follow = zh ? "跟随科研设置" : "Follow research settings";
  const followModel = zh ? "跟随执行模型" : "Follow the execution model";
  const effortSettings = [
    { name: "ARGUS_SKILL_MAP_REASONING_EFFORT", label: zh ? "讲解生成强度" : "Explanation effort", follow },
    { name: "ARGUS_SKILL_MAP_REVIEW_REASONING_EFFORT", label: zh ? "讲解复核强度" : "Explanation review effort",
      follow: zh ? "跟随讲解生成强度" : "Follow explanation effort" },
  ];
  return (
    <section className="map-model-settings rounded-lg border border-line glass-card p-3" aria-label={zh ? "地图模型" : "Map model"}>
      <div className="text-xs font-semibold text-ink">{zh ? "地图模型" : "Map model"}</div>
      <p className="mt-1 text-xs text-ink-dim">
        {zh ? "地图讲解与摘要用的模型；沿用执行模型的接入与账号。可以选一个便宜的。" : "The model that writes map explanations and summaries, on the execution model's runner and account. A cheaper one is fine."}
      </p>
      <p className="mt-1 text-xs text-ink-faint">
        {research?.backend_label} · {research?.model || (zh ? "接入默认模型" : "Runner default model")}
      </p>
      <label className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-dim">
        {zh ? "摘要模型" : "Summary model"}
        <select value={selectedModel} disabled={busy} aria-label={zh ? "摘要模型" : "Summary model"}
          onChange={(event) => void save("ARGUS_SKILL_MAP_MODEL", event.target.value)}
          className="h-9 min-w-0 max-w-full rounded border border-line bg-bg px-2 font-mono text-xs text-ink outline-none focus:border-blue">
          <option value="auto">{followModel}</option>
          {modelOptions.map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
      </label>
      {effortSettings.map((setting) => <label key={setting.name} className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-dim">
        {setting.label}
        <select value={values.get(setting.name) || "auto"} disabled={busy} onChange={(event) => void save(setting.name, event.target.value)}
          className="h-9 min-w-0 max-w-full rounded border border-line bg-bg px-2 text-xs text-ink outline-none focus:border-blue">
          <option value="auto">{setting.follow}</option>
          {EFFORT_OPTIONS.map(([value, label]) => <option key={value} value={value}>{zh ? label : value}</option>)}
        </select>
      </label>)}
      <p className="mt-2 text-xs text-ink-faint">{zh ? "复核用于检查讲解文字，保存后对新发起的讲解生效。" : "The review checks explanation text. Changes apply to newly requested explanations."}</p>
      {error && <p role="alert" className="mt-2 text-xs text-err">{error}</p>}
    </section>
  );
}
