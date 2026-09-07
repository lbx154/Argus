import { memo, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { cleanDeliverySummary } from './deliveryPresentation';

const inline = ({ children }: { children?: ReactNode }) => <span>{children} </span>;

/** Non-interactive Markdown for clickable cards: formatting without nested controls. */
export const MarkdownExcerpt = memo(function MarkdownExcerpt({ children }: { children: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    p: inline, h1: inline, h2: inline, h3: inline, h4: inline, h5: inline, h6: inline,
    ul: inline, ol: inline, blockquote: inline, pre: inline,
    li: ({ children: text }) => <span className="markdown-excerpt-item">{text} </span>,
    table: inline, thead: inline, tbody: inline, tr: inline,
    th: ({ children: text }) => <strong>{text} · </strong>, td: inline,
    a: ({ children: text }) => <span className="markdown-excerpt-link">{text}</span>,
    img: ({ alt }) => <span>{alt}</span>,
    input: ({ checked }) => <span>{checked ? '✓ ' : '○ '}</span>,
    hr: () => <span> · </span>,
    code: ({ children: text }) => <code>{text}</code>,
  }}>{cleanDeliverySummary(children)}</ReactMarkdown>;
});
