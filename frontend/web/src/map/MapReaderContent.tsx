import type { ArtifactInfo } from '../api';
import { MarkdownContent } from '../components/MarkdownContent';
import { RawDisclosure } from '../components/primitives';
import { useI18n } from '../i18n';
import { plainEventName } from '../lib/plainStatus';
import { cleanDeliverySummary } from '../components/deliveryPresentation';
import { isReaderBrief, READER_BRIEF_VERSION } from '../research-brief/model';
import { ReaderExplanation, ReaderExplanationStatus, readerExplanationBoundary } from '../research-brief/ReaderExplanation';
import type { MapEvent } from './model';
import type { CardCopy, CardRequest } from './presentation';

export interface MapReaderSelection {
  request: CardRequest;
  evidence: MapEvent[];
  pending: boolean;
  generating: boolean;
}

/** Only this card's copy and event range enter the reader; no task-root fallback. */
export function MapReaderContent({ cardKey, taskId, card, originalDetail, selection, artifacts, onOpenArtifact }: {
  cardKey: string; taskId: string; card?: CardCopy; originalDetail: string;
  selection?: MapReaderSelection; artifacts?: ArtifactInfo[]; onOpenArtifact?: (path: string) => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const selected = selection?.request.key === cardKey && selection.request.task_id === taskId ? selection : undefined;
  const brief = (card?.version ?? 0) >= READER_BRIEF_VERSION && isReaderBrief(card?.reader_brief) ? card!.reader_brief : undefined;
  const scopeIds = card?.event_ids ?? selected?.request.event_ids ?? [];
  const evidence = (selected?.evidence ?? []).filter(event => event.item_id === taskId && scopeIds.includes(event.id));
  const timestamps = evidence.map(event => event.ts).filter(ts => Number.isFinite(ts) && ts > 0);
  const dates = timestamps.length ? [Math.min(...timestamps), Math.max(...timestamps)] : [];
  const root = cardKey === taskId;
  return <div className="font-sans" data-reader-card={cardKey} data-reader-task-id={taskId}>
    <p className="text-xs text-ink-faint">{root
      ? zh ? '这项任务的说明；依据下方列出的记录。' : 'An explanation of this task, based on the records listed below.'
      : zh ? '本环节的说明；依据范围仅限这张卡的记录。' : 'An explanation of this step, limited to this card’s recorded evidence.'}</p>
    <ReaderExplanationStatus generatedAt={card?.generated_at} pending={selected?.pending} generating={selected?.generating} hasExplanation={!!card} />
    <p className="text-xs text-ink-faint">{zh ? `${card ? '这份说明依据' : '本次阅读选中'} ${scopeIds.length} 条记录，已加载 ${evidence.length} 条。` : `${card ? 'This explanation references' : 'This reading selects'} ${scopeIds.length} records; ${evidence.length} are loaded.`}</p>
    {dates.length ? <p className="text-xs text-ink-faint">{zh ? '记录时间：' : 'Record dates: '}{dates.map((ts, i) => <span key={`${i}:${ts}`}>{i ? ' – ' : ''}<time dateTime={new Date(ts * 1000).toISOString()}>{new Date(ts * 1000).toLocaleString(locale)}</time></span>)}</p> : null}
    {brief ? <ReaderExplanation brief={brief} identity={cardKey}
      readingUnavailable={card?.teaching_review?.reading_review?.status === 'unavailable'}
      teachingUnavailable={card?.teaching_review?.status === 'unavailable'} artifacts={artifacts} onOpenArtifact={onOpenArtifact} />
      : <>
        <p className="my-2 text-xs text-ink-faint">{zh ? '阅读说明待整理；可以先读已保留的详细记录。' : 'A reading explanation is pending. The retained detailed record is available below.'}</p>
        <div className="macro-reader-markdown"><MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{cleanDeliverySummary(card?.detail || originalDetail)}</MarkdownContent></div>
      </>}
    {brief && card?.detail ? <RawDisclosure label={zh ? '原有详细说明' : 'Retained detailed explanation'}>
      <div className="macro-reader-markdown"><MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{card.detail}</MarkdownContent></div>
    </RawDisclosure> : null}
    <RawDisclosure label={zh ? '原始任务与环节记录' : 'Original task and step record'}>
      <div className="macro-reader-markdown"><MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{originalDetail}</MarkdownContent></div>
    </RawDisclosure>
    <RawDisclosure label={zh ? '查看依据' : 'View evidence'}>
      {evidence.map(event => <section key={event.id} data-event-id={event.id} className="mt-3 border-t border-line/50 pt-2">
        <p className="text-xs font-medium">{plainEventName(event.type, locale) || event.type}</p>
        {Number.isFinite(event.ts) && event.ts > 0 ? <time className="text-xs text-ink-faint" dateTime={new Date(event.ts * 1000).toISOString()}>{new Date(event.ts * 1000).toLocaleString(locale)}</time> : null}
        {event.text || event.reason ? <div className="macro-reader-markdown"><MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{event.text || event.reason || ''}</MarkdownContent></div> : null}
        <RawDisclosure label={zh ? '原始记录（JSON）' : 'Original record (JSON)'}><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(event, null, 2)}</pre></RawDisclosure>
      </section>)}
      {!evidence.length ? <p className="text-xs text-ink-faint">{zh ? '此范围尚无已加载的事件原文。' : 'No source event text is loaded for this range yet.'}</p> : null}
    </RawDisclosure>
    <p className="mt-3 border-t border-line/50 pt-2 text-[11px] text-ink-faint">{readerExplanationBoundary(zh)}</p>
  </div>;
}
