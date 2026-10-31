import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "../../../core/src/http";
import type { api } from "../api";
import { HtmlPreview } from "../components/HtmlPreview";

type Preview = Awaited<ReturnType<typeof api.artifactPreview>>;
const language = vi.hoisted(() => ({ locale: "en-US" }));
const query = vi.hoisted(() => ({
  data: undefined as Preview | undefined,
  isPending: false,
  isError: false,
  error: undefined as ApiError | undefined,
  refetch: vi.fn(),
}));
vi.mock("@tanstack/react-query", () => ({ useQuery: () => query }));
vi.mock("../i18n", async importOriginal => ({
  ...await importOriginal<typeof import("../i18n")>(), useI18n: () => language,
}));
vi.mock("../api", () => ({
  api: {},
}));

beforeEach(() => {
  language.locale = "en-US";
  query.data = { url: "http://abcdef0123456789abcdef0123456789.localhost:4321/index.html" };
  query.isPending = false;
  query.isError = false;
  query.error = undefined;
});

const render = () => renderToStaticMarkup(
  <HtmlPreview html="Initial excerpt" title="Website" sid="test" path="index.html" />,
);

it("loads the static URL with its own origin so scripts and storage work", () => {
  const html = render();
  expect(html).toContain('src="http://abcdef0123456789abcdef0123456789.localhost:4321/index.html"');
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

for (const locale of ['en-US', 'zh-CN']) it(`explains local DNS and cross-site cookie limits and opens the existing URL (${locale})`, () => {
  language.locale = locale;
  const html = render();
  expect(html).toContain(locale === 'zh-CN' ? 'macOS 15 及更早' : 'macOS 15 and earlier');
  expect(html).toContain('Safari/WKWebView');
  expect(html).toContain(locale === 'zh-CN' ? '跨站' : 'cross-site');
  expect(html).toContain('cookie');
  expect(html).toContain(`href="${query.data!.url}"`);
  expect(html).toContain('target="_blank"');
  expect(html).toContain('rel="noopener noreferrer"');
});

it('offers a new tab for configured domains without claiming they have local DNS limits', () => {
  query.data = { url: 'https://js-test-abcdef.preview.example.com/index.html' };
  const html = render();
  expect(html).toContain(`href="${query.data.url}"`);
  expect(html).not.toContain('macOS 15 and earlier');
});

for (const locale of ['en-US', 'zh-CN']) it(`gives a localized actionable port failure (${locale})`, () => {
  language.locale = locale;
  query.isError = true;
  query.error = new ApiError('listen EADDRINUSE', 503, 'GET', '/preview', 'preview_unavailable');
  const html = render();
  expect(html).toContain(locale === 'zh-CN' ? '相邻端口' : 'adjacent port');
  expect(html).toContain(locale === 'zh-CN' ? '释放该端口' : 'Free that port');
  expect(html).not.toContain('EADDRINUSE');
  expect(html).toContain(locale === 'zh-CN' ? '重试' : 'Retry');
});
