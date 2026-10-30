import { Children, isValidElement, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import ReactMarkdown, { defaultUrlTransform, type Components } from 'react-markdown';
import { markdownRemarkPlugins, markdownRehypePlugins } from './markdownMath';
import { CopyButton } from './CopyButton';
import { useI18n } from '../i18n';
import { api, type ArtifactInfo } from '../api';
type ArtifactReference = Pick<ArtifactInfo, 'path'>;

function nodeText(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join('');
  if (isValidElement<{ children?: ReactNode }>(node)) return nodeText(node.props.children);
  return '';
}

function normalizedArtifactReference(value: string): string {
  let raw = String(value || '').trim();
  try {
    raw = decodeURIComponent(raw);
  } catch {
    // Keep the literal path when it contains a malformed percent escape.
  }
  if (/^sandbox:/i.test(raw)) raw = raw.slice('sandbox:'.length);
  if (/^file:/i.test(raw)) {
    try {
      const url = new URL(raw);
      if (url.hostname && url.hostname !== 'localhost') return '';
      raw = url.pathname;
    } catch {
      return '';
    }
  } else if (/^[a-z][a-z0-9+.-]*:/i.test(raw) && !/^[a-z]:[\\/]/i.test(raw)) {
    return '';
  }
  raw = raw.split('#', 1)[0].split('?', 1)[0].replaceAll('\\', '/');
  if (/^\/[a-z]:\//i.test(raw)) raw = raw.slice(1);
  while (raw.startsWith('./')) raw = raw.slice(2);
  return raw.replace(/\/{2,}/g, '/');
}

function sameArtifactReference(left: string, right: string): boolean {
  if (!left || !right) return false;
  if (/^[a-z]:\//i.test(left) || /^[a-z]:\//i.test(right)) {
    return left.toLowerCase() === right.toLowerCase();
  }
  return left === right;
}

function localFileReference(href: string): string {
  if (href.startsWith('#')) return '';
  const path = normalizedArtifactReference(href);
  return /\.(?:md|markdown|txt|pdf|csv|tsv|json|html|png|jpe?g|svg|zip|pptx|xlsx)$/i.test(path) ? path : '';
}

export function artifactPathFromHref(
  href: string | undefined,
  artifacts: ArtifactReference[] = [],
  basePath = '',
): string | null {
  const requested = normalizedArtifactReference(href || '');
  if (!requested) return null;
  let relativeToDocument = '';
  if (basePath && !/^(?:\/|[a-z]:\/)/i.test(requested)) {
    const parts = basePath.replaceAll('\\', '/').split('/').slice(0, -1);
    for (const part of requested.split('/')) {
      if (part === '..') {
        if (!parts.length) return null;
        parts.pop();
      } else if (part && part !== '.') parts.push(part);
    }
    relativeToDocument = parts.join('/');
  }
  const documentMatch = artifacts.find(artifact => sameArtifactReference(
    relativeToDocument, normalizedArtifactReference(artifact.path),
  ));
  if (documentMatch) return documentMatch.path;
  for (const artifact of artifacts) {
    const relative = normalizedArtifactReference(artifact.path);
    if (
      sameArtifactReference(requested, relative)
      || sameArtifactReference(requested.replace(/^\//, ''), relative)
    ) {
      return artifact.path;
    }
  }
  return null;
}

/** A workspace image shown inline; clicking it opens the file viewer when one is wired. */
export function WorkspaceImage({ src, alt, sid, path, onOpen, delivery, className = 'my-2 h-auto max-w-full rounded-lg' }: {
  src?: string; alt?: string; sid?: string | null; path?: string | null; onOpen?: () => void; className?: string; delivery?: string;
}) {
  const image = <MarkdownImage delivery={delivery} src={src} alt={alt} sid={sid} path={path} className={className} />;
  return onOpen ? <button type="button" onClick={onOpen} title={alt || path || undefined}
    className="block max-w-full cursor-zoom-in rounded-lg text-left">{image}</button> : image;
}

function MarkdownImage({ src, alt, sid, path, delivery, className = 'my-2 h-auto max-w-full rounded-lg' }: { src?: string; alt?: string; sid?: string | null; path?: string | null; className?: string; delivery?: string }) {
  const [failed, setFailed] = useState(false);
  const [previewUrl, setPreviewUrl] = useState('');
  useEffect(() => {
    setFailed(false);
    setPreviewUrl('');
    if (!sid || !path) return;
    const controller = new AbortController();
    let objectUrl = '';
    api.artifactBlob(sid, path, false, controller.signal, delivery).then(blob => {
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob);
      setPreviewUrl(objectUrl);
    }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [sid, path, src, delivery]);
  if (failed || !src) {
    return <span className="text-xs text-ink-faint">{alt}</span>;
  }
  if (sid && path && !previewUrl) return <span className="text-xs text-ink-faint">{alt || '…'}</span>;
  return (
    <img
      src={sid && path ? previewUrl : src}
      alt={alt || ''}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
      className={className}
    />
  );
}

export function MarkdownContent({
  children,
  artifacts = [],
  onOpenArtifact,
  basePath = '',
  delivery,
  sid,
}: {
  children: string;
  artifacts?: ArtifactReference[];
  onOpenArtifact?: (path: string) => void;
  basePath?: string;
  sid?: string | null;
  delivery?: string;
}) {
  const { t, locale } = useI18n();
  // The renderers keep their identity across renders: a new function per
  // render is a new component type to React, which remounts every node of the
  // message whenever the page polls (flicker, lost selection, reloaded
  // images). They read the current props from here.
  const live = useRef({ artifacts, onOpenArtifact, basePath, sid, delivery, locale, t });
  live.current = { artifacts, onOpenArtifact, basePath, sid, delivery, locale, t };
  const rehypePlugins = useMemo(() => markdownRehypePlugins(locale === 'zh-CN'), [locale]);
  const components = useMemo((): Components => ({
    h1: ({ children: value }) => <h1 className="mb-2 mt-3 text-base font-semibold text-ink first:mt-0">{value}</h1>,
    h2: ({ children: value }) => <h2 className="mb-1.5 mt-3 text-sm font-semibold text-ink first:mt-0">{value}</h2>,
    h3: ({ children: value }) => <h3 className="mb-1 mt-2 text-sm font-medium text-ink first:mt-0">{value}</h3>,
    p: ({ children: value }) => <p className="my-1.5 whitespace-pre-wrap break-words leading-[1.625] first:mt-0 last:mb-0">{value}</p>,
    ul: ({ children: value }) => <ul className="my-2 list-disc space-y-1 pl-5">{value}</ul>,
    ol: ({ children: value }) => <ol className="my-2 list-decimal space-y-1 pl-5">{value}</ol>,
    li: ({ children: value }) => <li className="pl-0.5">{value}</li>,
    blockquote: ({ children: value }) => <blockquote className="my-2 border-l border-blue/50 pl-3 text-ink-dim">{value}</blockquote>,
    hr: () => <hr className="my-3 border-line/60" />,
    a: ({ href, title, children: value }) => {
      const artifactPath = artifactPathFromHref(href, live.current.artifacts, live.current.basePath);
      if (artifactPath && live.current.onOpenArtifact) {
        return (
          <a
            href={href}
            data-artifact-path={artifactPath}
            title={artifactPath}
            onClick={(event) => {
              event.preventDefault();
              live.current.onOpenArtifact?.(artifactPath);
            }}
            className="cursor-pointer text-blue underline decoration-blue/35 underline-offset-2 hover:decoration-blue"
          >
            {value}
          </a>
        );
      }
      const localPath = localFileReference(href || '');
      if (live.current.onOpenArtifact && localPath) {
        const relativePath = live.current.basePath && !/^(?:\/|[a-z]:\/)/i.test(localPath)
          ? `${live.current.basePath.replaceAll('\\', '/').split('/').slice(0, -1).join('/')}/${localPath}`.replace(/^\//, '')
          : localPath;
        return <button type="button" onClick={() => live.current.onOpenArtifact?.(relativePath)}
          title={live.current.locale === 'zh-CN' ? '查看文件；不存在或不允许访问时会显示原因' : 'Preview file; unavailable files show an error'}
          className="inline cursor-pointer text-left text-blue underline decoration-blue/35 underline-offset-2 hover:decoration-blue">
          {value}
        </button>;
      }
      return (
        <a href={href} title={title} target="_blank" rel="noreferrer" className="text-blue underline decoration-blue/35 underline-offset-2 hover:decoration-blue">
          {value}
        </a>
      );
    },
    code: ({ className, children: value, ...props }) => {
      const block = Boolean(className) || String(value).includes('\n');
      // A result named in a reply, as `results/plot.png`, opens like a link to it.
      const path = block ? null : artifactPathFromHref(String(value), live.current.artifacts, live.current.basePath);
      if (path && live.current.onOpenArtifact) {
        return <button type="button" data-artifact-path={path} onClick={() => live.current.onOpenArtifact?.(path)}
          className="cursor-pointer break-words rounded bg-bg px-1.5 py-0.5 font-mono text-xs text-blue underline decoration-blue/35 underline-offset-2 hover:decoration-blue">
          {value}
        </button>;
      }
      return (
        <code
          {...props}
          className={block
            ? `block min-w-0 whitespace-pre-wrap break-words font-mono text-xs text-ink ${className ?? ''}`
            : 'break-words rounded bg-bg px-1.5 py-0.5 font-mono text-xs text-ink'}
        >
          {value}
        </code>
      );
    },
    pre: ({ children: value }) => {
      const block = (
        <pre className="group/code relative my-2 max-w-full overflow-x-hidden whitespace-pre-wrap break-words rounded-lg border border-line/50 bg-bg px-3 pb-3 pt-10">
          <CopyButton
            text={Children.toArray(value).map(nodeText).join('')}
            label={live.current.t('copy.code')}
            copiedLabel={live.current.t('copy.copied')}
            className="absolute right-2 top-2"
          />
          {value}
        </pre>
      );
      // A ```run block is how to do it yourself: folded, so the reply leads
      // with the offer to run it rather than with a command.
      const first = Children.toArray(value)[0];
      const language = isValidElement<{ className?: string }>(first) ? first.props.className ?? '' : '';
      if (!/\blanguage-run\b/.test(language)) return block;
      return <details className="my-2 text-ink-dim">
        <summary className="cursor-pointer select-none text-xs">{live.current.locale === 'zh-CN' ? '自己运行' : 'Run it yourself'}</summary>
        {block}
      </details>;
    },
    table: ({ children: value }) => <table className="my-2 w-full table-fixed border-collapse text-left text-xs">{value}</table>,
    th: ({ children: value }) => <th className="break-words border border-line/60 bg-bg px-2 py-1.5 font-semibold text-ink">{value}</th>,
    td: ({ children: value }) => <td className="break-words border border-line/60 px-2 py-1.5 align-top">{value}</td>,
    strong: ({ children: value }) => <strong className="font-semibold text-ink">{value}</strong>,
    img: ({ src, alt }) => {
      // A workspace-relative image the artifact list has not caught up with
      // yet is still a workspace file, not a path on this web server.
      const path = artifactPathFromHref(src, live.current.artifacts, live.current.basePath)
        ?? (live.current.sid && src && !/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(src) ? localFileReference(src) || null : null);
      return <WorkspaceImage src={src} alt={alt} delivery={live.current.delivery} sid={live.current.sid} path={path} onOpen={path && live.current.onOpenArtifact ? () => live.current.onOpenArtifact?.(path) : undefined} />;
    },
  }), []);
  // ReactMarkdown is a synchronous parser returning React elements. Keep that
  // result when the content and rendering context are unchanged; the stable
  // renderers above still read the latest callbacks from live.current.
  const artifactPaths = JSON.stringify(artifacts.map(artifact => artifact.path));
  const opensArtifacts = Boolean(onOpenArtifact);
  return useMemo(() => ReactMarkdown({
    children,
    remarkPlugins: markdownRemarkPlugins,
    rehypePlugins,
    urlTransform: (url, key) => key === 'href' && live.current.onOpenArtifact
      && (artifactPathFromHref(url, live.current.artifacts, live.current.basePath) || localFileReference(url)) ? url : defaultUrlTransform(url),
    components,
  }), [children, rehypePlugins, components, artifactPaths, opensArtifacts, basePath, sid, delivery]);
}
