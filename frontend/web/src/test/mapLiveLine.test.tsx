import { act, create } from "react-test-renderer";
import { describe, expect, it } from "vitest";
import { LiveLine } from "../map/LiveLine";

const text = (root: ReturnType<typeof create>) =>
  root.root.findAllByType("span").map((s) => s.children.join("")).join(" ");

describe("the living status line", () => {
  it("names the role at work and how long it has been at it", () => {
    const since = Date.now() / 1000 - 12 * 60 - 3;
    let root!: ReturnType<typeof create>;
    act(() => { root = create(<LiveLine role="engineer" since={since} zh={false} />); });
    expect(text(root)).toBe("Engineer at work · 12 min");
    act(() => root.unmount());
  });
  it("speaks Chinese when the map does, and stays quiet about time it does not know", () => {
    let root!: ReturnType<typeof create>;
    act(() => { root = create(<LiveLine role="reviewer" since={null} zh />); });
    expect(text(root)).toBe("审阅者正在处理");
    act(() => root.unmount());
  });
});
