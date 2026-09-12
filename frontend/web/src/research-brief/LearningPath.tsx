import type { ArtifactInfo } from '../api';
import type { ReaderLearningPath } from '../map/presentation';
import { MarkdownContent } from '../components/MarkdownContent';
import { RawDisclosure } from '../components/primitives';
import { useI18n } from '../i18n';

/** Read every step in order; an answer disclosure is not a learning score. */
export function LearningPath({ path, identity, artifacts, onOpenArtifact }: {
  path: ReaderLearningPath; identity: string;
  artifacts?: ArtifactInfo[]; onOpenArtifact?: (path: string) => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const markdownProps = { artifacts, onOpenArtifact };
  return <section className="min-w-0" data-reader-learning-path={identity}>
    <h3 className="mb-1 text-xs font-medium text-ink">{zh ? '理解这个问题' : 'Understand the question'}</h3>
    <div className="text-[13px] leading-6 text-ink-dim" data-learning-question>
      <MarkdownContent {...markdownProps}>{path.question}</MarkdownContent>
    </div>
    <ol className="mt-4 grid list-none gap-5 p-0">
      {path.steps.map((step, index) => <li key={`${identity}:${index}`} className="min-w-0 border-t border-line/50 pt-3" data-learning-step={index + 1}>
        <h4 className="mb-2 text-sm font-medium text-ink"><span className="mr-2 text-ink-faint">{index + 1}.</span>{step.title}</h4>
        <div className="text-[13px] leading-6 text-ink-dim" data-learning-explanation>
          <MarkdownContent {...markdownProps}>{step.explanation}</MarkdownContent>
        </div>
        <div className="mt-3 border-l-2 border-line pl-3 text-[13px] leading-6 text-ink-dim" data-learning-example>
          <h5 className="mb-1 text-xs font-medium text-ink">{zh ? '例子' : 'Example'}</h5>
          <MarkdownContent {...markdownProps}>{step.example}</MarkdownContent>
        </div>
        <div className="mt-3 text-[13px] leading-6 text-ink-dim" data-learning-check>
          <h5 className="mb-1 text-xs font-medium text-ink">{zh ? '小问题' : 'Try it'}</h5>
          <div data-learning-check-question><MarkdownContent {...markdownProps}>{step.check.question}</MarkdownContent></div>
          <RawDisclosure key={`${step.check.question}:${step.check.answer}`} label={zh ? '点击看解答' : 'Show the answer'}>
            <div data-learning-check-answer><MarkdownContent {...markdownProps}>{step.check.answer}</MarkdownContent></div>
          </RawDisclosure>
        </div>
      </li>)}
    </ol>
  </section>;
}
