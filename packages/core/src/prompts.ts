// Each role's prompt: its rules (prompts/<role>.md) first, the same for
// every turn, then what this turn is about. A resumed session already holds the rules, so it is
// sent only what is new. What each hand-off field means is in the form's own schema, not here.

import { lstatSync, mkdirSync, readdirSync, readFileSync, statfsSync, writeFileSync } from 'node:fs';
import { cpus, loadavg, totalmem } from 'node:os';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { KEPT, type Role } from './types.js';
import type { Note, Project, Store, Task } from './store.js';
import { isCheckout, WORKTREES } from './worktrees.js';
import { lettersOn } from './letters.js';
import { packSetups } from './packSetup.js';

export const NAMES: Record<Note['from'], string> = {
  manager: 'Manager', planner: 'Planner', engineer: 'Engineer', reviewer: 'Reviewer', operator: 'operator',
};

/** The skill library: a folder each, whose SKILL.md says in its front matter what the skill is for,
 * as Codex, Claude Code and others write a skill. Argus's own ship in the package, and an upgrade
 * replaces them; installed packs are read-only beside them. The roles and the operator keep theirs under the state root's skills/, listed
 * beside Argus's, a name shared or not. */
export const SKILLS = fileURLToPath(new URL('../skills', import.meta.url));
export const SKILL = 'SKILL.md';
/** Where the team's skills and wiki live: $ARGUS_SHARED, so that state roots that never meet (an
 * experiment each, say) learn together; unset, the state root itself. */
export const sharedHome = (home: string): string => resolve(process.env.ARGUS_SHARED || home);
/** The team's skills/, made when missing so that a role can write there. */
export function skillsHome(home: string): string {
  const dir = resolve(sharedHome(home), 'skills');
  mkdirSync(dir, { recursive: true });
  return dir;
}
/** The team's tools/: commands any role runs by name, on the PATH of every call. */
export function toolsHome(home: string): string {
  const dir = resolve(sharedHome(home), 'tools');
  mkdirSync(dir, { recursive: true });
  return dir;
}
/** Each team tool's name and what it is for: an executable regular file (never a link), described by
 * the first comment after its shebang. */
export const tools = (home: string): (readonly [string, string])[] => readdirSync(toolsHome(home)).sort().flatMap(name => {
  const file = resolve(toolsHome(home), name);
  const stat = lstatSync(file);
  if (!stat.isFile() || !(stat.mode & 0o111)) return [];
  const head = readFileSync(file, 'utf8').slice(0, 4096).split('\n').slice(0, 6);
  const said = head.map(line => /^\s*(?:#|\/\/)\s*(?!!)(.+)$/.exec(line)?.[1]?.trim()).find(Boolean) ?? '';
  return [[name, said] as const];
});
export const WIKI_INDEX = 'INDEX.md';
/** The wiki's pages as a reader meets them: each with its length, which no one sees from its name. */
function pages(home: string): string {
  const dir = wikiHome(home);
  const found = readdirSync(dir).filter(name => name.endsWith('.md') && name !== WIKI_INDEX).sort()
    .map(name => `${name} (${readFileSync(resolve(dir, name), 'utf8').split('\n').length} lines)`);
  return found.length ? ` Its pages: ${found.join(', ')}.` : '';
}
/** The team's wiki/: what earlier work learned that no script runs, a page a topic, under one index. */
export function wikiHome(home: string): string {
  const dir = resolve(sharedHome(home), 'wiki');
  mkdirSync(dir, { recursive: true });
  const index = resolve(dir, WIKI_INDEX);
  if (!lstatSync(index, { throwIfNoEntry: false })) writeFileSync(index, '# Wiki\n\nWhat earlier work learned that a later task would need and no script can run: one page a topic, each listed here in one line.\n');
  return dir;
}
/** The state root's skill-packs/, where the operator installs packs; made when missing. */
export function packsHome(home: string): string {
  const dir = resolve(home, 'skill-packs');
  mkdirSync(dir, { recursive: true });
  return dir;
}
/** Each skill's name and folder, Argus's first: a directory of its own holding a SKILL.md of its
 * own, never a link, so that what a role writes there cannot bring in a file from elsewhere. */
export const skills = (home: string): (readonly [string, string])[] => [SKILLS, packsHome(home), skillsHome(home)].flatMap(root => readdirSync(root)
  .map(name => [name, resolve(root, name)] as const)
  .filter(([, dir]) => lstatSync(dir).isDirectory() && lstatSync(resolve(dir, SKILL), { throwIfNoEntry: false })?.isFile()));
/** What a skill is for: the description in its SKILL.md's front matter, plain or quoted. */
export function purpose(dir: string, key = 'description'): string {
  const matter = /^---\n([\s\S]*?)\n---/.exec(readFileSync(resolve(dir, SKILL), 'utf8'))?.[1] ?? '';
  const said = new RegExp(`^${key}:[ \t]*(.*)$`, 'm').exec(matter)?.[1]?.trim() ?? '';
  try { if (said.startsWith('"')) return String(JSON.parse(said)); } catch { /* as written */ }
  return said.replace(/^'(.*)'$/, '$1');
}

const rules = (name: Role): string => readFileSync(new URL(`../prompts/${name}.md`, import.meta.url), 'utf8').trim();
const section = (title: string, body: string): string => `## ${title}\n${body}`;
const join = (...parts: (string | false | null)[]): string => parts.filter(Boolean).join('\n\n');

/** A role's standing instructions and the work's locations, session lifetime, date and machine. */
export const instructions = (role: Role, project: Project, store: Store): string =>
  join(rules(role), where(project, store, role));

function where(project: Project, store: Store, role: Role): string {
  const library = skillsHome(store.home);
  return section('Where things are', [
    `Workspace: ${project.workdir}`,
    `Argus's record of this project: ${store.dir(project.id)} (PROGRESS.md where the work stands, rebuilt from the rest; project.json holds the tasks and their state; events.jsonl every letter)`,
    project.chief && `You work for the operator across all their projects. Every project's record is in ${resolve(store.home, 'projects')}/<id>/, this one's among them: read them; only a project's own team changes it. The operator knows them by name:`,
    ...project.chief ? store.list().filter(other => other.id !== project.id).map(other => `- ${other.id}: ${other.name || other.request.split('\n')[0]!.slice(0, 80) || '(no request yet)'}`) : [],
    project.repeat && `Each day at ${project.repeat.at} UTC Argus sends this project the operator's message: ${project.repeat.text}`,
    project.every && `Every ${project.every.hours} hours (counted from midnight UTC) Argus sends this project the operator's message: ${project.every.text}`,
    project.onRest && `When another project's work comes to rest, Argus sends you the operator's message, with that project and how it rests: ${project.onRest}`,
    role === 'planner' && isCheckout(project.workdir) && `A task that begins while another task works in the workspace works in a git worktree of its own, under ${WORKTREES}/, so tasks side by side do not overwrite each other's files.`,
    `Skills (Argus's own, those installed in ${packsHome(store.home)}, then the team's in ${library}):`,
    ...skills(store.home).map(([name, dir]) => `- ${name}: ${purpose(dir)} (${resolve(dir, SKILL)})`),
    `The team's tools, commands on your PATH (${toolsHome(store.home)}):`,
    ...tools(store.home).map(([name, said]) => `- ${name}${said ? `: ${said}` : ''}`),
    `The team's wiki, what earlier work in every project learned that no script runs (facts, pitfalls, routes that failed and why): ${resolve(wikiHome(store.home), WIKI_INDEX)}. Read the index, and the pages on what your work touches; the learning skill says how to add to it.${pages(store.home)}`,
    KEPT.includes(project.backend)
      ? `Your session stays open between your turns: what you start in the background runs on after your turn ends, till your task closes (the Manager's and the Planner's, till the project's work comes to rest).${role === 'reviewer' ? ' On a task you are one Reviewer from its first look to its last review: what you saw and said before is yours to build on.' : ''}`
      : 'Your session ends with each turn, and everything you started with it.',
    lettersOn() && letters(role),
    ...packSetups(store.home).map(pack => pack.state === 'ready'
      ? `The ${pack.name} pack's tools are installed in ${pack.home}; what its setup set for your commands is in ${resolve(pack.home, 'env')} (already in your environment).`
      : `The ${pack.name} pack's setup ${pack.state === 'failed' ? 'failed' : 'has not finished'}: its tools are not installed. Its output: ${pack.log}.`),
    `Today is ${new Date().toISOString().slice(0, 10)} (UTC).`,
    machine(project.workdir),
  ].filter(Boolean).join('\n'));
}

/** How a role writes to others while its turn goes on. */
const letters = (role: Role): string => [
  'You can write to others while your turn goes on, without ending it; they have the letter as they work, and you go on:',
  '  curl -s --data-binary @- "$ARGUS_LETTERS?to=READERS&cc=READERS" <<\'EOF\'',
  '  (the letter)',
  '  EOF',
  `Readers, comma-separated: manager, planner, an open task by its id (its holder reads it)${role === 'engineer'
    ? ', and reviewer: the Reviewer of your task looks now at what your letter asks, beside your work, and writes back into your turn'
    : role === 'reviewer' ? ', and engineer, while the Engineer of your task works on (when you look at their work under way)' : ''}. cc sends a copy, marked as one, the same way. Your turn still ends with your hand-off.`,
].join('\n');

/** The machine the work runs on, as far as Argus knows it: a sandboxed role sees neither the
 * workspace's disk nor the team's processes, so the disk is said here. */
function machine(workdir: string): string {
  const count = cpus().length;
  const load = loadavg()[0]!;
  const gpus = process.env.CUDA_VISIBLE_DEVICES;
  const gib = (blocks: number, size: number): number => Math.round(blocks * size / 2 ** 30);
  let disk = '';
  try { const { bavail, blocks, bsize } = statfsSync(workdir); disk = `; the workspace's disk: ${gib(bavail, bsize)} GiB free of ${gib(blocks, bsize)}`; } catch { /* no workspace yet */ }
  return `${count} logical CPUs (load ${load.toFixed(1)}, about ${Math.max(0, count - load).toFixed(0)} free), ${Math.round(totalmem() / 2 ** 30)} GiB of memory${disk}; project GPUs: CUDA_VISIBLE_DEVICES=${(gpus ?? '(unset)') || '(none)'}.`;
}

export const managerPrompt = (message: string, resumed: boolean): string => resumed ? message : section('Message', message);

/** A turn on the operator's draft alone, whose letter is the draft rewritten. */
export const rewritePrompt = (draft: string): string => join(
  "Rewrite the operator's draft below as one clear paragraph the team can carry out as it stands. Keep the operator's language and intent, and add no requirement they did not state. Your letter, to the operator, is the rewritten paragraph alone: they read it, and send it as their own.",
  section('Draft', draft));

/** A letter to a task that is not open, or closed before its holder read it, back to its writer. */
export const unread = (task: string, letter: string): string => `Your letter to task ${task}, which is not open, was not read:\n${letter}`;

/** Standing instructions changed since a resumed session was given them, ahead of its prompt. */
export const reinstructed = (instructions: string, prompt: string): string =>
  join(section('Your standing instructions, as they now are (they replace those you were given before)', instructions), prompt);

/** A letter put into a turn under way. */
/** A letter to the Manager that came while its turn goes on: its letter, when it ends the turn, answers this one too. */
export const steeredPrompt = (text: string): string => section('A letter that came during your turn; your letter answers it too', text);

export const letterPrompt = (from: Note['from'], text: string): string => section(`From the ${NAMES[from]}, during your turn`, text);

/** The Engineer's or Reviewer's turn on `task`, whichever holds it. */
export function taskPrompt(project: Project, task: Task, resumed: boolean): string {
  const note = join(task.note && section(`From the ${NAMES[task.note.from]}`, task.note.text),
    ...(task.unread ?? []).map(({ from, text }) => section(`From the ${NAMES[from]}, since the task's last turn`, text)));
  const frozen = task.holder === 'reviewer' && task.frozen?.last ? section('The candidate, frozen by Argus',
    `Commit ${task.frozen.last} holds the task's files as the Engineer handed them over, uncommitted and new ones included. What changed since the task began: \`git diff ${task.frozen.start} ${task.frozen.last}\`${task.frozen.before && task.frozen.before !== task.frozen.start ? `; since the candidate before: \`git diff ${task.frozen.before} ${task.frozen.last}\`` : ''}. The Engineer's letter guides the reading; the change is the evidence.`) : '';
  if (resumed) return join(note, frozen) || 'Continue the task.';
  // How each earlier task ended is in Argus's record, which the workspace may not say.
  const earlier = project.tasks.filter(other => other.status === 'closed')
    .map(({ id, title, note }) => `- ${id} ${title}${note ? `. Its last word, from the ${NAMES[note.from]}: ${note.text.split('\n')[0]}` : ''}`);
  const beside = project.tasks.filter(other => other.status === 'open' && other !== task).map(({ id, title }) => `- ${id} ${title}`);
  const own = typeof task.place === 'object' ? task.place : null;
  const inWorkspace = project.tasks.filter(other => other.status === 'open' && other !== task && other.place === 'workspace').map(({ id }) => id);
  return join(earlier.length > 0 && section('Earlier tasks', earlier.join('\n')),
    beside.length > 0 && section(own ? 'Running beside it' : 'Running beside it, in the same workspace', beside.join('\n')),
    own && section('Where this task works', `In ${own.dir}, a git worktree of the workspace made from it as it was when the task began (commit ${own.base}, uncommitted and new files included), because ${inWorkspace.join(', ') || 'another task'} works in the workspace itself. The task's work counts once it is in the workspace, ${project.workdir}: \`git diff ${own.base}\` and \`git status\` in the worktree show what it changed, and settling a conflict with what changed in the workspace meanwhile is part of the task. Whether to bring its changes back to the workspace, and whether to keep the worktree or delete it (\`git worktree remove\`), are the task's to decide.`),
    section(`Task ${task.id}, from the ${NAMES[task.from]}`, task.objective), note, frozen);
}

export function plannerPrompt(project: Project): string {
  const open = new Set(project.tasks.filter(task => task.status === 'open').map(task => task.id));
  const state = (task: Task): string => {
    if (task.status === 'closed') return 'closed';
    const waits = (task.after ?? []).filter(id => open.has(id));
    return waits.length ? `open, waits for ${waits.join(', ')}` : `open, with the ${NAMES[task.holder]}`;
  };
  const tasks = project.tasks.map(task => `- ${task.id} [${state(task)}] ${task.title}`);
  return join(section('Tasks', [...tasks, `The next task opened is t${project.tasks.length + 1}.`].join('\n')),
    !!project.beside?.length && section('Hand-overs inside your tasks since your last turn', project.beside.join('\n\n')),
    section('Since your last turn', project.inbox.join('\n\n')));
}

/** A letter that reaches the Planner or the Manager from another role. */
export function handedTo(role: Role, task: Task | null, letter: string): string {
  return `From the ${NAMES[role]}${task ? `, on task ${task.id} (${task.title})` : ''}:\n${letter}`;
}

/** A turn that failed beside work that goes on, for the Manager to tell the operator. */
export const heldLetter = (role: Role, task: Task | null, error: string): string =>
  `From Argus: the ${NAMES[role]}'s turn${task ? ` on task ${task.id} (${task.title})` : ''} failed and did not go on by itself. It waits, with whatever waits for it; the rest of the work goes on. The error: ${error}\nTell the operator in plain words what is held up; any word from them sends it on.`;

/** The turn that takes over from a session that could not go on. */
export const startedAfresh = (error: string, events: string, prompt: string): string =>
  `${prompt}\n\n${section('You are taking over', `The session that had this work failed twice and could not go on, so this is a new one, without its memory. Its failure: ${error}\nWhat it did is in the working folder; each step it took is in ${events}. Look at what is there before doing anything again. If the failure objects to something in how the work was going, go about that part another way.`)}`;

/** A hand-over inside a task, which the Planner reads as the work goes, to steer it or the tasks beside it. */
export function copied(from: Role, to: Role, task: Task, letter: string): string {
  return `From the ${NAMES[from]} to the ${NAMES[to]}, on task ${task.id} (${task.title}), as the work goes:\n${letter}`;
}

/** A letter written during a turn, as its readers have it: who wrote it, on which task, to whom, copied to whom. */
export function posted(from: Role, task: Task | null, to: string[], cc: string[], text: string): string {
  const named = (names: string[]) => names.map(name => /^t\d+$/.test(name) ? `task ${name}` : `the ${NAMES[name as Role] ?? name}`).join(', ');
  const on = task ? ` on task ${task.id} (${task.title})` : '';
  return `From the ${NAMES[from]}${on}, as their turn goes on, to ${to.length ? named(to) : '(copies only)'}${cc.length ? `; copied to ${named(cc)}` : ''}:\n${text}`;
}

/** A Reviewer's look at work under way, which the Engineer asked for while going on. */
export function lookPrompt(project: Project, task: Task, look: string, resumed = false): string {
  return join(!resumed && taskPrompt(project, task, false),
    section('The Engineer asks you to look now, while they go on working', look),
    'Your letter reaches the Engineer as they work: what you see that they should know now. It is not your review of their delivery, which comes when they hand it over.');
}

export function refusedPrompt(reason: string): string {
  return `Argus refused your hand-off: ${reason}\nEnd your turn with the form again, corrected.`;
}

export function answered(question: string, answer: string): string {
  return `You asked the operator: ${question}\nThey answered: ${answer}`;
}
