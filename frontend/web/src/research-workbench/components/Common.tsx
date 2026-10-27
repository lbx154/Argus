import {
  ExternalLink,
  File,
  LoaderCircle,
  Radio,
} from 'lucide-react';
import type { ReactNode } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import { markdownRemarkPlugins, markdownRehypePlugins } from '../../components/markdownMath';
import { agentRoleColor } from '../../lib/agentRoles';
import { plainDetail } from '../../lib/plainStatus';
import { roleLabel } from '../enumLabels';
import type { EventMsg } from '../types';
import { useWorkbenchText } from '../useWorkbenchText';
import {
  cx,
  eventDetail,
  eventRole,
  eventTitle,
  formatClock,
  statusTone,
  type Tone,
} from '../utils';

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

export function EventTimeline({
  events,
  limit = 24,
  empty,
  dense = false,
}: {
  events: EventMsg[];
  limit?: number;
  empty?: string;
  dense?: boolean;
}) {
  const { locale, text } = useWorkbenchText();
  const rows = events.slice(-limit).reverse();
  if (!rows.length) return <EmptyState icon={Radio} title={empty || text('还没有可展示的实时动态', 'No activity to show yet')} />;
  return (
    <div className={cx('event-list', dense && 'event-list--dense')}>
      {rows.map((event, index) => {
        const role = eventRole(event);
        const title = eventTitle(event, locale);
        const detail = plainDetail(eventDetail(event, dense ? 180 : 480), locale).text;
        const tone = statusTone(String(event.status ?? event.kind ?? event.type ?? ''));
        return (
          <article className="event-row" key={`${event.type}-${event.ts}-${event.message_id ?? index}`}>
            <div className={cx('event-row__marker', `event-row__marker--${tone}`)} />
            <div className="event-row__content">
              <div className="event-row__meta">
                <span className="role-label" style={{ color: agentRoleColor(role) }}>{roleLabel(role, text)}</span>
                <time>{formatClock(event.ts, locale)}</time>
              </div>
              <div className="event-row__title">{title}</div>
              {detail ? <div className="event-row__detail">{detail}</div> : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}
