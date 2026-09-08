import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { NodeProps } from "@xyflow/react";
import { afterEach, expect, it, vi } from "vitest";
import { BranchNode, type BranchFlowNode } from "../map/BranchNode";
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

const propsFor = (task: MapTask, open = vi.fn()) =>
  ({
    id: task.id,
    data: { task, zh: false, parentCardId: "m-card", open },
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
