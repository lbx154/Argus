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
  previewPageUrl: () => "/api/projects/test/artifact/preview/page?path=index.html",
}));

beforeEach(() => {
  query.data = { html: "<h1>Website</h1>", warnings: [], file_count: 1 };
  query.isPending = false;
  query.isError = false;
});

const render = () => renderToStaticMarkup(
  <HtmlPreview html="Initial excerpt" title="Website" sid="test" path="index.html" />,
);

it("preserves embedded previews on deployed APIs without the new page endpoint", () => {
  const html = render();
  expect(html).toContain('srcDoc="&lt;h1&gt;Website&lt;/h1&gt;"');
  expect(html).not.toContain('src="/api/');
  expect(html).toContain("does not yet support standalone preview");
  expect(html).toContain('sandbox="allow-scripts allow-downloads"');
});

it("uses the independently sandboxed page only when the backend supports it", () => {
  query.data!.served_page = true;
  const html = render();
  expect(html).toContain('src="/api/projects/test/artifact/preview/page?path=index.html"');
  expect(html).not.toContain("srcDoc");
  expect(html).not.toContain("does not yet support");
});

it("does not disguise preview request failures as compatibility mode", () => {
  query.isError = true;
  const html = render();
  expect(html).toContain('role="alert"');
  expect(html).not.toContain("<iframe");
});
