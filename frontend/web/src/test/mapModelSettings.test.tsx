import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { ConfigSnapshot } from "../api";
import { MapModelSettings } from "../map/MapModelSettings";

const config: ConfigSnapshot = {
  schema_version: 1, generated_at_utc: "", how_to_change: [],
  roles: [{ role: "engineer", backend: "copilot", backend_label: "Copilot", backend_source: "env", model: "research-model", model_source: "env", reasoning_effort: "medium", reasoning_effort_source: "env", description: "" }],
  operator_knobs: [{ name: "ARGUS_SKILL_MAP_MODEL", value: "summary-model", source: "persisted", default: "auto", doc: "", group: "models" }],
};

it("shows the inherited connection and a separate map model override without credentials", () => {
  const html = renderToStaticMarkup(<MapModelSettings sid="s-settings" config={config} onSaved={async () => {}} />);
  expect(html).toContain("Copilot");
  expect(html).toContain("research-model");
  expect(html).toContain('value="summary-model"');
  expect(html).toContain('value="auto"');
  expect(html).not.toContain('type="password"');
});
