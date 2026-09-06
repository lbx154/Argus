import { useEffect, useState } from "react";
import { api, type ConfigSnapshot } from "../api";
import { useI18n } from "../i18n";

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
  const effort = values.get("ARGUS_SKILL_MAP_REASONING_EFFORT") || "auto";
  const [model, setModel] = useState(selectedModel === "auto" ? "" : selectedModel);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => setModel(selectedModel === "auto" ? "" : selectedModel), [selectedModel]);
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
  return (
    <section className="map-model-settings rounded-lg border border-line glass-card p-3" aria-label={zh ? "地图模型" : "Map model"}>
      <div className="text-xs font-semibold text-ink">{zh ? "地图模型" : "Map model"}</div>
      <p className="mt-1 text-xs text-ink-dim">
        {zh ? "沿用科研执行的接入与账号。留空即可跟随科研模型。" : "Uses the research runner and account. Leave the model blank to follow research settings."}
      </p>
      <p className="mt-1 text-xs text-ink-faint">
        {research?.backend_label} · {research?.model || (zh ? "接入默认模型" : "Runner default model")}
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <label className="min-w-0 flex-1 text-xs text-ink-dim">
          {zh ? "摘要模型" : "Summary model"}
          <input value={model} onChange={(event) => setModel(event.target.value)} disabled={busy}
            placeholder={follow} className="mt-1 h-9 w-full rounded border border-line bg-bg px-2 text-xs text-ink outline-none focus:border-blue" />
        </label>
        <button type="button" disabled={busy} onClick={() => void save("ARGUS_SKILL_MAP_MODEL", model.trim() || "auto")}
          className="h-9 rounded border border-line px-3 text-xs text-ink-dim hover:border-blue disabled:opacity-40">
          {zh ? "应用" : "Apply"}
        </button>
        {selectedModel !== "auto" && <button type="button" disabled={busy} onClick={() => void save("ARGUS_SKILL_MAP_MODEL", "auto")}
          className="h-9 rounded border border-line px-3 text-xs text-ink-dim hover:border-blue disabled:opacity-40">{follow}</button>}
      </div>
      <label className="mt-3 flex items-center gap-3 text-xs text-ink-dim">
        {zh ? "思考强度" : "Reasoning effort"}
        <select value={effort} disabled={busy} onChange={(event) => void save("ARGUS_SKILL_MAP_REASONING_EFFORT", event.target.value)}
          className="h-9 rounded border border-line bg-bg px-2 text-xs text-ink outline-none focus:border-blue">
          <option value="auto">{follow}</option>
          {[["low", "低"], ["medium", "中"], ["high", "高"], ["xhigh", "很高"], ["max", "最高"]].map(([value, label]) => (
            <option key={value} value={value}>{zh ? label : value}</option>
          ))}
        </select>
      </label>
      {error && <p role="alert" className="mt-2 text-xs text-err">{error}</p>}
    </section>
  );
}
