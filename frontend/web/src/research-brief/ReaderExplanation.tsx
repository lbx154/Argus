import { useEffect, useRef, useState } from 'react';
import { ChevronDown, RefreshCw } from 'lucide-react';
import type { ArtifactInfo } from '../api';
import type { CardCopy, ReaderBrief, ReaderLearningPath } from '../map/presentation';
import { MarkdownContent } from '../components/MarkdownContent';
import { Button, RawDisclosure, Spinner } from '../components/primitives';
import { useI18n } from '../i18n';
import { dateOf } from '../lib/format';
import { DirectionExample, offersDirectionExample } from './DirectionExample';
import { LearningPath } from './LearningPath';
import { explanationProgressLabel } from './progress';

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
export function ReaderExplanationStatus({ generatedAt, pending = false, generating = false, phase, hasExplanation = false, error, unavailable = false, retry, retryDisabled = false }: {
  generatedAt?: number; pending?: boolean; generating?: boolean; hasExplanation?: boolean;
  phase?: import('../api').ExplanationPhase;
  error?: unknown; unavailable?: boolean; retry?: () => Promise<unknown>; retryDisabled?: boolean;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const date = dateOf({ ts: generatedAt });
  const stamp = date?.toLocaleString(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) ?? '';
  const hasProblem = !!error || unavailable;
  if (!(hasExplanation && (stamp || pending)) && !generating && !hasProblem) return null;
  return <div className="flex flex-wrap items-center gap-x-2 gap-y-1" data-testid="research-brief-status">
    {hasExplanation && (stamp || pending) ? <span className="text-[11px] text-ink-faint">
      {pending ? `${zh ? '上次说明' : 'Previous explanation'}${stamp || !hasProblem ? ' · ' : ''}` : ''}
      {stamp ? <time dateTime={date?.toISOString()} title={date?.toLocaleString(locale)}>{stamp}</time> : null}
      {pending && !hasProblem ? `${stamp ? ' · ' : ''}${zh ? '待更新' : 'update pending'}` : ''}
    </span> : null}
    {hasProblem ? <>
      <span role="status" className="text-xs text-ink-faint">{error
        ? zh ? '说明生成未完成；不会自动重复请求。' : 'The explanation could not be prepared. This request will not be repeated automatically.'
        : zh ? '说明暂未更新；可以手动重试。' : 'The explanation has not been updated yet. You can retry manually.'}</span>
      {retry ? <Button className="inline-flex items-center gap-1 text-xs" disabled={generating || retryDisabled} onClick={() => void retry()}><RefreshCw size={12} />{zh ? '重试' : 'Retry'}</Button> : null}
    </> : generating ? <span role="status" data-explanation-phase={phase ?? 'pending'} className="inline-flex items-center gap-1.5 text-xs text-ink-faint"><Spinner />{explanationProgressLabel(phase, zh)}</span> : null}
  </div>;
}

/** Pure presentation shared by current research and an explicitly selected historical card. */
export function ReaderExplanation({ brief, identity, detail, learningPath, foundation, readingUnavailable = false, teachingUnavailable = false, artifacts, onOpenArtifact }: {
  brief: ReaderBrief; identity: string; detail?: string; readingUnavailable?: boolean; teachingUnavailable?: boolean;
  learningPath?: ReaderLearningPath | null;
  foundation?: CardCopy['foundation_ref'];
  artifacts?: ArtifactInfo[]; onOpenArtifact?: (path: string) => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const text = (chinese: string, english: string) => zh ? chinese : english;
  return <div className="mt-3 grid min-w-0 gap-4" data-reader-explanation={identity}>
    {foundation ? <div className="rounded border border-line/60 p-2 text-xs text-ink-dim" data-foundation-id={foundation.id}>
      <p>{text('这次进展参考的基础说明：', 'Foundations used to explain this progress: ')}{foundation.question}</p>
      {onOpenArtifact ? <Button className="mt-1 text-xs" onClick={() => onOpenArtifact(foundation.path)}>{text('阅读这份基础说明', 'Read these foundations')}</Button> : null}
    </div> : null}
    {readingUnavailable ? <p className="text-xs text-ink-faint">{text('阅读说明还需要核对，可以先查看依据或继续问这一步。', 'The reading explanation still needs checking. You can view its evidence or keep asking about this step.')}</p>
      : teachingUnavailable ? <p className="text-xs text-ink-faint">{text('这个概念的说明还没核对清楚，可以继续问这一步。', 'The explanation of this concept has not been checked clearly yet. You can keep asking about this step.')}</p> : null}
    {learningPath ? <LearningPath key={identity} path={learningPath} identity={identity} artifacts={artifacts} onOpenArtifact={onOpenArtifact} /> : <><section className="min-w-0" data-reader-why={identity}>
      <h3 className="mb-1 text-xs font-medium text-ink">{text('这一步为什么有用', 'Why this step helps')}</h3>
      <ShortText key={`why:${identity}`} value={brief.why} expandLabel={text('完整说明', 'Full explanation')} clamp={false} />
    </section>
    {brief.concept ? <section className="min-w-0" data-reader-teaching={identity}>
      <h3 className="mb-3 text-xs font-medium text-ink">{text('认识一个概念', 'One useful concept')} · {brief.concept.name}</h3>
      <div className="grid min-w-0 gap-3" data-reader-teaching-columns={identity}>
        <div className="min-w-0" data-reader-definition={identity}>
          <h4 className="mb-1 text-xs font-medium text-ink">{text('定义', 'Definition')}</h4>
          <ShortText key={`concept:${identity}:${brief.concept.name}`} value={brief.concept.explanation} expandLabel={text('完整定义', 'Full definition')} clamp={false} />
        </div>
        <div className="min-w-0">
          <div className="border-l-2 border-line pl-3 text-[13px] leading-6 text-ink-dim" data-testid="concept-example">
            <h4 className="mb-1 text-xs font-medium text-ink">{text('例子', 'Example')}</h4>
            <MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{brief.concept.example}</MarkdownContent>
          </div>
          {offersDirectionExample(brief.concept) ? <RawDisclosure label={text('动手看两个方向', 'Try two directions')}><DirectionExample /></RawDisclosure> : null}
        </div>
        <div className="min-w-0" data-reader-connection={identity}>
          <h4 className="mb-1 text-xs font-medium text-ink">{text('与本步的关系', 'Connection to this step')}</h4>
          <div className="text-[13px] leading-6 text-ink-dim"><MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{brief.concept.connection}</MarkdownContent></div>
        </div>
      </div>
    </section> : null}</>}
    <section className="min-w-0"><h3 className="mb-1 text-xs font-medium text-ink">{text('结论到哪里为止', 'What this does and does not establish')}</h3><ShortText key={`scope:${identity}`} value={brief.scope} expandLabel={text('完整适用范围', 'Full scope')} clamp={false} /></section>
    <section className="min-w-0" data-reader-next-interpretation={identity}><h3 className="mb-1 text-xs font-medium text-ink">{text('解说对后续的理解', 'How the explanation interprets the follow-up')}</h3><ShortText key={`next:${identity}`} value={brief.next} expandLabel={text('完整解读', 'Full interpretation')} clamp={false} /></section>
    {detail ? <RawDisclosure key={`detail:${identity}`} className="min-w-0" label={text('详细说明与条件', 'Detailed explanation and conditions')}>
      <div className="macro-reader-markdown text-[13px] leading-6 text-ink-dim"><MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{detail}</MarkdownContent></div>
    </RawDisclosure> : null}
  </div>;
}
