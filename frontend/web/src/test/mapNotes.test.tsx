import { renderToStaticMarkup } from "react-dom/server";
import type { NodeProps } from "@xyflow/react";
import { describe, expect, it, vi } from "vitest";
import { MacroTaskNode, MapNotesContext, type MacroData, type MacroNode } from "../map/MacroTaskNode";
import { groupNotesByNode, type MapNote } from "../map/notes";
import { layoutSubmap, type SubmapStep } from "../map/submap";
import type { MapTask } from "../map/model";

vi.mock("@xyflow/react", async (original) => ({
  ...(await original<typeof import("@xyflow/react")>()),
  Handle: () => null,
  useStore: (select: (state: { transform: number[] }) => unknown) =>
    select({ transform: [0, 0, 1] }),
}));

const notes: MapNote[] = [
  { id: "n2", node_id: "parent", text: "后一条批注", ts: 20 },
  { id: "n1", node_id: "parent", text: "先看这条曲线是否复现", ts: 10 },
  { id: "n3", node_id: "other", text: "别的卡", ts: 5 },
];

describe("groupNotesByNode", () => {
  it("groups by node and orders oldest first within a node", () => {
    const grouped = groupNotesByNode(notes);
    expect(Object.keys(grouped).sort()).toEqual(["other", "parent"]);
    expect(grouped.parent.map((n) => n.id)).toEqual(["n1", "n2"]);
  });
});

const task: MapTask = {
  id: "parent",
  title: "Research portfolio",
  objective: "",
  status: "running",
  deps: [],
};
const step = (id: string): SubmapStep => ({
  id,
  kind: "execution",
  title: id,
  detail: `Work for ${id}`,
  status: "done",
  source: "event",
  eventIds: [id],
});
const propsFor = (detailed: boolean): NodeProps<MacroNode> =>
  ({
    id: "parent-card",
    data: {
      id: "parent-card",
      task,
      ordinal: 1,
      part: 1,
      partCount: 1,
      start: 1,
      end: 1,
      totalSteps: 1,
      layout: layoutSubmap(task, [], false, [step("s1")]),
      frame: { width: 900, height: 650, scale: 1 },
      canvasSize: { width: 1440, height: 960 },
      zh: true,
      focused: detailed,
      detailed,
      live: true,
      source: "live:project",
      readOnly: false,
      open: vi.fn(),
      quote: vi.fn(),
      menu: vi.fn(),
      readStep: vi.fn(),
    } as unknown as MacroData,
  }) as unknown as NodeProps<MacroNode>;

const renderWithNotes = (detailed: boolean) =>
  renderToStaticMarkup(
    <MapNotesContext.Provider value={{ notes: groupNotesByNode(notes) }}>
      <MacroTaskNode {...propsFor(detailed)} />
    </MapNotesContext.Provider>,
  );

describe("MacroTaskNode notes", () => {
  it("shows a note badge with the count on the overview card", () => {
    const html = renderWithNotes(false);
    expect(html).toContain("macro-note-badge");
    expect(html).toContain(">2<");
  });

  it("lists the note text inside the detailed card", () => {
    const html = renderWithNotes(true);
    expect(html).toContain("先看这条曲线是否复现");
    expect(html).toContain("macro-notes");
  });

  it("renders no badge when the card has no notes", () => {
    const html = renderToStaticMarkup(
      <MapNotesContext.Provider value={{ notes: {} }}>
        <MacroTaskNode {...propsFor(false)} />
      </MapNotesContext.Provider>,
    );
    expect(html).not.toContain("macro-note-badge");
  });
});
