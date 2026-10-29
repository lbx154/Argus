import {
  ExternalLink,
  File,
  LoaderCircle,
} from 'lucide-react';
import type { ReactNode } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import { markdownRemarkPlugins, markdownRehypePlugins } from '../../components/markdownMath';
import { useWorkbenchText } from '../useWorkbenchText';
import { cx, type Tone } from '../utils';

export function Badge({
  children,
  tone = 'neutral',
  dot = false,
  className,
}: {
  children: ReactNode;
  tone?: Tone;
  dot?: boolean;
  className?: string;
}) {
  return (
    <span className={cx('badge', `badge--${tone}`, className)}>
      {dot ? <span className={cx('badge__dot', tone === 'live' && 'is-pulsing')} /> : null}
      {children}
    </span>
  );
}

export function EmptyState({
  icon: Icon = File,
  title,
  description,
  action,
}: {
  icon?: typeof File;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-state__icon"><Icon size={19} /></span>
      <div className="empty-state__title">{title}</div>
      {description ? <p>{description}</p> : null}
      {action ? <div className="empty-state__action">{action}</div> : null}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  const { text } = useWorkbenchText();
  return (
    <span className="spinner" role="status">
      <LoaderCircle size={15} className="spin" /> {label || text('加载中', 'Loading')}
    </span>
  );
}

// Module-level, so a re-render never hands React new component types.
const markdownComponents: Components = {
  a: ({ href, title, children: label }) => (
    <a href={href} title={title} target="_blank" rel="noreferrer">{label}<ExternalLink size={11} /></a>
  ),
};

export function Markdown({ children, className }: { children: string; className?: string }) {
  const { locale } = useWorkbenchText();
  return (
    <div className={cx('markdown', className)}>
      <ReactMarkdown
        remarkPlugins={markdownRemarkPlugins}
        rehypePlugins={markdownRehypePlugins(locale === 'zh-CN')}
        components={markdownComponents}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
