import { memo, type ReactNode } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import { excerptRehypePlugins, markdownRemarkPlugins } from './markdownMath';
import { cleanDeliverySummary } from './deliveryPresentation';

const inline = ({ children }: { children?: ReactNode }) => <span>{children} </span>;

// Module-level, so a re-render never hands React new component types.
const excerptComponents: Components = {
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
};

/** Non-interactive Markdown for clickable cards: formatting without nested controls. */
export const MarkdownExcerpt = memo(function MarkdownExcerpt({ children }: { children: string }) {
  return <ReactMarkdown remarkPlugins={markdownRemarkPlugins} rehypePlugins={excerptRehypePlugins} components={excerptComponents}>{cleanDeliverySummary(children)}</ReactMarkdown>;
});
