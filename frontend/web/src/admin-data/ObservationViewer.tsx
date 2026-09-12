import { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, ArrowRight, ChevronDown, Code2, Download, FileJson, MessageSquare, Terminal } from 'lucide-react';
import { Button, Chip, RawDisclosure } from '../components/primitives';
import { CopyButton } from '../components/CopyButton';
import { MarkdownContent } from '../components/MarkdownContent';
import { theme } from '../lib/theme';
import { eventView, publicText } from './model';
import type { ObservedEpisode, ObservedEvent, PublicMessage } from './types';
import { dateLabel, eventLabel, roleName, stateLabel, useAdminText } from './copy';

const stringify = (value: unknown) => JSON.stringify(value, null, 2);

function RawJSON({ value }: { value: unknown }) {
  const { text } = useAdminText();
  return <RawDisclosure label={text('原始 JSON', 'Raw JSON')}>
    <div className="admin-data-raw-actions"><CopyButton text={stringify(value)} label={text('复制 JSON', 'Copy JSON')} copiedLabel={text('已复制', 'Copied')} /></div>
    <pre className="admin-data-code">{stringify(value)}</pre>
  </RawDisclosure>;
}

function PublicMessageView({ message, index }: { message: PublicMessage; index: number }) {
  const { locale, text } = useAdminText();
  const [open, setOpen] = useState(false);
  const value = publicText(message);
  return <details className="admin-data-message" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary><span>{String(index + 1).padStart(2, '0')}</span><strong>{roleName(message.role || 'unknown', locale)}</strong><span className="admin-data-subtle">{value.length.toLocaleString(locale)} {text('字符', 'characters')}</span><ChevronDown size={13} /></summary>
    {open ? <div className="admin-data-message-body">
      {value ? <><CopyButton text={value} label={text('复制内容', 'Copy text')} copiedLabel={text('已复制', 'Copied')} /><div className="admin-data-prose"><MarkdownContent>{value}</MarkdownContent></div></> : <p className="admin-data-subtle">{text('这条消息没有公开文本；结构见下方。', 'This message has no public text; its structure is below.')}</p>}
      <RawJSON value={message} />
    </div> : null}
  </details>;
}

function EventBody({ event }: { event: ObservedEvent }) {
  const { locale, text } = useAdminText();
  const view = eventView(event), payload = event.payload;
  const messages = Array.isArray(payload.messages) ? payload.messages : [];
  return <div className="admin-data-event-body">
    {event.kind === 'provider_request' ? <div className="admin-data-inline-meta">
      {typeof payload.model === 'string' ? <Chip>{payload.model}</Chip> : null}
      {typeof payload.reasoning_effort === 'string' ? <Chip>{payload.reasoning_effort}</Chip> : null}
      <span>{messages.length.toLocaleString(locale)} {text('条消息', 'messages')} · {(payload.tools?.length || 0).toLocaleString(locale)} {text('个工具定义', 'tool definitions')}</span>
    </div> : null}
    {messages.length ? <div className="admin-data-message-list">{messages.map((message, index) => <PublicMessageView key={index} message={message} index={index} />)}</div>
      : view.text ? <><div className="admin-data-raw-actions"><CopyButton text={view.text} label={text('复制内容', 'Copy text')} copiedLabel={text('已复制', 'Copied')} /></div>
        {view.category === 'tool' || event.kind === 'message_delta' ? <pre className="admin-data-code">{view.text}</pre> : <div className="admin-data-prose"><MarkdownContent>{view.text}</MarkdownContent></div>}</>
        : <p className="admin-data-subtle">{text('此事件保留了状态和结构，没有公开文本。', 'This event contains status and structure, with no public text.')}</p>}
    {payload.tools?.length ? <RawDisclosure label={text('工具定义', 'Tool definitions')}><pre className="admin-data-code">{stringify(payload.tools)}</pre></RawDisclosure> : null}
    <RawJSON value={event} />
  </div>;
}

interface EventGroup { events: ObservedEvent[]; key: string }

/** Fold only adjacent increments from the same public message; retain every event. */
export function groupPublicIncrements(events: ObservedEvent[]): EventGroup[] {
  const groups: EventGroup[] = [];
  for (const event of events) {
    const key = event.kind === 'message_delta'
      ? JSON.stringify([event.kind, event.payload.type, event.payload.message_index, event.payload.content_index, event.payload.toolCallId])
      : `event:${event.sequence}`;
    const last = groups[groups.length - 1];
    if (event.kind === 'message_delta' && last?.key === key) last.events.push(event);
    else groups.push({ key, events: [event] });
  }
  return groups;
}

function EventRow({ group }: { group: EventGroup }) {
  const { locale, text } = useAdminText();
  const [open, setOpen] = useState(false);
  const event = group.events[0], view = eventView(event);
  const Icon = view.category === 'tool' ? Terminal : view.category === 'input' ? Code2 : MessageSquare;
  const increments = group.events.length > 1;
  const output = increments ? group.events.map(item => eventView(item).text).join('') : view.text;
  const summary = event.kind === 'provider_request'
    ? `${event.payload.messages?.length ?? 0} ${text('条消息', 'messages')} · ${event.payload.tools?.length ?? 0} ${text('个工具', 'tools')}`
    : (event.payload.messages ? publicText(event.payload.messages) : output).replace(/\s+/g, ' ').slice(0, 110);
  return <details className="admin-data-event" data-event-kind={event.kind} onToggle={e => setOpen(e.currentTarget.open)}>
    <summary>
      <span className="admin-data-event-icon"><Icon size={15} /></span>
      <div className="min-w-0 flex-1"><div className="admin-data-event-title"><strong>{eventLabel(event.kind, locale)}{view.toolName ? ` · ${view.toolName}` : ''}</strong>
        {increments ? <Chip>{group.events.length} {text('条增量', 'increments')}</Chip> : null}
        {view.result === 'error' ? <Chip color={theme.error}>{text('工具报告错误', 'Tool reported an error')}</Chip> : null}
        {view.result === 'success' ? <Chip color={theme.success}>{text('工具报告成功', 'Tool reported success')}</Chip> : null}</div>
        {summary ? <p className="admin-data-event-preview">{summary}</p> : null}
      </div>
      <span className="admin-data-event-time">{dateLabel(event.observed_at, locale)}</span><ChevronDown size={14} />
    </summary>
    {open ? increments ? <div className="admin-data-event-body"><CopyButton text={output} label={text('复制输出', 'Copy output')} copiedLabel={text('已复制', 'Copied')} /><pre className="admin-data-code">{output}</pre><RawJSON value={group.events} /></div> : <EventBody event={event} /> : null}
  </details>;
}

export function ObservationViewer({ episode, readonly, onDownload }: {
  episode: ObservedEpisode; readonly: boolean; onDownload: (episode: ObservedEpisode) => void;
}) {
  const { locale, text } = useAdminText();
  const [filter, setFilter] = useState<'all' | 'messages' | 'tools'>('all');
  const [groupPage, setGroupPage] = useState(0);
  const groups = useMemo(() => groupPublicIncrements(episode.events.filter(event => {
    const category = eventView(event).category;
    return filter === 'all' || (filter === 'tools' ? category === 'tool' : category === 'input' || category === 'output');
  })), [episode.events, filter]);
  useEffect(() => setGroupPage(0), [episode.episode_id, filter]);
  const pageCount = Math.max(1, Math.ceil(groups.length / 60));
  const safePage = Math.min(groupPage, pageCount - 1);
  const visibleGroups = groups.slice(safePage * 60, safePage * 60 + 60);
  const loaded = episode.events.length;
  const total = episode.collection.event_count;
  const approved = episode.quality.approved === true && episode.quality.state === 'approved';
  return <section className="admin-data-inspector" aria-label={text('过程详情', 'Process details')}>
    <div className="admin-data-section-heading"><div><h3>{roleName(episode.role, locale)} <span className="admin-data-subtle">· #{episode.episode_id}</span></h3>
      <p>{dateLabel(episode.started_at, locale)} · {episode.task_id ? `${text('任务', 'Task')} ${episode.task_id}` : text('项目级 / 未关联任务', 'Project-level / no task association')}</p></div>
      <Button className="inline-flex items-center justify-center gap-2" disabled={readonly} onClick={() => onDownload(episode)} title={readonly ? text('只读会话不能导出', 'Exports are disabled for read-only sessions') : text('下载当前已加载的原始记录', 'Download the raw records currently loaded')}><Download size={14} /> JSON</Button>
    </div>
    <div className="admin-data-inline-meta"><Chip>{stateLabel(episode.state, locale)}</Chip><Chip color={approved ? theme.success : undefined}>{approved ? text('已验收', 'Reviewed') : text('未验收', 'Not reviewed')}</Chip>
      <span>{text('已加载', 'Loaded')} {loaded.toLocaleString(locale)} / {total.toLocaleString(locale)} {text('条事件', 'events')}</span>
      {episode.runtime.run_label ? <code>{episode.runtime.run_label}</code> : null}</div>
    {!episode.collection.complete || episode.collection.historical_data_unavailable || loaded < total ? <p className="admin-data-notice">
      {loaded < total ? text('当前仅显示已加载的部分事件，后续页可能包含更多记录。', 'Only part of this process is loaded; later pages may contain more records.') : text('这段过程存在缺失或未记录结束；不能据此认定任务已完成。', 'This process has gaps or no recorded ending; it does not establish task completion.')}
    </p> : null}
    {episode.collection.issues?.length ? <RawDisclosure label={text('查看采集问题', 'Capture issues')}><pre className="admin-data-code">{stringify(episode.collection.issues)}</pre></RawDisclosure> : null}
    <div className="admin-data-filter-row" role="group" aria-label={text('事件类型', 'Event type')}>
      {(['all', 'messages', 'tools'] as const).map(value => <button key={value} type="button" aria-pressed={filter === value} className={filter === value ? 'active' : ''} onClick={() => setFilter(value)}>{value === 'all' ? text('全部事件', 'All events') : value === 'messages' ? text('模型输入 / 输出', 'Model input / output') : text('工具轨迹', 'Tool trace')}</button>)}
    </div>
    {groups.length ? <div className="admin-data-timeline">{visibleGroups.map(group => <EventRow key={`${episode.episode_id}:${group.events[0].sequence}`} group={group} />)}</div> : <div className="admin-data-empty"><FileJson size={24} /><p>{text('当前已加载的记录中没有此类事件。', 'No events of this type are present in the loaded records.')}</p></div>}
    {groups.length > 60 ? <div className="admin-data-paging" aria-label={text('当前过程的事件分页', 'Event pages for this process')}><Button className="inline-flex items-center justify-center gap-2" disabled={!safePage} onClick={() => setGroupPage(safePage - 1)} title={text('上一段事件', 'Previous event group')}><ArrowLeft size={14} /></Button><span>{safePage * 60 + 1}–{Math.min(groups.length, safePage * 60 + 60)} / {groups.length.toLocaleString(locale)} {text('组事件', 'event groups')}</span><Button className="inline-flex items-center justify-center gap-2" disabled={safePage + 1 >= pageCount} onClick={() => setGroupPage(safePage + 1)} title={text('下一段事件', 'Next event group')}><ArrowRight size={14} /></Button></div> : null}
    <RawJSON value={{ ...episode, events: undefined, loaded_event_count: loaded }} />
  </section>;
}
