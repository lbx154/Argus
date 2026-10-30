import type { BacklogSummary, EventMsg } from '../../../core/src/types';
import { PROMPT_COMPLETION_VISIBLE_ROWS as ROWS, type CompletionItem } from '../components/PromptCompletionMenu';
import type { Locale } from '../i18n';
import { referenceText, splitDraft, type CardReference } from '../map/presentation';
import { isInternalEntry, type WorkspaceEntry } from '../research-workbench/workspaceApi';
import { statusLabel } from './enumLabels';

type Translate = (key: string, variables?: Record<string, string | number>) => string;

export const HISTORY_MIN_CHARS = 3;

/** What the main composer completes from; absent on the landing page. */
export interface PromptSources {
  sid: string;
  tasks: readonly BacklogSummary[];
  /** {@link promptHistory}, newest first. */
  history: readonly string[];
  /** Undefined until the first `@` loads the workspace listing. */
  files?: readonly WorkspaceEntry[];
  /** Whether the caret is in an `@` word; the listing loads, or refreshes once stale, while it is. */
  wantFiles: (want: boolean) => void;
}
export interface MentionToken { trigger: '@' | '#'; query: string; start: number; end: number }
export interface CompletionMenu { kind: '@' | '#'; items: CompletionItem[] }

/** The `@path` or `#task` word the caret is in. Glued to a word (a@example.com, C#, issue#12)
 *  or followed by a space (`# heading`), it stays text. Past the caret it takes only ASCII
 *  path characters, so the rest of a Chinese sentence it was typed into stays. */
export function completionToken(text: string, at: number): MentionToken | null {
  const match = at < 0 ? null : /(^|[^\w.@#/+\-＠＃])([@#＠＃])([^\s@#＠＃]*)$/.exec(text.slice(0, at));
  if (!match) return null;
  const start = match.index + match[1].length;
  return { trigger: /[@＠]/.test(match[2]) ? '@' : '#', query: match[3], start,
    end: at + /^[\w./~+-]*/.exec(text.slice(at))![0].length };
}

/** The rows offered for the caret's `@`/`#` word. */
export function completionMenu(
  value: string, token: MentionToken | null, sources: PromptSources | undefined, t: Translate, locale: Locale,
): CompletionMenu | null {
  if (!sources || !token) return null;
  const items = token.trigger === '@' ? fileRows(value, token, sources.files ?? []) : taskRows(value, token, sources, t, locale);
  return items.length ? { kind: token.trigger, items } : null;
}

/** Names starting with the query, then paths starting with it, then paths containing it.
 *  An empty query or one ending in `/` lists that one folder level. */
function fileRows(value: string, token: MentionToken, files: readonly WorkspaceEntry[]): CompletionItem[] {
  const needle = token.query.toLowerCase();
  const browsing = !needle || needle.endsWith('/');
  const tiers: WorkspaceEntry[][] = [[], [], []];
  for (const entry of files) {
    // A control character in a name could forge a reference line in the draft.
    if (isInternalEntry(entry) || /[\u0000-\u001f\u007f]/.test(entry.path)) continue;
    const path = entry.path.toLowerCase();
    const tier = browsing ? (path.startsWith(needle) && !path.slice(needle.length).includes('/') ? 0 : -1)
      : entry.name.toLowerCase().startsWith(needle) ? 0 : path.startsWith(needle) ? 1 : path.includes(needle) ? 2 : -1;
    if (tier < 0 || tiers[tier].length >= ROWS) continue;
    tiers[tier].push(entry);
    if (tiers[0].length >= ROWS) break;
  }
  const { refs, text } = splitDraft(value);
  const before = refs.map(referenceText).join('') + text.slice(0, token.start);
  const after = text.slice(token.end);
  return tiers.flat().slice(0, ROWS).map((entry, index) => {
    const folder = entry.type === 'directory';
    const path = folder ? `${entry.path}/` : entry.path;
    const quoted = /\s/.test(path);
    const word = quoted ? `@"${path}"` : `@${path}`;
    // A folder stays open so the next level narrows it; anything else ends the word.
    const insert = (folder && !quoted) || /^\s/.test(after) ? word : `${word} `;
    const slash = entry.path.lastIndexOf('/');
    return {
      id: `file-${index}`, name: entry.name + (folder ? '/' : ''), detail: slash > 0 ? entry.path.slice(0, slash) : undefined,
      value: before + insert + after, caret: token.start + insert.length, close: !folder || quoted,
    };
  });
}

/** Tasks whose title holds the query, newest first; choosing one quotes it like a map card. */
function taskRows(value: string, token: MentionToken, sources: PromptSources, t: Translate, locale: Locale): CompletionItem[] {
  const needle = token.query.toLowerCase();
  const { refs, text } = splitDraft(value);
  const before = text.slice(0, token.start);
  const rest = text.slice(token.end);
  const after = (!before || /\s$/.test(before)) && rest.startsWith(' ') ? rest.slice(1) : rest;
  return sources.tasks.filter((task) => task.title.toLowerCase().includes(needle))
    .sort((a, b) => (b.ts ?? 0) - (a.ts ?? 0)).slice(0, ROWS)
    .map((task, index) => {
      const quoted = refs.some((ref) => ref.task_id === task.id && !ref.step_id);
      const ref: CardReference = { source: `live:${sources.sid}`, task_id: task.id, task_title: task.title, event_ids: [], lang: locale === 'zh-CN' ? 'zh' : 'en' };
      return {
        id: `task-${index}`, name: `#${task.title}`, arg: statusLabel(task.status, t), detail: task.id,
        value: (quoted ? refs : [...refs, ref]).map(referenceText).join('') + before + after, caret: before.length,
      };
    });
}

/** Earlier one-line messages worth offering again, newest first, without reference
 *  markers, attachment lists, option clicks or anything the vault replaced. */
export function promptHistory(events: readonly EventMsg[]): string[] {
  const seen = new Set<string>();
  for (let index = events.length - 1; index >= 0 && seen.size < 50; index -= 1) {
    if (events[index].type !== 'ui.operator') continue;
    const text = splitDraft(String(events[index].text ?? '').split('## Operator attachments')[0]).text.split('\n')
      .filter((line) => !line.startsWith('（引用：') && !line.startsWith('(Referenced: ')).join('\n').trim();
    const length = [...text].length;
    if (length < 4 || length > 200 || text.includes('\n') || /^\d+$/.test(text) || text.startsWith('/')
      || text.includes('[stored in capability vault]')) continue;
    seen.add(text);
  }
  return [...seen];
}

/** The whole line the gray text proposes: an earlier message this draft begins; '' when there is none. */
export function ghostFor(text: string, history: readonly string[]): string {
  if ([...text.trim()].length < HISTORY_MIN_CHARS) return '';
  return history.find((prompt) => prompt.length > text.length && prompt.startsWith(text)) ?? '';
}
