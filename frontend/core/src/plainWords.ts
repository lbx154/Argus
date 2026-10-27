/**
 * The host's own words said plainly (docs: ux-principles rule 1), shared by the
 * web feed and the terminal: the bare status word a role ends its message with
 * is read in the reader's language. Words a role wrote stay as written.
 */

export type PlainLocale = 'en' | 'zh-CN';

const zh = (locale: PlainLocale) => locale === 'zh-CN';

/** A verdict's status as the agent wrote it on its last line ("…\n\ncontinue"). */
export const STATUS_TAIL = /(?:^|\n)[ \t]*`?(?:continue|done|blocked|replan_requested|question|no_progress)`?[ \t.]*$/i;

const STATUS_WORDS: Record<string, [string, string]> = {
  continue: ['Carrying on with another round.', '接着再做一轮。'],
  done: ['Called the work done.', '认为工作做完了。'],
  blocked: ['The work is stuck and needs a decision.', '工作卡住了，需要定夺。'],
  replan_requested: ['Asked for a new plan.', '请求重新规划。'],
  question: ['Leaves the choice to you.', '把选择交给你。'],
  no_progress: ['No progress this round.', '这一轮没有进展。'],
};

/** A bare status word (`replan_requested`) as a plain sentence, or '' when unknown. */
export function plainStatusWord(word: string, locale: PlainLocale): string {
  const pair = STATUS_WORDS[word.trim().replace(/^`|`$/g, '').replace(/\.$/, '').toLowerCase()];
  return pair ? pair[zh(locale) ? 1 : 0] : '';
}

/**
 * A role's message in plain words: the status word ending it is dropped (the
 * verdict says it in words), and a bare status reads as its sentence.
 */
export function plainRoleMessage(text: string, locale: PlainLocale): string {
  const said = String(text ?? '').trim();
  if (!STATUS_TAIL.test(said)) return said;
  const rest = said.replace(STATUS_TAIL, '').trim();
  return rest || plainStatusWord(said, locale) || said;
}
