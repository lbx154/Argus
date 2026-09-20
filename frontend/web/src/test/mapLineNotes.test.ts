import { describe, expect, it } from "vitest";
import { buildMap, connectMap, unstatedPairs, type MapTask } from "../map/model";

const tasks: MapTask[] = ["a", "b", "c", "d"].map((id, i) => ({
  id,
  title: id,
  objective: id,
  status: "done",
  deps: id === "b" ? ["a"] : [],
  ts: i,
}));
const shape = (links: ReturnType<typeof connectMap>) => links.map((l) => [l.id, l.source, l.target, l.kind]);

describe("what a line between two tasks says", () => {
  it("asks for notes on the lines that only name their kind", () => {
    const links = connectMap(buildMap(tasks), [], true);
    expect(unstatedPairs(links)).toEqual([
      { source: "a", target: "b" },
      { source: "b", target: "c" },
      { source: "c", target: "d" },
    ]);
  });

  it("writes a note on the existing line and leaves every line where it was", () => {
    const graph = buildMap(tasks);
    const bare = connectMap(graph, [], true);
    const noted = connectMap(graph, [], true, [
      { source: "a", target: "b", label: "基线结果", evidence: "b 读取了 a 的结果文件" },
      { source: "b", target: "c", label: "评测数据", evidence: "c 汇总了 b 的评测" },
      { source: "ghost", target: "d", label: "bad", evidence: "bad" },
      { source: "a", target: "d", label: "不相邻", evidence: "没有这条线" },
    ]);
    expect(shape(noted)).toEqual(shape(bare));
    const line = (source: string, target: string) => noted.find((l) => l.source === source && l.target === target)!;
    expect(line("a", "b")).toMatchObject({ kind: "dependency", label: "基线结果", stated: true });
    expect(line("a", "b").evidence).toContain("执行依赖");
    expect(line("b", "c")).toMatchObject({ kind: "context", label: "评测数据", stated: true });
    expect(line("b", "c").evidence).toContain("不表示执行依赖");
    // A line nobody could say anything about keeps the name of its kind, unstated.
    expect(line("c", "d")).toMatchObject({ kind: "context", label: "同一研究" });
    expect(line("c", "d").stated).toBeFalsy();
    expect(unstatedPairs(noted)).toEqual([{ source: "c", target: "d" }]);
  });

  it("lets a relation the explanation already states win over a note", () => {
    const noted = connectMap(
      buildMap(tasks),
      [{ source: "a", target: "b", label: "方法前置", evidence: "card" }],
      true,
      [{ source: "a", target: "b", label: "基线结果", evidence: "note" }],
    );
    expect(noted.find((l) => l.source === "a" && l.target === "b")).toMatchObject({ label: "方法前置", stated: true });
  });

  it("ignores a note with nothing in it", () => {
    const noted = connectMap(buildMap(tasks), [], true, [{ source: "c", target: "d", label: "", evidence: "" }]);
    expect(noted.find((l) => l.source === "c" && l.target === "d")).toMatchObject({ label: "同一研究" });
  });
});
