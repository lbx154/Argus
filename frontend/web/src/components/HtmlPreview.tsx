import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { useI18n } from '../i18n';

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
    {zh ? '网页预览加载失败，请重试或下载文件。' : 'Preview could not load. Retry or download the file.'}
    <button type="button" className="ml-3 underline" onClick={() => void preview.refetch()}>{zh ? '重试' : 'Retry'}</button>
  </div>;
  return <iframe title={title} src={preview.data?.url} sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-modals allow-popups" referrerPolicy="no-referrer" className={`min-h-0 w-full flex-1 border-0 bg-white ${className}`} />;
}
