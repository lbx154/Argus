import { EVENT_VIEW_FILTERS, type EventViewFilter } from './events.js';

export type CommandKind = 'panel' | 'action' | 'local';

export type CommandId =
  | 'status' | 'roles' | 'journal' | 'backlog' | 'artifacts' | 'artifact'
  | 'events' | 'find' | 'cancel' | 'ask' | 'task' | 'plan' | 'rewrite' | 'nudge' | 'abort'
  | 'note' | 'done' | 'skip' | 'stop' | 'item' | 'run' | 'new' | 'daemons'
  | 'resume' | 'attach' | 'rename' | 'doctor' | 'backend' | 'config'
  | 'identity' | 'reset' | 'skills' | 'clear' | 'reconnect' | 'help' | 'quit';

export interface SlashCommand {
  id: CommandId;
  name: `/${string}`;
  arg?: string;
  argument: 'none' | 'optional' | 'required';
  desc: string;
  aliases?: `/${string}`[];
  group: 'Everyday' | 'Task management' | 'Sessions & diagnostics' | 'Configuration' | 'Other';
  kind: CommandKind;
}

export const COMMANDS: SlashCommand[] = [
  { id: 'status', name: '/status', argument: 'none', desc: 'roles, queued work, journal, and health', group: 'Everyday', kind: 'panel' },
  { id: 'roles', name: '/roles', argument: 'none', desc: 'per-role backend / model / effort + live activity', group: 'Everyday', kind: 'panel' },
  { id: 'journal', name: '/journal', arg: '[N]', argument: 'optional', desc: 'recent journal entries (default 10)', group: 'Everyday', kind: 'panel' },
  { id: 'backlog', name: '/backlog', arg: '[all]', argument: 'optional', desc: 'pending tasks (all = incl. done/skipped)', group: 'Everyday', kind: 'panel' },
  { id: 'artifacts', name: '/artifacts', argument: 'none', desc: 'reviewer-approved result files (Enter previews)', group: 'Everyday', kind: 'panel' },
  { id: 'artifact', name: '/artifact', arg: '<path>', argument: 'required', desc: 'preview one approved result file', group: 'Everyday', kind: 'panel' },
  { id: 'events', name: '/events', arg: '[filter] [query]', argument: 'optional', desc: 'search feed: all / watch / milestones / messages', group: 'Everyday', kind: 'panel' },
  { id: 'find', name: '/find', arg: '<text>', argument: 'required', desc: 'search the current event buffer', group: 'Everyday', kind: 'panel' },
  { id: 'cancel', name: '/cancel', argument: 'none', desc: 'stop waiting for the current Manager reply', group: 'Everyday', kind: 'local' },
  { id: 'ask', name: '/ask', arg: '<question>', argument: 'required', desc: 'answer inline — no task queued, no Planner/Engineer/Reviewer', aliases: ['/chat'], group: 'Everyday', kind: 'action' },
  { id: 'task', name: '/task', arg: '<text>', argument: 'required', desc: 'queue work directly', aliases: ['/add'], group: 'Task management', kind: 'action' },
  { id: 'plan', name: '/plan', arg: '<objective>', argument: 'required', desc: 'preview a Planner-authored execution plan', group: 'Task management', kind: 'action' },
  { id: 'rewrite', name: '/rewrite', arg: '[text]', argument: 'optional', desc: 'let the Manager rewrite your prompt before sending', aliases: ['/refine'], group: 'Task management', kind: 'action' },
  { id: 'nudge', name: '/nudge', arg: '<text>', argument: 'required', desc: 'inject guidance into the running mission', aliases: ['/inject', '/notify'], group: 'Task management', kind: 'action' },
  { id: 'abort', name: '/abort', argument: 'none', desc: 'immediately stop the running mission', group: 'Task management', kind: 'action' },
  { id: 'note', name: '/note', arg: '<text>', argument: 'required', desc: 'append a manual note to the timeline', group: 'Task management', kind: 'action' },
  { id: 'done', name: '/done', arg: '<id>', argument: 'required', desc: 'mark a task done', group: 'Task management', kind: 'action' },
  { id: 'skip', name: '/skip', arg: '<id>', argument: 'required', desc: 'skip a task', aliases: ['/rm'], group: 'Task management', kind: 'action' },
  { id: 'stop', name: '/stop', arg: '<id>', argument: 'required', desc: "stop a task's auto-iteration", group: 'Task management', kind: 'action' },
  { id: 'item', name: '/item', arg: '<id>', argument: 'required', desc: 'inspect a full task contract', group: 'Task management', kind: 'panel' },
  { id: 'run', name: '/run', argument: 'none', desc: 'return to the always-live mission feed', group: 'Task management', kind: 'local' },
  { id: 'new', name: '/new', arg: '[objective]', argument: 'optional', desc: 'review, create, and switch to a fresh conversation', group: 'Sessions & diagnostics', kind: 'action' },
  { id: 'daemons', name: '/daemons', arg: '[query]', argument: 'optional', desc: 'find every session + switch or create', group: 'Sessions & diagnostics', kind: 'panel' },
  { id: 'resume', name: '/resume', arg: '[list|<id>]', argument: 'optional', desc: 'switch to another project/session', group: 'Sessions & diagnostics', kind: 'action' },
  { id: 'attach', name: '/attach', arg: '<id|prefix>', argument: 'required', desc: 'follow another project (read the stream)', group: 'Sessions & diagnostics', kind: 'action' },
  { id: 'rename', name: '/rename', arg: '<name>', argument: 'required', desc: 'rename the current conversation', group: 'Sessions & diagnostics', kind: 'action' },
  { id: 'doctor', name: '/doctor', argument: 'none', desc: "diagnose 'why isn't anything running'", group: 'Sessions & diagnostics', kind: 'panel' },
  { id: 'backend', name: '/backend', arg: '[codex|claude|copilot|cursor|opencode|pi|grok|qoder|dsh]', argument: 'optional', desc: 'view or change the shared runner backend', group: 'Configuration', kind: 'action' },
  { id: 'config', name: '/config', arg: '[key=value …]', argument: 'optional', desc: 'view or change runtime settings', group: 'Configuration', kind: 'panel' },
  { id: 'identity', name: '/identity', arg: '[set <text>]', argument: 'optional', desc: 'view or replace the operator identity card', group: 'Configuration', kind: 'panel' },
  { id: 'reset', name: '/reset', argument: 'none', desc: 'drop the warm Manager conversation context', group: 'Configuration', kind: 'action' },
  { id: 'skills', name: '/skills', arg: '[ls|promote <name>]', argument: 'optional', desc: 'inspect or promote runtime skills', group: 'Configuration', kind: 'action' },
  { id: 'clear', name: '/clear', argument: 'none', desc: 'clear the event feed view', group: 'Other', kind: 'local' },
  { id: 'reconnect', name: '/reconnect', argument: 'none', desc: 'reconnect the live event stream', group: 'Other', kind: 'local' },
  { id: 'help', name: '/help', argument: 'none', desc: 'keys + full command reference', aliases: ['/?', '/commands'], group: 'Other', kind: 'local' },
  { id: 'quit', name: '/quit', argument: 'none', desc: 'leave the cockpit (background work keeps running)', aliases: ['/exit', '/q'], group: 'Other', kind: 'local' },
];

const COMMAND_BY_ID = new Map<CommandId, SlashCommand>(COMMANDS.map((command) => [command.id, command]));
const CANON = new Map<string, SlashCommand>();

for (const command of COMMANDS) {
  for (const name of [command.name, ...(command.aliases ?? [])]) {
    CANON.set(name.toLowerCase(), command);
  }
}

export function commandById(id: CommandId): SlashCommand {
  const command = COMMAND_BY_ID.get(id);
  if (!command) throw new Error(`unknown command id: ${id}`);
  return command;
}

export function commandNeedsArgument(command: SlashCommand): boolean {
  return command.argument === 'required';
}

/** A command token: `/` followed by one word of letters, digits, - or _.
 *
 * Paths start with `/` too. Treating `/data/yijia/run.py` as a command means
 * answering a pasted path with "Unknown command /data", which is both wrong
 * and unhelpful — the operator wanted to say something, not run something.
 * Requiring a single bare word keeps real typos (`/statu`) reported while
 * letting anything path-shaped through as ordinary text.
 */
const COMMAND_TOKEN = /^\/[A-Za-z0-9_-]+$/;

export function isSlash(line: string): boolean {
  if (!line.startsWith('/')) return false;
  const sp = line.indexOf(' ');
  const token = sp === -1 ? line : line.slice(0, sp);
  return COMMAND_TOKEN.test(token);
}

/** Whether the operator is still typing a command token.
 *
 * Wider than {@link isSlash}: a bare `/` is not yet a command but should open
 * the menu, while `/data/` has already committed to being a path and should
 * not. Completion and dispatch answer different questions.
 */
export function isTypingCommand(line: string): boolean {
  return line.startsWith('/') && !line.includes(' ') && !line.slice(1).includes('/');
}

/** Completions while typing the command TOKEN (before the first space). */
export function slashCompletions(line: string): SlashCommand[] {
  if (!isTypingCommand(line)) return [];
  const token = line.toLowerCase();
  const seen = new Set<string>();
  const out: SlashCommand[] = [];
  for (const command of COMMANDS) {
    const names = [command.name, ...(command.aliases ?? [])];
    if (names.some((name) => name.toLowerCase().startsWith(token)) && !seen.has(command.name)) {
      seen.add(command.name);
      out.push(command);
    }
  }
  // Prefix siblings such as /artifact + /artifacts must not steal Enter from
  // an exactly typed command. Keep registry order otherwise, but promote a
  // canonical/alias exact match to the first row.
  return out.sort((a, b) => Number(isExact(b, token)) - Number(isExact(a, token)));
}

function isExact(command: SlashCommand, token: string): boolean {
  return [command.name, ...(command.aliases ?? [])].some((name) => name.toLowerCase() === token);
}

export function applyCompletion(command: SlashCommand): string {
  return command.arg ? `${command.name} ` : command.name;
}

export interface ParsedCommand {
  cmd: SlashCommand | null; // null → unknown
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
  for (let j = 0; j <= n; j += 1) dp[0][j] = j;
  for (let i = 1; i <= m; i += 1) {
    for (let j = 1; j <= n; j += 1) {
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
  for (const command of COMMANDS) {
    const aliasNote = command.aliases?.length ? `  (= ${command.aliases.join(', ')})` : '';
    const label = `${command.name}${command.arg ? ` ${command.arg}` : ''}${aliasNote}`;
    if (!groups.has(command.group)) groups.set(command.group, []);
    groups.get(command.group)!.push({ label, desc: command.desc });
  }
  return order.filter((group) => groups.has(group)).map((group) => ({ group, rows: groups.get(group)! }));
}
