/**
 * Slash-command registry + completion + dispatch parsing — the CLI's 1:1 mirror
 * of the Python cockpit's SLASH_COMMANDS. Pure logic (no Ink) so it is unit
 * tested. Each command declares a ``kind``: 'panel' opens an overlay view,
 * 'action' fires a one-shot API call, 'local' is handled in-client.
 */

import { EVENT_VIEW_FILTERS, type EventViewFilter } from '../../../core/src/events.js';

export type SlashKind = 'panel' | 'action' | 'local';

export interface SlashCmd {
  name: string; // canonical, e.g. "/status"
  arg?: string; // usage hint
  desc: string;
  aliases?: string[];
  group: string; // for grouped /help
  kind: SlashKind;
}

export const SLASH_COMMANDS: SlashCmd[] = [
  // ── everyday (read/inspect panels) ──
  { name: '/status', desc: 'roles, queued work, journal, and health', group: 'Everyday', kind: 'panel' },
  { name: '/roles', desc: 'per-role backend / model / effort + live activity', group: 'Everyday', kind: 'panel' },
  { name: '/journal', arg: '[N]', desc: 'recent journal entries (default 10)', group: 'Everyday', kind: 'panel' },
  { name: '/backlog', arg: '[all]', desc: 'pending tasks (all = incl. done/skipped)', group: 'Everyday', kind: 'panel' },
  { name: '/artifacts', desc: 'reviewer-approved result files (Enter previews)', group: 'Everyday', kind: 'panel' },
  { name: '/artifact', arg: '<path>', desc: 'preview one approved result file', group: 'Everyday', kind: 'panel' },
  { name: '/events', arg: '[filter] [query]', desc: 'search feed: all / watch / milestones / messages', group: 'Everyday', kind: 'panel' },
  { name: '/find', arg: '<text>', desc: 'search the current event buffer', group: 'Everyday', kind: 'panel' },
  { name: '/cancel', desc: 'stop waiting for the current Manager reply', aliases: ['/abort'], group: 'Everyday', kind: 'local' },
  // ── task management (actions) ──
  { name: '/task', arg: '<text>', desc: 'queue work directly', aliases: ['/add'], group: 'Task management', kind: 'action' },
  { name: '/plan', arg: '<objective>', desc: 'preview a Planner-authored execution plan', group: 'Task management', kind: 'action' },
  { name: '/nudge', arg: '<text>', desc: 'inject guidance into the running mission', aliases: ['/inject', '/notify'], group: 'Task management', kind: 'action' },
  { name: '/note', arg: '<text>', desc: 'append a manual note to the timeline', group: 'Task management', kind: 'action' },
  { name: '/done', arg: '<id>', desc: 'mark a task done', group: 'Task management', kind: 'action' },
  { name: '/skip', arg: '<id>', desc: 'skip a task', aliases: ['/rm'], group: 'Task management', kind: 'action' },
  { name: '/stop', arg: '<id>', desc: "stop a task's auto-iteration", group: 'Task management', kind: 'action' },
  { name: '/item', arg: '<id>', desc: 'inspect a full task contract', group: 'Task management', kind: 'panel' },
  { name: '/run', desc: 'return to the always-live mission feed', group: 'Task management', kind: 'local' },
  // ── sessions & diagnostics ──
  { name: '/new', arg: '[objective]', desc: 'review, create, and switch to a fresh conversation', group: 'Sessions & diagnostics', kind: 'action' },
  { name: '/daemons', arg: '[query]', desc: 'find every session + switch or create', group: 'Sessions & diagnostics', kind: 'panel' },
  { name: '/resume', arg: '[list|<id>]', desc: 'switch to another project/session', group: 'Sessions & diagnostics', kind: 'action' },
  { name: '/attach', arg: '<id|prefix>', desc: 'follow another project (read the stream)', group: 'Sessions & diagnostics', kind: 'action' },
  { name: '/doctor', desc: "diagnose 'why isn't anything running'", group: 'Sessions & diagnostics', kind: 'panel' },
  // ── configuration ──
  { name: '/backend', arg: '[codex|claude|copilot]', desc: 'view or change the shared runner backend', group: 'Configuration', kind: 'action' },
  { name: '/config', arg: '[key=value …]', desc: 'view or change runtime settings', group: 'Configuration', kind: 'panel' },
  { name: '/identity', arg: '[set <text>]', desc: 'view or replace the operator identity card', group: 'Configuration', kind: 'panel' },
  { name: '/reset', desc: 'drop the warm Manager conversation context', group: 'Configuration', kind: 'action' },
  { name: '/skills', arg: '[ls|promote <name>]', desc: 'inspect or promote runtime skills', group: 'Configuration', kind: 'action' },
  // ── other (local) ──
  { name: '/clear', desc: 'clear the event feed view', group: 'Other', kind: 'local' },
  { name: '/reconnect', desc: 'reconnect the live event stream', group: 'Other', kind: 'local' },
  { name: '/help', desc: 'keys + full command reference', aliases: ['/?', '/commands'], group: 'Other', kind: 'local' },
  { name: '/quit', desc: 'leave the cockpit (background work keeps running)', aliases: ['/exit', '/q'], group: 'Other', kind: 'local' },
];

const CANON = new Map<string, SlashCmd>();
for (const c of SLASH_COMMANDS) {
  for (const n of [c.name, ...(c.aliases ?? [])]) CANON.set(n.toLowerCase(), c);
}

export function isSlash(line: string): boolean {
  return line.startsWith('/');
}

/** Completions while typing the command TOKEN (before the first space). */
export function slashCompletions(line: string): SlashCmd[] {
  if (!isSlash(line) || line.includes(' ')) return [];
  const token = line.toLowerCase();
  const seen = new Set<string>();
  const out: SlashCmd[] = [];
  for (const c of SLASH_COMMANDS) {
    const names = [c.name, ...(c.aliases ?? [])];
    if (names.some((n) => n.toLowerCase().startsWith(token)) && !seen.has(c.name)) {
      seen.add(c.name);
      out.push(c);
    }
  }
  // Prefix siblings such as /artifact + /artifacts must not steal Enter from
  // an exactly typed command. Keep registry order
  // otherwise, but promote a canonical/alias exact match to the first row.
  return out.sort((a, b) => Number(isExact(b, token)) - Number(isExact(a, token)));
}

function isExact(command: SlashCmd, token: string): boolean {
  return [command.name, ...(command.aliases ?? [])].some((name) => name.toLowerCase() === token);
}

export function applyCompletion(cmd: SlashCmd): string {
  return cmd.arg ? `${cmd.name} ` : cmd.name;
}

export interface ParsedCommand {
  cmd: SlashCmd | null; // null → unknown
  name: string; // canonical (or the typed token if unknown)
  rest: string;
}

export interface EventViewArgs {
  filter: EventViewFilter;
  query: string;
}

export type ResumeTarget =
  | { kind: 'list' }
  | { kind: 'project'; query: string };

/** ``/resume`` and ``/resume list`` both open the session picker. */
export function parseResumeTarget(rest: string): ResumeTarget {
  const query = rest.trim();
  return !query || query.toLowerCase() === 'list'
    ? { kind: 'list' }
    : { kind: 'project', query };
}

export function parseEventViewArgs(rest: string): EventViewArgs {
  const trimmed = rest.trim();
  if (!trimmed) return { filter: 'all', query: '' };
  const [first, ...tail] = trimmed.split(/\s+/);
  if (first.toLowerCase() === 'watch') {
    return { filter: 'attention', query: tail.join(' ') };
  }
  if ((EVENT_VIEW_FILTERS as readonly string[]).includes(first.toLowerCase())) {
    return { filter: first.toLowerCase() as EventViewFilter, query: tail.join(' ') };
  }
  return { filter: 'all', query: trimmed };
}

export function parseCommand(line: string): ParsedCommand | null {
  if (!isSlash(line)) return null;
  const sp = line.indexOf(' ');
  const token = (sp === -1 ? line : line.slice(0, sp)).toLowerCase();
  const rest = sp === -1 ? '' : line.slice(sp + 1).trim();
  const cmd = CANON.get(token) ?? null;
  return { cmd, name: cmd ? cmd.name : token, rest };
}

/** difflib-style "did you mean /x?" for an unknown command token. */
export function didYouMean(token: string): string | null {
  const t = token.toLowerCase();
  let best: string | null = null;
  let bestScore = 0;
  for (const name of CANON.keys()) {
    const s = similarity(t, name);
    if (s > bestScore) {
      bestScore = s;
      best = CANON.get(name)!.name;
    }
  }
  return bestScore >= 0.6 ? best : null;
}

/** Ratcliff/Obershelp-ish ratio via normalized edit distance. */
function similarity(a: string, b: string): number {
  const d = levenshtein(a, b);
  const max = Math.max(a.length, b.length) || 1;
  return 1 - d / max;
}

function levenshtein(a: string, b: string): number {
  const m = a.length;
  const n = b.length;
  const dp = Array.from({ length: m + 1 }, (_, i) => [i, ...Array(n).fill(0)]);
  for (let j = 0; j <= n; j++) dp[0][j] = j;
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = Math.min(
        dp[i - 1][j] + 1,
        dp[i][j - 1] + 1,
        dp[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1),
      );
    }
  }
  return dp[m][n];
}

/** Grouped view for /help, with aliases folded ('/skip  (= /rm)'). */
export function helpGroups(): Array<{ group: string; rows: Array<{ label: string; desc: string }> }> {
  const order = ['Everyday', 'Task management', 'Sessions & diagnostics', 'Configuration', 'Other'];
  const groups = new Map<string, Array<{ label: string; desc: string }>>();
  for (const c of SLASH_COMMANDS) {
    const aliasNote = c.aliases?.length ? `  (= ${c.aliases.join(', ')})` : '';
    const label = `${c.name}${c.arg ? ` ${c.arg}` : ''}${aliasNote}`;
    if (!groups.has(c.group)) groups.set(c.group, []);
    groups.get(c.group)!.push({ label, desc: c.desc });
  }
  return order.filter((g) => groups.has(g)).map((g) => ({ group: g, rows: groups.get(g)! }));
}
