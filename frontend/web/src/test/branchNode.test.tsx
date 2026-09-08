import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { NodeProps } from "@xyflow/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  BranchNode,
  type BranchData,
  type BranchFlowNode,
} from "../map/BranchNode";
import type { MapTask } from "../map/model";

vi.mock("@xyflow/react", async (original) => ({
  ...(await original<typeof import("@xyflow/react")>()),
  Handle: () => null,
}));

const branch = (patch: Partial<MapTask> = {}): MapTask => ({
  id: "team:r1",
  title: "Research route 1",
  objective: "Route one findings",
  status: "done",
  deps: [],
  branch: true,
  parent_id: "m",
  team_role: "idea-route",
  ts: 11,
  ...patch,
});

const propsFor = (
  task: MapTask,
  open = vi.fn(),
  extra: Partial<BranchData> = {},
) =>
  ({
    id: task.id,
    data: { task, zh: false, parentCardId: "m-card", open, ...extra },
  }) as unknown as NodeProps<BranchFlowNode>;

let renderer: ReactTestRenderer | undefined;
afterEach(() => {
  act(() => renderer?.unmount());
  renderer = undefined;
});

it("renders a status-coloured pill and opens the owning card on click", () => {
  const open = vi.fn();
  act(() => {
    renderer = create(<BranchNode {...propsFor(branch(), open)} />);
  });
  const pill = renderer!.root.findByProps({ "data-testid": "map-branch" });
  expect(pill.props["data-status"]).toBe("done");
  expect(pill.props.className).toContain("map-state-done");
  expect(
    renderer!.root.findByProps({ className: "map-branch-title" }).children,
  ).toEqual(["Research route 1"]);
  act(() => pill.props.onClick());
  expect(open).toHaveBeenCalledWith("m-card");
});

it("marks a running branch and a needs-input branch truthfully", () => {
  act(() => {
    renderer = create(
      <BranchNode {...propsFor(branch({ status: "running" }))} />,
    );
  });
  expect(
    renderer!.root.findByProps({ "data-testid": "map-branch" }).props[
      "data-status"
    ],
  ).toBe("running");
  act(() => renderer!.unmount());
  act(() => {
    renderer = create(
      <BranchNode
        {...propsFor(branch({ status: "failed", pending_question: "Which?" }))}
      />,
    );
  });
  expect(
    renderer!.root.findByProps({ "data-testid": "map-branch" }).props[
      "data-status"
    ],
  ).toBe("question");
});

it("speaks the cards' status class and glyph language on every pill", () => {
  const cases: Array<[Partial<MapTask>, string, string | undefined]> = [
    [{ status: "done" }, "done", "✓"],
    [{ status: "running" }, "running", "▶"],
    [{ status: "failed" }, "failed", "✕"],
    [{ status: "done", pending_question: "Which?" }, "question", "?"],
    [{ status: "paused" }, "paused", "‖"],
    [{ status: "superseded" }, "superseded", "↪"],
    [{ status: "pending" }, "pending", undefined],
  ];
  for (const [patch, state, glyph] of cases) {
    act(() => {
      renderer = create(<BranchNode {...propsFor(branch(patch))} />);
    });
    const pill = renderer!.root.findByProps({ "data-testid": "map-branch" });
    expect(pill.props["data-status"]).toBe(state);
    expect(pill.props.className).toContain(`map-state-${state}`);
    const marks = renderer!.root.findAllByProps({
      className: "map-branch-state",
    });
    if (glyph) expect(marks.map((mark) => mark.children)).toEqual([[glyph]]);
    else expect(marks).toEqual([]);
    act(() => renderer!.unmount());
    renderer = undefined;
  }
});

it("wears a k/N ordinal badge only when both fan coordinates arrive", () => {
  const open = vi.fn();
  act(() => {
    renderer = create(
      <BranchNode {...propsFor(branch(), open, { fanIndex: 2, fanCount: 5 })} />,
    );
  });
  const badge = renderer!.root.findByProps({ "data-testid": "map-branch-fan" });
  expect(badge.children).toEqual(["2/5"]);
  expect(badge.props.title).toBe("Parallel branch 2 of 5");
  // The badge is decoration: the pill still opens the owning card.
  act(() =>
    renderer!.root.findByProps({ "data-testid": "map-branch" }).props.onClick(),
  );
  expect(open).toHaveBeenCalledWith("m-card");
  for (const extra of [{ fanIndex: 2 }, { fanCount: 5 }, {}]) {
    act(() => renderer!.unmount());
    act(() => {
      renderer = create(<BranchNode {...propsFor(branch(), vi.fn(), extra)} />);
    });
    expect(
      renderer!.root.findAllByProps({ "data-testid": "map-branch-fan" }),
    ).toEqual([]);
  }
});

it("localises the ordinal badge title", () => {
  act(() => {
    renderer = create(
      <BranchNode
        {...propsFor(branch(), vi.fn(), { zh: true, fanIndex: 1, fanCount: 3 })}
      />,
    );
  });
  expect(
    renderer!.root.findByProps({ "data-testid": "map-branch-fan" }).props.title,
  ).toBe("并行分支 1 / 3");
});

it("renders the overflow pill that leads back into the parent card", () => {
  const open = vi.fn();
  act(() => {
    renderer = create(
      <BranchNode
        {...propsFor(
          branch({
            id: "team-overflow:m",
            title: "+9 more",
            team_role: undefined,
            overflow_count: 9,
            status: "recorded",
          }),
          open,
        )}
      />,
    );
  });
  const pill = renderer!.root.findByProps({ "data-testid": "map-branch" });
  expect(pill.props["data-overflow"]).toBe(true);
  expect(
    renderer!.root.findByProps({ className: "map-branch-title" }).children,
  ).toEqual(["+9 more"]);
  act(() => pill.props.onClick());
  expect(open).toHaveBeenCalledWith("m-card");
});
