import { Children, isValidElement, useState, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { CopyButton } from './CopyButton';
import { useI18n } from '../i18n';
import type { ArtifactInfo } from '../api';

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

export function artifactPathFromHref(
  href: string | undefined,
  artifacts: ArtifactInfo[] = [],
): string | null {
  const requested = normalizedArtifactReference(href || '');
  if (!requested) return null;
  for (const artifact of artifacts) {
    const relative = normalizedArtifactReference(artifact.path);
    const storage = normalizedArtifactReference(artifact.storage_path || '');
    if (
      sameArtifactReference(requested, relative)
      || sameArtifactReference(requested.replace(/^\//, ''), relative)
      || sameArtifactReference(requested, storage)
    ) {
      return artifact.path;
    }
  }
  return null;
}

function MarkdownImage({ src, alt }: { src?: string; alt?: string }) {
  const [failed, setFailed] = useState(false);
  if (failed || !src) {
    return <span className="text-xs text-ink-faint">Image unavailable{alt ? ` · ${alt}` : ''}</span>;
  }
  return (
    <img
      src={src}
      alt={alt || ''}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
      className="my-2 h-auto max-w-full rounded-lg"
    />
  );
}

export function MarkdownContent({
  children,
  artifacts = [],
  onOpenArtifact,
}: {
  children: string;
  artifacts?: ArtifactInfo[];
  onOpenArtifact?: (path: string) => void;
}) {
  const { t } = useI18n();
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ children: value }) => <h1 className="mb-2 mt-3 text-base font-semibold text-ink first:mt-0">{value}</h1>,
        h2: ({ children: value }) => <h2 className="mb-1.5 mt-3 text-sm font-semibold text-ink first:mt-0">{value}</h2>,
        h3: ({ children: value }) => <h3 className="mb-1 mt-2 text-sm font-medium text-ink first:mt-0">{value}</h3>,
        p: ({ children: value }) => <p className="my-1.5 whitespace-pre-wrap break-words leading-[1.625] first:mt-0 last:mb-0">{value}</p>,
        ul: ({ children: value }) => <ul className="my-2 list-disc space-y-1 pl-5">{value}</ul>,
        ol: ({ children: value }) => <ol className="my-2 list-decimal space-y-1 pl-5">{value}</ol>,
        li: ({ children: value }) => <li className="pl-0.5">{value}</li>,
        blockquote: ({ children: value }) => <blockquote className="my-2 border-l border-blue/50 pl-3 text-ink-dim">{value}</blockquote>,
        hr: () => <hr className="my-3 border-line/60" />,
        a: ({ href, children: value }) => {
          const artifactPath = artifactPathFromHref(href, artifacts);
          const artifact = artifactPath
            ? artifacts.find((item) => item.path === artifactPath)
            : undefined;
          if (artifactPath && onOpenArtifact) {
            return (
              <a
                href={href}
                data-artifact-path={artifactPath}
                title={artifact?.storage_path || artifactPath}
                onClick={(event) => {
                  event.preventDefault();
                  onOpenArtifact(artifactPath);
                }}
                className="cursor-pointer text-blue underline decoration-blue/35 underline-offset-2 hover:decoration-blue"
              >
                {value}
              </a>
            );
          }
          return (
            <a href={href} target="_blank" rel="noreferrer" className="text-blue underline decoration-blue/35 underline-offset-2 hover:decoration-blue">
              {value}
            </a>
          );
        },
        code: ({ className, children: value, ...props }) => {
          const block = Boolean(className) || String(value).includes('\n');
          return (
            <code
              {...props}
              className={block
                ? `block min-w-0 whitespace-pre-wrap break-words font-mono text-xs text-ink ${className ?? ''}`
                : 'break-all rounded bg-bg px-1.5 py-0.5 font-mono text-xs text-ink'}
            >
              {value}
            </code>
          );
        },
        pre: ({ children: value }) => (
          <pre className="group/code relative my-2 max-w-full overflow-x-hidden whitespace-pre-wrap break-words rounded-lg border border-line/50 bg-bg px-3 pb-3 pt-10">
            <CopyButton
              text={Children.toArray(value).map(nodeText).join('')}
              label={t('copy.code')}
              copiedLabel={t('copy.copied')}
              className="absolute right-2 top-2"
            />
            {value}
          </pre>
        ),
        table: ({ children: value }) => <table className="my-2 w-full table-fixed border-collapse text-left text-xs">{value}</table>,
        th: ({ children: value }) => <th className="break-words border border-line/60 bg-bg px-2 py-1.5 font-semibold text-ink">{value}</th>,
        td: ({ children: value }) => <td className="break-words border border-line/60 px-2 py-1.5 align-top">{value}</td>,
        strong: ({ children: value }) => <strong className="font-semibold text-ink">{value}</strong>,
        img: ({ src, alt }) => <MarkdownImage src={src} alt={alt} />,
      }}
    >
      {children}
    </ReactMarkdown>
  );
}
