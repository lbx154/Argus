/**
 * The host's own words said plainly (docs: ux-principles rule 1), shared by the
 * web feed and the terminal. Routing reasons the host writes in English
 * (argus/engineer/round_routing.py; the Python side is
 * argus/core/operator_messages.py `humanize_runtime_reason`) and the bare status
 * word a role ends its message with are read in the reader's language. Words a
 * role wrote stay as written.
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
 * The host's own reasons for where work went, said in the reader's language as
 * what they mean for the work: which role held it and which one holds it now
 * are internal hand-offs, never the reader's words (same wording as the Python
 * side's `plain_routing_reason`).
 */
export function plainInternalReason(text: string, locale: PlainLocale): string {
  return text.replace(
    /Nothing changed since the [a-z]+(?: and the [a-z]+)* last held this work, and nothing new arrived from outside, so it went one rung up, to the ([a-z]+)\.(?:\s*The Reviewer had handed it to the [a-z]+:)?\s*/gi,
    (_match, holder: string) => {
      if (holder.toLowerCase() === 'operator') {
        return zh(locale) ? '这条路没有新进展，需要你决定怎么继续。' : 'This path is making no new progress; it needs your decision on how to go on. ';
      }
      return zh(locale) ? '这条路没有新进展，团队正在换个思路。' : 'This path is making no new progress, so the team is trying a different approach. ';
    },
  ).trim();
}

/**
 * A role's message in plain words: the status word ending it is dropped (the
 * verdict says it in words), a bare status reads as its sentence, and the
 * host's English reasons are said plainly.
 */
export function plainRoleMessage(text: string, locale: PlainLocale): string {
  const said = String(text ?? '').trim();
  if (!STATUS_TAIL.test(said)) return plainInternalReason(said, locale);
  const rest = said.replace(STATUS_TAIL, '').trim();
  return rest ? plainInternalReason(rest, locale) : plainStatusWord(said, locale) || said;
}
