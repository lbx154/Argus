import { useRef, type ReactNode } from 'react';
import { Button } from '../components/primitives';
import { useI18n } from '../i18n';
import type { FoundationDraft } from './foundation';

const readingQuestions = [
  {
    label: ['从头讲清楚', 'Explain from the beginning'],
    question: [
      '请从头讲清楚这份说明。先用日常语言解释它在讨论什么、要判断什么，再按需要解释其中的对象和符号。涉及比较或公式时，请明确两边是什么、哪些条件下能比较，并用一个可复算的小例子说明。请分别说明标准背景知识、本次已有证据和仍未知的内容。',
      'Explain this reading from the beginning using everyday language. Start with what it discusses and what we are trying to determine, then explain the necessary objects and symbols. For a comparison or formula, define both sides and when the comparison is valid, and work a small example I can recompute. Distinguish standard background, evidence from this run, and what remains unknown.',
    ],
  },
  {
    label: ['带我算一遍', 'Work through an example'],
    question: [
      '请带我实际算一遍这份说明中的关键一步。先解释对象、输入和运算规则，再用一组明确标为教学例子的小数据展示中间步骤与结果，然后换一个输入让我能照着计算。请说明它怎样关系到正在判断的结论，以及这个例子还不能证明什么。',
      'Walk me through the key operation in this reading. Explain the objects, inputs, and rules, then use a small example clearly labeled as a teaching example to show the intermediate steps and result. Change one input and work it again so I can follow the calculation. Explain how it relates to the conclusion being assessed and what the example does not prove.',
    ],
  },
  {
    label: ['这说明了什么', 'What does this establish?'],
    question: [
      '这份说明究竟支持什么结论？请用我能懂的语言解释已经验证的部分、依赖的条件、未覆盖的情况，以及它和整个任务目标的关系。解释必要概念，并指出哪些只是记录中的说法、哪些有可核对的依据。',
      'What conclusion does this reading support? Explain the part that has been checked, its assumptions, the cases it does not cover, and how it relates to the overall task. Explain the necessary concepts and distinguish reported claims from conclusions with verifiable supporting evidence.',
    ],
  },
] as const;

/** The same question editor is used for a foundation and a retained progress card. */
export function ReadingQuestionEditor({ draft, onChange, onSubmit, disabled, children }: {
  draft: FoundationDraft; onChange: (draft: FoundationDraft) => void; onSubmit: () => void;
  disabled?: boolean; children?: ReactNode;
}) {
  const zh = useI18n().locale === 'zh-CN';
  const question = !!draft.parentId || !!draft.progressSource;
  const textarea = useRef<HTMLTextAreaElement>(null);
  return <>
    {children}
    {question && !draft.question.trim() ? <div className="mb-3 space-y-2" data-reading-question-suggestions>
      <p className="text-xs text-ink-dim">{zh ? '可以从这些问题开始，也可以直接输入。' : 'Start with one of these questions, or write your own.'}</p>
      <div className="flex flex-wrap gap-2">{readingQuestions.map(suggestion => <Button key={suggestion.label[1]}
        type="button" className="text-xs" disabled={disabled}
        onClick={() => {
          onChange({ ...draft, question: suggestion.question[zh ? 0 : 1] });
          textarea.current?.focus();
        }}>
        {suggestion.label[zh ? 0 : 1]}
      </Button>)}</div>
    </div> : null}
    <textarea ref={textarea} aria-label={zh ? '想理解的问题' : 'Question to understand'} className="min-h-40 w-full rounded-lg border border-line bg-bg p-3 text-sm leading-6 text-ink"
      maxLength={8000} value={draft.question} onChange={event => onChange({ ...draft, question: event.target.value })} />
    {draft.sourceTitle ? <p className="mt-2 text-xs text-ink-faint">{zh ? '从这项任务打开：' : 'Opened from this task: '}{draft.sourceTitle}</p> : null}
    <div className="mt-3 flex justify-end"><Button data-reading-submit disabled={!draft.question.trim() || disabled} onClick={onSubmit}>
      {question ? zh ? '提问' : 'Ask question' : zh ? '生成并保存基础说明' : 'Generate and save foundations'}
    </Button></div>
  </>;
}
