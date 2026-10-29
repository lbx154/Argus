import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { buildMap, connectMap, type MapTask } from "../map/model";

// The survey project's shape: a turn handed to a team task, a check of the
// delivered survey, a replacement of that check, and a later question whose
// reply links the survey file.
const survey: MapTask[] = [
  { id: "turn:web-1", kind: "turn", title: "调研奖励作弊", objective: "", status: "done", deps: [], ts: 1 },
  { id: "survey", title: "综述", objective: "", status: "done", deps: [], ts: 2,
    relations: [{ kind: "carries_out", target: "turn:web-1" }] },
  { id: "check", title: "验收", objective: "", status: "superseded", deps: [], ts: 3, plan_id: "p1",
    superseded_by_plan_id: "p2", relations: [{ kind: "checks", target: "survey" }] },
  { id: "recheck", title: "再验收", objective: "", status: "running", deps: [], ts: 4, plan_id: "p2",
    relations: [{ kind: "replaces", target: "check" }] },
  { id: "turn:web-2", kind: "turn", turn_kind: "qa", title: "为啥我看不懂你在干啥", objective: "", status: "done",
    deps: [], ts: 5, relations: [{ kind: "about", target: "survey" }] },
];

describe("map lines follow recorded relations", () => {
  it("links a question to the card it is about instead of the card before it in time", () => {
    const links = connectMap(buildMap(survey), [], true);
    const question = links.filter((link) => link.target === "turn:web-2");
    expect(question).toEqual([expect.objectContaining({ kind: "relation", relation: "about", source: "survey" })]);
    // Shared chronology does not add a dependency.
    expect(links.filter((link) => link.kind === "context")).toEqual([]);
    // The recorded replacement stands in for the one the plan ids would draw.
    expect(links.filter((link) => link.source === "check" && link.target === "recheck").map((l) => l.kind))
      .toEqual(["relation"]);
  });

  it("leaves a card with no dependency or recorded relation unconnected", () => {
    const loose: MapTask = { id: "later", title: "另一件事", objective: "", status: "done", deps: [], ts: 6 };
    const links = connectMap(buildMap([...survey, loose]), [], true);
    expect(links.filter((link) => link.kind === "context").map((l) => [l.source, l.target]))
      .toEqual([]);
  });
});

describe("the map writes no text on its own", () => {
  const files = (dir: string): string[] => readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? (name === "test" ? [] : files(path)) : /\.tsx?$/.test(name) ? [path] : [];
  });
  it("has no caller of the card-words or line-note routes", () => {
    const source = files(join(__dirname, "..")).map((path) => readFileSync(path, "utf8")).join("\n");
    expect(source).not.toMatch(/\/api\/map-cards|\/api\/map-lines|useMapWords|useMapLines/);
  });
});
