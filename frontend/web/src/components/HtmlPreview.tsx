import { useQuery } from '@tanstack/react-query';
import { api, previewPageUrl } from '../api';
import { useI18n } from '../i18n';

export function HtmlPreview({ html, title, className = '', sid, path }: {
  html: string; title: string; className?: string; sid?: string | null; path?: string | null;
}) {
  return sid && path ? <AuthenticatedHtmlPreview sid={sid} path={path} html={html} title={title} className={className} /> : <iframe title={title} srcDoc={html} sandbox="allow-scripts" referrerPolicy="no-referrer" className={`min-h-0 w-full flex-1 border-0 bg-white ${className}`} />;
}

function AuthenticatedHtmlPreview({ html, title, className, sid, path }: { html: string; title: string; className: string; sid: string; path: string }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const preview = useQuery({
    queryKey: ['artifact-html', sid, path, html],
    queryFn: ({ signal }) => api.artifactPreview(sid!, path!, signal),
    enabled: Boolean(sid && path), staleTime: 30_000, retry: 1,
  });
  if (sid && path && preview.isPending) return <div className="m-auto p-6 text-sm text-ink-dim" role="status">{zh ? '正在加载网页和配套资源…' : 'Loading the website and its assets…'}</div>;
  if (sid && path && preview.isError) return <div className="m-auto p-6 text-sm text-err" role="alert">
    {zh ? '网页预览加载失败，请重试或下载文件。' : 'Preview could not load. Retry or download the file.'}
    <button type="button" className="ml-3 underline" onClick={() => void preview.refetch()}>{zh ? '重试' : 'Retry'}</button>
  </div>;
  // Hosted frontend releases can precede tenant API upgrades.
  const servedPage = preview.data?.served_page === true;
  return <div className={`flex min-h-0 w-full flex-1 flex-col ${className}`}>
    {!!preview.data?.warnings.length && <p className="shrink-0 bg-warn/10 px-3 py-2 text-xs text-warn" role="status">{zh ? '部分配套资源无法加载，页面可能不完整。' : 'Some linked assets are unavailable; the preview may be incomplete.'}</p>}
    {!servedPage && <p className="shrink-0 bg-warn/10 px-3 py-2 text-xs text-warn" role="status">{zh ? '此后端尚未支持独立网页预览；当前为内嵌预览，交互脚本可能不可用。' : 'This backend does not yet support standalone preview; embedded preview may not run interactive scripts.'}</p>}
    <iframe title={title} src={servedPage ? previewPageUrl(sid, path) : undefined} srcDoc={servedPage ? undefined : preview.data?.html ?? html} sandbox="allow-scripts allow-downloads" referrerPolicy="no-referrer" className="min-h-0 w-full flex-1 border-0 bg-white" />
  </div>;
}
