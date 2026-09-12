import { renderToStaticMarkup } from "react-dom/server";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, expect, it, vi } from "vitest";
import { api, type ConfigSnapshot } from "../api";
import { MapModelSettings } from "../map/MapModelSettings";

const language = vi.hoisted(() => ({ locale: "en" }));
vi.mock("../i18n", () => ({ useI18n: () => language }));

const GENERATION_EFFORT = "ARGUS_SKILL_MAP_REASONING_EFFORT";
const REVIEW_EFFORT = "ARGUS_SKILL_MAP_REVIEW_REASONING_EFFORT";
const config: ConfigSnapshot = {
  schema_version: 1, generated_at_utc: "", how_to_change: [],
  roles: [{ role: "engineer", backend: "copilot", backend_label: "Copilot", backend_source: "env", model: "research-model", model_source: "env", reasoning_effort: "medium", reasoning_effort_source: "env", description: "" }],
  operator_knobs: [{ name: "ARGUS_SKILL_MAP_MODEL", value: "summary-model", source: "persisted", default: "auto", doc: "", group: "models" }],
};
let renderer: ReactTestRenderer | undefined;

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = undefined;
  language.locale = "en";
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function withEfforts(review?: string): ConfigSnapshot {
  const efforts = [[GENERATION_EFFORT, "medium"], ...(review ? [[REVIEW_EFFORT, review]] : [])];
  return { ...config, operator_knobs: [...config.operator_knobs,
    ...efforts.map(([name, value]) => ({ name, value, source: "persisted", default: "auto", doc: "", group: "models" })),
  ] };
}

function select(label: string) {
  return renderer!.root.findAllByType("label").find(node => node.children.includes(label))!.findByType("select");
}

it("shows the inherited connection and a separate map model override without credentials", () => {
  const html = renderToStaticMarkup(<MapModelSettings sid="s-settings" config={config} onSaved={async () => {}} />);
  expect(html).toContain("Copilot");
  expect(html).toContain("research-model");
  expect(html).toContain('value="summary-model"');
  expect(html).toContain('value="auto"');
  expect(html).not.toContain('type="password"');
});

it.each([
  ["en", "Explanation effort", "Explanation review effort", "Follow research settings", "Follow explanation effort"],
  ["zh-CN", "讲解生成强度", "讲解复核强度", "跟随科研设置", "跟随讲解生成强度"],
])("explains the review auto setting in %s as following explanation effort", (locale, generation, review, followResearch, followGeneration) => {
  language.locale = locale;
  act(() => { renderer = create(<MapModelSettings sid="s-settings" config={withEfforts()} onSaved={async () => {}} />); });
  expect(select(generation).props.value).toBe("medium");
  expect(select(review).props.value).toBe("auto");
  expect(select(generation).findAllByType("option").find(option => option.props.value === "auto")!.children).toEqual([followResearch]);
  expect(select(review).findAllByType("option").find(option => option.props.value === "auto")!.children).toEqual([followGeneration]);
  expect(select(review).findAllByType("option").map(option => option.props.value)).toEqual(["auto", "low", "medium", "high", "xhigh", "max"]);
});

it("shows the review effort returned by trial configuration independently of generation effort", () => {
  act(() => { renderer = create(<MapModelSettings sid="s-settings" config={{ ...withEfforts("high"), trial_mode: true }} onSaved={async () => {}} />); });
  expect(select("Explanation effort").props.value).toBe("medium");
  expect(select("Explanation review effort").props.value).toBe("high");
});

it("saves review overrides and auto through the existing config API and displays the refreshed value immediately", async () => {
  let stored = withEfforts("auto");
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/projects/s-settings/config/set" && init?.method === "POST") {
      const update = JSON.parse(String(init.body)) as { name: string; value: string };
      stored = { ...stored, operator_knobs: stored.operator_knobs.map(knob => knob.name === update.name ? { ...knob, value: update.value } : knob) };
      return Response.json({});
    }
    if (path === "/api/projects/s-settings/config" && !init?.method) return Response.json(stored);
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  let finishRefresh: () => void = () => {};
  const onSaved = vi.fn(async () => {
    const refreshed = await api.config("s-settings");
    renderer!.update(<MapModelSettings sid="s-settings" config={refreshed} onSaved={onSaved} />);
    finishRefresh();
  });
  const initial = await api.config("s-settings");
  act(() => { renderer = create(<MapModelSettings sid="s-settings" config={initial} onSaved={onSaved} />); });

  for (const value of ["high", "auto"]) {
    const refreshed = new Promise<void>(resolve => { finishRefresh = resolve; });
    await act(async () => {
      select("Explanation review effort").props.onChange({ target: { value } });
      await refreshed;
    });
    expect(select("Explanation review effort").props.value).toBe(value);
    expect(select("Explanation review effort").props.disabled).toBe(false);
    expect(select("Explanation effort").props.value).toBe("medium");
    expect(renderer!.root.findByType("input").props.value).toBe("summary-model");
  }

  const writes = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(writes.map(([path, init]) => [path, JSON.parse(String(init?.body))])).toEqual([
    ["/api/projects/s-settings/config/set", { name: REVIEW_EFFORT, value: "high" }],
    ["/api/projects/s-settings/config/set", { name: REVIEW_EFFORT, value: "auto" }],
  ]);
  expect(fetchMock).toHaveBeenCalledTimes(5);
  expect(onSaved).toHaveBeenCalledTimes(2);
  expect(stored.roles).toEqual(config.roles);
});

it("keeps the saved review setting and reports a failed update without refreshing", async () => {
  const save = vi.spyOn(api, "setConfig").mockRejectedValue(new Error("Could not save review effort"));
  const onSaved = vi.fn(async () => {});
  act(() => { renderer = create(<MapModelSettings sid="s-settings" config={withEfforts("high")} onSaved={onSaved} />); });
  await act(async () => { select("Explanation review effort").props.onChange({ target: { value: "max" } }); });
  expect(save).toHaveBeenCalledWith("s-settings", REVIEW_EFFORT, "max");
  expect(onSaved).not.toHaveBeenCalled();
  expect(select("Explanation review effort").props.value).toBe("high");
  expect(select("Explanation review effort").props.disabled).toBe(false);
  expect(renderer!.root.findByProps({ role: "alert" }).children).toEqual(["Could not save review effort"]);
});
