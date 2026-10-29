// What the record says beyond the map: a task in full, and a search
// of every project's request, conversation and letters, read from project.json and events.jsonl.

import { NAMES } from '../prompts.js';
import type { Note, Project, Store } from '../store.js';
import { HttpError, type Handler } from './index.js';
import { named as projectName, progressMap, readEvents, projectRecord, conversationRows } from './views.js';

const RESULTS = 50;
const EXCERPT = 32_000;

const named = (to: string): string => NAMES[to as Note['from']] ?? to;

export function records(store: Store, project: (id: string) => Project): [string, RegExp, Handler][] {
  return [
    ['GET', /^\/api\/projects\/([^/]+)\/backlog\/([^/]+)$/, ({ params: [id, task] }) => ({ item: detail(store, project(id!), task!) })],
    ['GET', /^\/api\/search$/, ({ url }) => search(store, url.searchParams.get('q') ?? '')],
  ];
}

/** A task in full: its card on the map, every letter written about it in order, by whom to whom,
 * and the error its last call ended with, if that call failed. */
function detail(store: Store, project: Project, id: string) {
  const card = progressMap(store, project, Infinity).tasks.find(task => task.id === id);
  if (!card) throw new HttpError(404, `unknown backlog item: ${id}`);
  const letters: string[] = [];
  let error = '';
  for (const event of readEvents(store, project.id)) {
    if (event.type === 'handoff' && event.task === id) letters.push(`${named(event.role)} to the ${named(event.to)}:\n${event.letter}`);
    else if (event.type === 'call.end' && event.task === id) error = event.error ?? '';
  }
  return { ...card, notes: letters.join('\n\n'), last_error: error };
}

/** Where a text is quoted for the line found at `hit`: the paragraph, list or table it is in,
 * which the page renders as Markdown; of a long one, the part around it. */
function snippet(text: string, hit: number): string {
  const paragraph = text.lastIndexOf('\n\n', hit), end = text.indexOf('\n\n', hit);
  const boundary = paragraph < 0 ? 0 : paragraph + 2;
  let start = Math.max(boundary, hit - 200);
  // Keep the entire word or path containing the cut, even when it is long.
  while (start > boundary && !/\s/.test(text[start - 1]!)) start--;
  let finish = Math.min(end < 0 ? text.length : end, hit + 400);
  while (finish < text.length && !/\s/.test(text[finish]!)) finish++;
  // Spaces inside Markdown code spans belong to the quoted path, not its boundary.
  for (const code of text.slice(boundary, end < 0 ? text.length : end).matchAll(/(`+)[\s\S]*?\1/g)) {
    const opening = boundary + code.index, closing = opening + code[0].length;
    if (opening < start && start < closing) start = opening;
    if (opening < finish && finish < closing) finish = closing;
  }
  // However long the word or span, the quote stays near what was found.
  return text.slice(Math.max(start, hit - 1_000), Math.min(finish, hit + 1_000)).trim();
}

/** What holds every word asked, newest project first: a project's request, its conversation, and
 * the team's letters. A project whose record cannot be read is named in `errors`. */
function search(store: Store, query: string) {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) throw new HttpError(422, 'Enter at least one search term', 'search_required');
  const results: Record<string, unknown>[] = [];
  const errors: string[] = [];
  let partial = false;
  for (const project of store.list().reverse()) {
    if (partial) break;
    const title = projectName(project) || project.id;
    const match = (text: string, kind: string, more: Record<string, unknown> = {}): void => {
      const folded = text.toLowerCase();
      if (!terms.every(term => folded.includes(term))) return;
      if (results.length >= RESULTS) { partial = true; return; }
      const hit = folded.indexOf(terms[0]!);
      const at = Math.max(0, hit - 65);
      results.push({
        sid: project.id, title, kind, snippet: snippet(text, hit), ...more,
        text: text.length > EXCERPT ? text.slice(Math.max(0, at - 1_000), at + EXCERPT - 1_000) : text, truncated: text.length > EXCERPT,
      });
    };
    try {
      const events = readEvents(store, project.id);
      // Folded once here, not kept: a search reads every project, and the open ones are kept already.
      const dialogue = conversationRows(projectRecord(events), project, { since: null, calls: [], requests: [] }).filter(row => row.type === 'ui.operator' || row.type === 'ui.argus');
      // The request is the first operator message, also kept on project.json. Prefer its
      // conversation entry so opening the hit can locate that message. Independent messages
      // (even identical words), and the same words from another speaker, remain separate.
      if (!dialogue.some(row => row.type === 'ui.operator' && row.text === project.request)) match(project.request, 'project', { role: 'operator' });
      for (const { type, text, ts, message_id } of dialogue) {
        match(String(text), 'conversation', { role: type === 'ui.operator' ? 'operator' : 'argus', ts, message_id });
      }
      // The Manager's letters are the conversation's replies; the others' open the project.
      for (const event of events) {
        if (event.type === 'handoff' && event.role !== 'manager') match(event.letter, 'project', { role: event.role, ts: Date.parse(event.ts) / 1000 });
      }
    } catch (error) {
      errors.push(`${project.id}: ${error instanceof Error ? error.message : String(error)}`);
      match(project.request, 'project', { role: 'operator' });
    }
  }
  return { results, partial, errors };
}
