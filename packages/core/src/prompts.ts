// Each role's prompt: its rules (prompts/<role>.md) first, the same for
// every turn, then what this turn is about. A resumed session already holds the rules, so it is
// sent only what is new. What each hand-off field means is in the form's own schema, not here.

import { existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Role } from './types.js';
import type { Note, Project, Store, Task } from './store.js';

export const NAMES: Record<Note['from'], string> = {
  manager: 'Manager', planner: 'Planner', engineer: 'Engineer', reviewer: 'Reviewer', operator: 'operator',
};

/** The skill library: a folder each, whose SKILL.md says in its front matter what the skill is for,
 * as Codex, Claude Code and others write a skill. Argus's own ship in the package, and an upgrade
 * replaces them; the roles and the operator keep theirs under the state root's skills/, where one
 * takes the place of Argus's of the same name. */
export const SKILLS = fileURLToPath(new URL('../skills', import.meta.url));
export const SKILL = 'SKILL.md';
/** The state root's skills/, made when missing so that a role can write there. */
export function skillsHome(home: string): string {
  const dir = resolve(home, 'skills');
  mkdirSync(dir, { recursive: true });
  return dir;
}
/** Each skill's folder, by its name: a directory of its own holding a SKILL.md of its own, never
 * a link, so that what a role writes there cannot bring in a file from elsewhere. */
export const skills = (home: string): Map<string, string> => new Map([SKILLS, skillsHome(home)].flatMap(root => readdirSync(root)
  .map(name => [name, resolve(root, name)] as const)
  .filter(([, dir]) => lstatSync(dir).isDirectory() && lstatSync(resolve(dir, SKILL), { throwIfNoEntry: false })?.isFile())));
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

const rules = (role: Role): string => readFileSync(new URL(`../prompts/${role}.md`, import.meta.url), 'utf8').trim();
const section = (title: string, body: string): string => `## ${title}\n${body}`;
const join = (...parts: (string | false | null)[]): string => parts.filter(Boolean).join('\n\n');

/** A role's standing instructions: its rules, and where the work, Argus's record of it and the
 * skills are, and the date. */
export const instructions = (role: Role, project: Project, store: Store): string => join(rules(role), where(project, store));

function where(project: Project, store: Store): string {
  const library = skillsHome(store.home);
  return section('Where things are', [
    `Workspace: ${project.workdir}`,
    `Argus's record of this project: ${store.dir(project.id)} (project.json holds the tasks and their state; events.jsonl every letter)`,
    `Skills, a folder each whose SKILL.md says in its front matter what it is for (one in ${library} takes the place of Argus's own of the same name):`,
    ...[...skills(store.home)].map(([name, dir]) => `- ${name}: ${purpose(dir)} (${resolve(dir, SKILL)})`),
    `The skills are yours: when your work teaches you something later projects can use, write a new skill in ${library} or improve one there.`,
    `Today is ${new Date().toISOString().slice(0, 10)} (UTC). Your training knowledge may predate it: call nothing the latest unless a source checked now says so.`,
  ].join('\n'));
}

export const managerPrompt = (message: string, resumed: boolean): string => resumed ? message : section('Message', message);

/** The Engineer's or Reviewer's turn on `task`, whichever holds it. */
export function taskPrompt(project: Project, task: Task, resumed: boolean): string {
  const note = task.note && section(`From the ${NAMES[task.note.from]}`, task.note.text);
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
