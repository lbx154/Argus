import { useEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import type { ArtifactInfo } from '../api';
import type { ReaderBrief } from '../map/presentation';
import { MarkdownContent } from '../components/MarkdownContent';
import { RawDisclosure, Spinner } from '../components/primitives';
import { useI18n } from '../i18n';
import { dateOf } from '../lib/format';
import { DirectionExample, offersDirectionExample } from './DirectionExample';

export function ShortText({ value, expandLabel, clamp = true }: { value: string; expandLabel: string; clamp?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const [overflow, setOverflow] = useState(false);
  const content = useRef<HTMLDivElement>(null);
  const { locale } = useI18n();
  useEffect(() => {
    const element = content.current;
    if (!element || !clamp || expanded) return;
    const measure = () => setOverflow(element.scrollHeight > element.clientHeight + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [value, clamp, expanded]);
  return <div>
    <div ref={content} className={`text-[13px] leading-6 text-ink-dim ${!clamp || expanded ? '' : 'line-clamp-3'}`}><MarkdownContent>{value}</MarkdownContent></div>
    {clamp && (overflow || expanded) ? <button type="button" className="mt-0.5 text-xs text-blue-sky hover:underline" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>
      {expanded ? locale === 'zh-CN' ? '收起' : 'Show less' : expandLabel}<ChevronDown size={11} className={`ml-1 inline ${expanded ? 'rotate-180' : ''}`} />
    </button> : null}
  </div>;
}

/** A retained explanation and its freshness describe the same card. */
export function ReaderExplanationStatus({ generatedAt, pending = false, generating = false, hasExplanation = false }: {
  generatedAt?: number; pending?: boolean; generating?: boolean; hasExplanation?: boolean;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const date = dateOf({ ts: generatedAt });
  const stamp = date?.toLocaleString(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) ?? '';
  if (!(hasExplanation && (stamp || pending)) && !generating) return null;
  return <div className="flex flex-wrap items-center gap-x-2 gap-y-1" data-testid="research-brief-status">
    {hasExplanation && (stamp || pending) ? <span className="text-[11px] text-ink-faint">
      {pending ? zh ? '上次说明 · ' : 'Previous explanation · ' : ''}
      {stamp ? <time dateTime={date?.toISOString()} title={date?.toLocaleString(locale)}>{stamp}</time> : null}
      {pending ? `${stamp ? ' · ' : ''}${zh ? '待更新' : 'update pending'}` : ''}
    </span> : null}
    {generating ? <span role="status" className="inline-flex items-center gap-1.5 text-xs text-ink-faint"><Spinner />{zh ? '正在整理说明' : 'Preparing an explanation'}</span> : null}
  </div>;
}

export function readerExplanationBoundary(zh: boolean) {
  return zh ? '背景教学不计作研究进展；子任务完成不表示整个目标已经解决。'
    : 'Background explanations are not research progress; finishing one task does not establish the overall goal.';
}

/** Pure presentation shared by current research and an explicitly selected historical card. */
export function ReaderExplanation({ brief, identity, readingUnavailable = false, teachingUnavailable = false, artifacts, onOpenArtifact }: {
  brief: ReaderBrief; identity: string; readingUnavailable?: boolean; teachingUnavailable?: boolean;
  artifacts?: ArtifactInfo[]; onOpenArtifact?: (path: string) => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const text = (chinese: string, english: string) => zh ? chinese : english;
  return <div className="mt-3 grid gap-3 sm:grid-cols-2" data-reader-explanation={identity}>
    {readingUnavailable ? <p className="text-xs text-ink-faint sm:col-span-2">{text('阅读说明还需要核对，可以先查看依据或继续问这一步。', 'The reading explanation still needs checking. You can view its evidence or keep asking about this step.')}</p>
      : teachingUnavailable ? <p className="text-xs text-ink-faint sm:col-span-2">{text('这个概念的说明还没核对清楚，可以继续问这一步。', 'The explanation of this concept has not been checked clearly yet. You can keep asking about this step.')}</p> : null}
    {brief.concept ? <section className="min-w-0 sm:col-span-2" data-reader-teaching={identity}>
      <h3 className="mb-3 text-xs font-medium text-ink">{text('认识一个概念', 'One useful concept')} · {brief.concept.name}</h3>
      <div className="grid min-w-0 gap-3" data-reader-teaching-columns={identity}
        style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 16rem), 1fr))' }}>
        <div className="min-w-0">
          <div className="border-l-2 border-line pl-3 text-[13px] leading-6 text-ink-dim" data-testid="concept-example">
            <p className="mb-0.5 text-xs font-medium text-ink">{text('示意例子', 'Illustrative example')}</p>
            <MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{brief.concept.example}</MarkdownContent>
          </div>
          {offersDirectionExample(brief.concept) ? <RawDisclosure label={text('动手看两个方向', 'Try two directions')}><DirectionExample /></RawDisclosure> : null}
        </div>
        <div className="min-w-0" data-reader-definition={identity}>
          <p className="mb-0.5 text-xs font-medium text-ink">{text('概念说明', 'Concept explanation')}</p>
          <ShortText key={`concept:${identity}:${brief.concept.name}`} value={brief.concept.explanation} expandLabel={text('完整定义', 'Full definition')} />
          <RawDisclosure label={text('它与这一步的关系', 'How it connects to this step')}><div className="mt-2 text-[13px] leading-6 text-ink-dim"><MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{brief.concept.connection}</MarkdownContent></div></RawDisclosure>
        </div>
      </div>
    </section> : null}
    <div><h3 className="mb-0.5 text-xs font-medium text-ink">{text('这一步为什么有用', 'Why this step helps')}</h3><ShortText key={`why:${identity}`} value={brief.why} expandLabel={text('完整说明', 'Full explanation')} /></div>
    <div><h3 className="mb-0.5 text-xs font-medium text-ink">{text('结论到哪里为止', 'What this does and does not establish')}</h3><ShortText key={`scope:${identity}`} value={brief.scope} expandLabel={text('完整适用范围', 'Full scope')} clamp={false} /></div>
    <div><h3 className="mb-0.5 text-xs font-medium text-ink">{text('记录中的下一步', 'The recorded next step')}</h3><ShortText key={`next:${identity}`} value={brief.next} expandLabel={text('完整下一步', 'Full next step')} /></div>
  </div>;
}
