import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import App from "../App";

const state = vi.hoisted(() => ({ view: "map", open: true }));

vi.mock("../useWorkbenchLayout", () => ({
  useWorkbenchLayout: () => ({
    workspaceView: state.view,
    mobileView: "activity",
    themeMode: "light",
    themeStyle: "glass",
    leftWidth: 256,
    rightWidth: 440,
    shellRef: { current: null },
  }),
}));
vi.mock("../useProjectSelection", () => ({
  useProjectSelection: () => ({ activeSid: null, sidRef: { current: null } }),
}));
vi.mock("../hooks", async (original) => {
  const hooks = await original<typeof import("../hooks")>();
  const emptyQuery = () => ({ data: undefined });
  return {
    ...hooks,
    useProjects: emptyQuery,
    useProjectCosts: emptyQuery,
    useSnapshot: emptyQuery,
    useArtifacts: emptyQuery,
    useGitDiff: emptyQuery,
    useTranscript: emptyQuery,
    useJournal: emptyQuery,
    useEventStream: () => ({ events: [], connected: true }),
  };
});
vi.mock("../usePendingReplySession", () => ({
  usePendingReplySession: () => ({
    pendingReplyOpen: state.open,
    pendingReplyBusy: false,
    pendingReply: {
      id: "decision-task-1",
      item_id: "task-1",
      title: "Choose an experiment",
      question: "Continue with the local baseline?",
      reason: "An operator decision is required to continue.",
      evidence: [],
      options: [{ id: "local", label: "Run the local baseline", description: "", requires_note: false }],
    },
  }),
}));

afterEach(() => { state.view = "map"; state.open = true; });

function markup() {
  const client = new QueryClient();
  try {
    return renderToStaticMarkup(<QueryClientProvider client={client}><App /></QueryClientProvider>);
  } finally { client.clear(); }
}

it.each(["map", "activity", "mission", "workbench"])(
  "keeps required operator decisions actionable in the %s workspace",
  (view) => {
    state.view = view;
    const html = markup();
    expect(html).toContain('role="dialog"');
    expect(html).toContain("Continue with the local baseline?");
    expect(html).toContain("Run the local baseline");
  },
);

it("keeps a dismissed decision closed in the map workspace", () => {
  state.open = false;
  expect(markup()).not.toContain("Continue with the local baseline?");
});
