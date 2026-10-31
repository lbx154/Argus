import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { useI18n } from '../i18n';
import { errorText } from '../lib/format';
import { ErrorDetails } from './ErrorMessage';

export function HtmlPreview({ html, title, className = '', sid, path, delivery }: {
  html: string; title: string; className?: string; sid?: string | null; path?: string | null; delivery?: string;
}) {
  return sid && path ? <AuthenticatedHtmlPreview sid={sid} path={path} delivery={delivery} html={html} title={title} className={className} /> : <iframe title={title} srcDoc={html} sandbox="allow-scripts" referrerPolicy="no-referrer" className={`min-h-0 w-full flex-1 border-0 bg-white ${className}`} />;
}

function AuthenticatedHtmlPreview({ html, title, className, sid, path, delivery }: { html: string; title: string; className: string; sid: string; path: string; delivery?: string }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const preview = useQuery({
    queryKey: ['artifact-html', sid, path, delivery, html],
    queryFn: ({ signal }) => api.artifactPreview(sid!, path!, signal, delivery),
    enabled: Boolean(sid && path), staleTime: 30_000, retry: false,
  });
  if (sid && path && preview.isPending) return <div className="m-auto p-6 text-sm text-ink-dim" role="status">{zh ? '正在加载网页和配套资源…' : 'Loading the website and its assets…'}</div>;
  if (sid && path && preview.isError) return <div className="m-auto p-6 text-sm text-err" role="alert">
    {errorText(preview.error, locale)}
    <button type="button" className="ml-3 underline" onClick={() => void preview.refetch()}>{zh ? '重试' : 'Retry'}</button>
    <ErrorDetails error={preview.error} />
  </div>;
  const url = preview.data?.url;
  // Safari and the macOS desktop app (WebKit without Chrome) may not resolve *.localhost on macOS 15 and earlier.
  const unresolved = url && new URL(url).hostname.endsWith('.localhost')
    && /Macintosh.*AppleWebKit/.test(navigator.userAgent) && !/Chrom/.test(navigator.userAgent);
  return <div className="flex min-h-0 min-w-0 w-full flex-1 flex-col">
    <div className="shrink-0 px-3 py-2 text-xs text-ink-dim">
      {unresolved && <p>{zh ? 'macOS 15 及更早系统上，此浏览器可能无法显示预览，请改用 Chrome 或 Firefox。'
        : 'On macOS 15 and earlier, this browser may not show the preview; use Chrome or Firefox.'}</p>}
      <a href={url} target="_blank" rel="noopener noreferrer" className="underline">{zh ? '新标签打开' : 'Open in a new tab'}</a>
    </div>
    <iframe title={title} src={url} sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-modals allow-popups" referrerPolicy="no-referrer" className={`min-h-0 w-full flex-1 border-0 bg-white ${className}`} />
  </div>;
}
