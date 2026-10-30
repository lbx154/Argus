// Each role's prompt: its rules (prompts/<role>.md) first, the same for
// every turn, then what this turn is about. A resumed session already holds the rules, so it is
// sent only what is new. What each hand-off field means is in the form's own schema, not here.

import { existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { cpus, totalmem } from 'node:os';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { KEPT, type Role } from './types.js';
import type { Note, Project, Store, Task } from './store.js';

export const NAMES: Record<Note['from'], string> = {
  manager: 'Manager', planner: 'Planner', engineer: 'Engineer', reviewer: 'Reviewer', operator: 'operator',
};

/** The skill library: a folder each, whose SKILL.md says in its front matter what the skill is for,
 * as Codex, Claude Code and others write a skill. Argus's own ship in the package, and an upgrade
 * replaces them; the roles and the operator keep theirs under the state root's skills/, listed
 * beside Argus's, a name shared or not. */
export const SKILLS = fileURLToPath(new URL('../skills', import.meta.url));
export const SKILL = 'SKILL.md';
/** The state root's skills/, made when missing so that a role can write there. */
export function skillsHome(home: string): string {
  const dir = resolve(home, 'skills');
  mkdirSync(dir, { recursive: true });
  return dir;
}
/** Each skill's name and folder, Argus's first: a directory of its own holding a SKILL.md of its
 * own, never a link, so that what a role writes there cannot bring in a file from elsewhere. */
export const skills = (home: string): (readonly [string, string])[] => [SKILLS, skillsHome(home)].flatMap(root => readdirSync(root)
  .map(name => [name, resolve(root, name)] as const)
  .filter(([, dir]) => lstatSync(dir).isDirectory() && lstatSync(resolve(dir, SKILL), { throwIfNoEntry: false })?.isFile()));
/** What a skill is for: the description in its SKILL.md's front matter, plain or quoted. */
export function purpose(dir: string): string {
  const matter = /^---\n([\s\S]*?)\n---/.exec(readFileSync(resolve(dir, SKILL), 'utf8'))?.[1] ?? '';
  const said = /^description:[ \t]*(.*)$/m.exec(matter)?.[1]?.trim() ?? '';
  try { if (said.startsWith('"')) return String(JSON.parse(said)); } catch { /* as written */ }
  return said.replace(/^'(.*)'$/, '$1');
}

/** From before skills were SKILL.md: a folder of the state root's skills/ whose README.md said in its
 * first line what it was for becomes one, once. To go in a later version. */
export function renameSkills(home: string): void {
  const root = skillsHome(home);
  for (const name of readdirSync(root)) {
    const dir = resolve(root, name);
    const old = resolve(dir, 'README.md');
    if (!lstatSync(dir).isDirectory() || existsSync(resolve(dir, SKILL)) || !lstatSync(old, { throwIfNoEntry: false })?.isFile()) continue;
    const [first = '', ...rest] = readFileSync(old, 'utf8').split('\n');
    writeFileSync(resolve(dir, SKILL), `---\nname: ${name}\ndescription: ${JSON.stringify(first.replace(/^#\s*/, ''))}\n---\n\n${rest.join('\n').replace(/^\n+/, '')}`);
    unlinkSync(old);
  }
}

const rules = (name: Role | 'writing'): string => readFileSync(new URL(`../prompts/${name}.md`, import.meta.url), 'utf8').trim();
const section = (title: string, body: string): string => `## ${title}\n${body}`;
const join = (...parts: (string | false | null)[]): string => parts.filter(Boolean).join('\n\n');

/** A role's standing instructions: its rules, how every role writes, and where the work, Argus's
 * record of it and the skills are, the date and the machine. A role that writes may write skills. */
export const instructions = (role: Role, writes: boolean, project: Project, store: Store): string =>
  join(rules(role), rules('writing'), where(project, store, writes));

function where(project: Project, store: Store, writes: boolean): string {
  const library = skillsHome(store.home);
  return section('Where things are', [
    `Workspace: ${project.workdir}`,
    `Argus's record of this project: ${store.dir(project.id)} (project.json holds the tasks and their state; events.jsonl every letter)`,
    `Skills, a folder each whose SKILL.md says in its front matter what it is for: when one fits your work, read its SKILL.md whole, once, and the files it links as you need them. Argus's own, then the team's in ${library}:`,
    ...skills(store.home).map(([name, dir]) => `- ${name}: ${purpose(dir)} (${resolve(dir, SKILL)})`),
    writes && `The skills in ${library} are the team's: when your work teaches how later projects can do theirs better, write it there, in a new skill or one of the team's. A fault in Argus itself is no skill: tell the operator.`,
    KEPT.includes(project.backend)
      ? 'Your session stays open between your turns: what you start in the background runs on after your turn ends, till your task closes (the Manager\'s and the Planner\'s, till the project\'s work comes to rest).'
      : 'Your session ends with each turn, and everything you started with it.',
    `Today is ${new Date().toISOString().slice(0, 10)} (UTC). Your training knowledge may predate it: call nothing the latest unless a source checked now says so.`,
    'Keep in the workspace what the work makes, for the other roles and the operator to see; install a tool you only check with (a browser, say) under ~/.cache, which every role and project shares. Your /tmp may be yours alone, and a path elsewhere may be hidden from you: when the operator names a file you cannot read, ask them to attach it, or for a folder that keeps changing, to start the project in a folder that holds it. What only the operator can do on their own machine, such as installing a scheduled job, give them as the commands to type.',
    machine(),
  ].filter(Boolean).join('\n'));
}

/** The machine the work runs on, as far as Argus knows it. */
function machine(): string {
  const gpus = process.env.CUDA_VISIBLE_DEVICES;
  return `The machine: ${cpus().length} logical CPUs, ${Math.round(totalmem() / 2 ** 30)} GiB of memory${gpus === undefined ? ''
    : `, and the GPUs this project may use, CUDA_VISIBLE_DEVICES=${gpus || '(none)'}`}.`;
}

export const managerPrompt = (message: string, resumed: boolean): string => resumed ? message : section('Message', message);

/** A turn on the operator's draft alone, whose letter is the draft rewritten. */
export const rewritePrompt = (draft: string): string => join(
  "Rewrite the operator's draft below as one clear paragraph the team can carry out as it stands. Keep the operator's language and intent, and add no requirement they did not state. Your letter, to the operator, is the rewritten paragraph alone: they read it, and send it as their own.",
  section('Draft', draft));

/** A letter to a task that is not open, or closed before its holder read it, back to its writer. */
export const unread = (task: string, letter: string): string => `Your letter to task ${task}, which is not open, was not read:\n${letter}`;

/** A letter put into a turn under way. */
export const letterPrompt = (from: Note['from'], text: string): string => section(`From the ${NAMES[from]}, during your turn`, text);

/** The Engineer's or Reviewer's turn on `task`, whichever holds it. */
export function taskPrompt(project: Project, task: Task, resumed: boolean): string {
  const note = join(task.note && section(`From the ${NAMES[task.note.from]}`, task.note.text),
    ...(task.unread ?? []).map(({ from, text }) => section(`From the ${NAMES[from]}, since the task's last turn`, text)));
  if (resumed) {
    const again = task.holder === 'reviewer' && task.note?.from === 'engineer'
      && 'The Engineer has worked on the task since your last judgment. Judge the current files afresh: your earlier judgment is not a fact.';
    return join(again, note || 'Continue the task.');
  }
  // How each earlier task ended is in Argus's record, which the workspace may not say.
  const earlier = project.tasks.filter(other => other.status === 'closed')
    .map(({ id, title, note }) => `- ${id} ${title}${note ? `. Its last word, from the ${NAMES[note.from]}: ${note.text.split('\n')[0]}` : ''}`);
  const beside = project.tasks.filter(other => other.status === 'open' && other !== task).map(({ id, title }) => `- ${id} ${title}`);
  return join(earlier.length > 0 && section('Earlier tasks', earlier.join('\n')),
    beside.length > 0 && section('Running beside it, in the same workspace', beside.join('\n')),
    section(`Task ${task.id}, from the ${NAMES[task.from]}`, task.objective), note);
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
    section('Since your last turn', project.inbox.join('\n\n')));
}

/** A letter that reaches the Planner or the Manager from another role. */
export function handedTo(role: Role, task: Task | null, letter: string): string {
  return `From the ${NAMES[role]}${task ? `, on task ${task.id} (${task.title})` : ''}:\n${letter}`;
}

export function refusedPrompt(reason: string): string {
  return `Argus refused your hand-off: ${reason}\nEnd your turn with the form again, corrected.`;
}

export function answered(question: string, answer: string): string {
  return `You asked the operator: ${question}\nThey answered: ${answer}`;
}
