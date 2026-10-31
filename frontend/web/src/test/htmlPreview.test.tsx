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
vi.mock("../i18n", async importOriginal => {
  const i18n = await importOriginal<typeof import("../i18n")>();
  return { ...i18n, useI18n: () => ({ ...language, t: (key: string) => i18n.translate(key, {}, language.locale as never) }) };
});
vi.mock("../api", () => ({
  api: {},
}));

const SAFARI = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Safari/605.1.15';
beforeEach(() => {
  vi.unstubAllGlobals();
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

for (const locale of ['en-US', 'zh-CN']) it(`warns Safari and the macOS desktop app about local previews and opens the existing URL (${locale})`, () => {
  language.locale = locale;
  for (const userAgent of [SAFARI, 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)']) {
    vi.stubGlobal('navigator', { userAgent });
    const html = render();
    expect(html).toContain(locale === 'zh-CN' ? 'macOS 15 及更早系统上，此浏览器可能无法显示预览' : 'On macOS 15 and earlier, this browser may not show the preview');
    expect(html).toContain(`href="${query.data!.url}"`);
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  }
});

it('keeps the warning away from browsers that resolve local previews', () => {
  for (const userAgent of [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:144.0) Gecko/20100101 Firefox/144.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0',
  ]) {
    vi.stubGlobal('navigator', { userAgent });
    const html = render();
    expect(html).not.toContain('macOS 15');
    expect(html).toContain(`href="${query.data!.url}"`);
  }
});

it('offers a new tab for configured domains without claiming they have local DNS limits', () => {
  vi.stubGlobal('navigator', { userAgent: SAFARI });
  query.data = { url: 'https://js-test-abcdef.preview.example.com/index.html' };
  const html = render();
  expect(html).toContain(`href="${query.data.url}"`);
  expect(html).not.toContain('macOS 15 and earlier');
});

for (const locale of ['en-US', 'zh-CN']) it(`gives a localized actionable port failure, with the address and reason in its details (${locale})`, () => {
  language.locale = locale;
  query.isError = true;
  query.error = new ApiError('Preview server could not listen on [::]:8800: EADDRINUSE', 503, 'GET', '/preview', 'preview_unavailable');
  const html = render();
  expect(html).toContain(locale === 'zh-CN' ? '释放该端口' : 'Free that port');
  expect(html).toContain(locale === 'zh-CN' ? '重试' : 'Retry');
  // The raw text breaks anywhere, so a long request path stays inside a phone-width panel.
  expect(html).toMatch(/<details[^>]*><summary[^>]*>[^<]+<\/summary><pre class="[^"]*\[overflow-wrap:anywhere\][^"]*">Preview server could not listen on \[::\]:8800: EADDRINUSE<\/pre>/);
});
