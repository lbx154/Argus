import { useId, useState } from 'react';
import { useI18n } from '../i18n';
import { Button } from '../components/primitives';
import { theme } from '../lib/theme';
import type { ReaderBrief } from '../map/presentation';

type Vector = readonly [number, number];
const A: Vector = [1, 0];
const CHOICES = [
  { id: 'up', vector: [0, 1] as Vector, zh: '向上 1 格', en: 'Up 1 square' },
  { id: 'right', vector: [2, 0] as Vector, zh: '向右 2 格', en: 'Right 2 squares' },
  { id: 'fraction', vector: [1.5, 0] as Vector, zh: '向右 1.5 格', en: 'Right 1.5 squares' },
  { id: 'zero', vector: [0, 0] as Vector, zh: '留在原点', en: 'Stay at the origin' },
] as const;

/** Exact for the small integer/half-integer examples offered by this view. */
export function directionCount(a: Vector, b: Vector): 0 | 1 | 2 {
  if (a[0] * b[1] - a[1] * b[0] !== 0) return 2;
  return a.some(Boolean) || b.some(Boolean) ? 1 : 0;
}

/** Offers a topic-specific illustration, not a verdict on generated text. */
export function offersDirectionExample(concept: ReaderBrief['concept']): boolean {
  if (!concept) return false;
  const text = [concept.name, concept.explanation, concept.example, concept.connection].join(' ');
  return /独立(?:的)?方向|线性(?:无关|独立)|independent\s+directions?\b|linear\s+independence\b|linearly\s+independent\b/iu.test(text);
}

export function DirectionExample() {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const text = (chinese: string, english: string) => zh ? chinese : english;
  const [choice, setChoice] = useState<typeof CHOICES[number]>(CHOICES[0]);
  const id = useId();
  const b = choice.vector;
  const count = directionCount(A, b);
  const x = (value: number) => 62 + value * 36;
  const y = (value: number) => 116 - value * 36;
  const explanation = choice.id === 'up'
    ? text('只沿 A 左右移动，走不到 B 的终点；B 补上了上下方向。', 'Moving left or right along A cannot reach B’s endpoint. B adds an up-and-down direction.')
    : choice.id === 'right'
      ? text('走两次 A 就能代替 B。步子更长，并没有增加独立方向。', 'Two copies of A replace B. A longer step does not add an independent direction.')
      : choice.id === 'fraction'
        ? text('走两次 B 和走三次 A，都向右走了 3 格。虽然两者互相都不是整数倍，仍只有一个独立方向。', 'Two copies of B and three copies of A both move 3 squares right. Neither is an integer multiple of the other, but they still provide only one independent direction.')
        : text('B 没有移动，没有增加方向；A 仍提供一个独立方向。', 'B does not move, so it adds no direction. A still provides one independent direction.');
  const result = text(`这两支箭头提供 ${count} 个独立方向`, `These two arrows provide ${count} independent direction${count === 1 ? '' : 's'}`);
  return <section className="my-2 rounded-lg border border-line bg-surface p-3" aria-label={text('平面上的独立方向示意', 'Independent directions in a plane')} data-testid="direction-example">
    <p className="text-xs leading-5 text-ink-dim">{text('A 固定向右走 1 格。改变 B，看看它是否提供新的走法。允许把箭头缩放、反向，再相加。', 'A always moves 1 square right. Change B to see whether it adds a new way to move. Arrows may be scaled, reversed, and added.')}</p>
    <svg viewBox="0 0 210 180" className="mx-auto my-2 block w-full max-w-[260px]" role="img" aria-labelledby={`${id}-title ${id}-desc`}>
      <title id={`${id}-title`}>{result}</title>
      <desc id={`${id}-desc`}>{`A = (1, 0), B = (${b[0]}, ${b[1]}). ${explanation}`}</desc>
      <defs>
        <marker id={`${id}-a`} markerWidth="7" markerHeight="7" refX="5.5" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0 0 L6 3 L0 6" fill={theme.accent} /></marker>
        <marker id={`${id}-b`} markerWidth="7" markerHeight="7" refX="5.5" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0 0 L6 3 L0 6" fill={theme.success} /></marker>
      </defs>
      <rect x="20" y="32" width="165" height="125" rx="4" fill={theme.accent} opacity={count === 2 ? 0.07 : 0} />
      {[-1, 0, 1, 2, 3].map(value => <line key={`x${value}`} x1={x(value)} x2={x(value)} y1="32" y2="157" stroke="rgb(var(--line))" />)}
      {[-1, 0, 1, 2].map(value => <line key={`y${value}`} y1={y(value)} y2={y(value)} x1="20" x2="185" stroke="rgb(var(--line))" />)}
      {count === 1 ? <line x1="20" x2="185" y1={y(0)} y2={y(0)} stroke={theme.accent} strokeWidth="10" opacity=".09" /> : null}
      <line x1="20" x2="185" y1={y(0)} y2={y(0)} stroke={theme.inkFaint} />
      <line x1={x(0)} x2={x(0)} y1="32" y2="157" stroke={theme.inkFaint} />
      {[0, 1, 2, 3].map(value => <text key={value} x={x(value) - 3} y={y(0) + 14} fill={theme.inkFaint} fontSize="9">{value}</text>)}
      {choice.id !== 'zero' ? <line x1={x(0)} y1={y(0)} x2={x(b[0])} y2={y(b[1])} stroke={theme.success} strokeWidth="3" markerEnd={`url(#${id}-b)`} data-testid="direction-b" />
        : <circle cx={x(0)} cy={y(0)} r="5" fill="rgb(var(--surface))" stroke={theme.success} strokeWidth="3" data-testid="direction-b-zero" />}
      <line x1={x(0)} y1={y(0)} x2={x(A[0])} y2={y(A[1])} stroke={theme.accent} strokeWidth="3" markerEnd={`url(#${id}-a)`} />
      <text x={x(A[0]) + 3} y={y(A[1]) - 10} fill={theme.accent} fontSize="12" fontWeight="600">A</text>
      <text x={x(b[0]) + (choice.id === 'zero' ? -17 : 8)} y={y(b[1]) + (choice.id === 'up' ? -3 : 28)} fill={theme.success} fontSize="12" fontWeight="600">B</text>
    </svg>
    <div className="flex flex-wrap gap-1.5" role="group" aria-label={text('选择 B 的走法', 'Choose B’s movement')}>
      {CHOICES.map(option => <Button key={option.id} className="text-[11px]" variant={choice.id === option.id ? 'primary' : 'ghost'} aria-pressed={choice.id === option.id} onClick={() => setChoice(option)} data-choice={option.id}>{zh ? option.zh : option.en}</Button>)}
    </div>
    <div className="mt-3" aria-live="polite">
      <p className="text-xs font-medium text-ink" data-testid="direction-count">{result}</p>
      <p className="mt-1 text-xs leading-5 text-ink-dim">{explanation}</p>
    </div>
    <p className="mt-3 border-t border-line pt-2 text-[11px] leading-5 text-ink-faint">{text('这里只统计这两支箭头提供的方向。找到一些独立方向，不等于已经找全研究对象的所有方向；图里的数字不能用来计算曲面的 Picard 数，也不能证明研究中的条件。', 'This counts only the directions supplied by these two arrows. Finding some independent directions does not establish that they span the whole research object. These numbers cannot determine a surface’s Picard number or prove the conditions used in the research.')}</p>
  </section>;
}
