import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

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

export function MarkdownContent({ children }: { children: string }) {
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
        a: ({ href, children: value }) => (
          <a href={href} target="_blank" rel="noreferrer" className="text-blue underline decoration-blue/35 underline-offset-2 hover:decoration-blue">
            {value}
          </a>
        ),
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
        pre: ({ children: value }) => <pre className="my-2 max-w-full overflow-x-hidden whitespace-pre-wrap break-words rounded-lg border border-line/50 bg-bg p-3">{value}</pre>,
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
