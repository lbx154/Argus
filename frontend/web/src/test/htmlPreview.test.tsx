import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it, vi } from "vitest";
import type { api } from "../api";
import { HtmlPreview } from "../components/HtmlPreview";

type Preview = Awaited<ReturnType<typeof api.artifactPreview>>;
const query = vi.hoisted(() => ({
  data: undefined as Preview | undefined,
  isPending: false,
  isError: false,
  refetch: vi.fn(),
}));
vi.mock("@tanstack/react-query", () => ({ useQuery: () => query }));
vi.mock("../i18n", () => ({ useI18n: () => ({ locale: "en-US" }) }));
vi.mock("../api", () => ({
  api: {},
}));

beforeEach(() => {
  query.data = { url: "http://localhost:4321/unguessable/index.html" };
  query.isPending = false;
  query.isError = false;
});

const render = () => renderToStaticMarkup(
  <HtmlPreview html="Initial excerpt" title="Website" sid="test" path="index.html" />,
);

it("loads the static URL with its own origin so scripts and storage work", () => {
  const html = render();
  expect(html).toContain('src="http://localhost:4321/unguessable/index.html"');
  expect(html).not.toContain('srcDoc');
  expect(html).toContain('allow-scripts allow-same-origin');
  expect(html).toContain('referrerPolicy="no-referrer"');
});

it("keeps HTML without a project in an opaque sandbox", () => {
  const html = renderToStaticMarkup(<HtmlPreview html="<p>Excerpt</p>" title="Excerpt" />);
  expect(html).toContain('srcDoc="&lt;p&gt;Excerpt&lt;/p&gt;"');
  expect(html).toContain('sandbox="allow-scripts"');
});

it("does not disguise preview request failures as compatibility mode", () => {
  query.isError = true;
  const html = render();
  expect(html).toContain('role="alert"');
  expect(html).not.toContain("<iframe");
});
