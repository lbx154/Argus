// Each role's prompt: its rules (prompts/<role>.md) first, the same for
// every turn, then what this turn is about. A resumed session already holds the rules, so it is
// sent only what is new. What each hand-off field means is in the form's own schema, not here.

import { existsSync, mkdirSync, readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Role } from './types.js';
import type { Note, Project, Store, Task } from './store.js';

export const NAMES: Record<Note['from'], string> = {
  manager: 'Manager', planner: 'Planner', engineer: 'Engineer', reviewer: 'Reviewer', operator: 'operator',
};

/** The skill library: a folder each, whose README.md says in its first line what the skill is for.
 * Argus's own ship in the package, and an upgrade replaces them; the roles and the operator keep
 * theirs under the state root's skills/, where one takes the place of Argus's of the same name. */
export const SKILLS = fileURLToPath(new URL('../skills', import.meta.url));
/** The state root's skills/, made when missing so that a role can write there. */
export function skillsHome(home: string): string {
  const dir = resolve(home, 'skills');
  mkdirSync(dir, { recursive: true });
  return dir;
}
/** Each skill's folder, by its name. */
export const skills = (home: string): Map<string, string> => new Map([SKILLS, skillsHome(home)].flatMap(root => readdirSync(root)
  .map(name => [name, resolve(root, name)] as const).filter(([, dir]) => existsSync(resolve(dir, 'README.md')))));
/** What a skill is for: the first line of its README. */
export const purpose = (dir: string): string => readFileSync(resolve(dir, 'README.md'), 'utf8').split('\n')[0]!.replace(/^#\s*/, '');

const rules = (role: Role): string => readFileSync(new URL(`../prompts/${role}.md`, import.meta.url), 'utf8').trim();
const section = (title: string, body: string): string => `## ${title}\n${body}`;
const join = (...parts: (string | false | null)[]): string => parts.filter(Boolean).join('\n\n');

/** What every role is told once: where the work, Argus's record of it and the skills are, and the date. */
function where(project: Project, store: Store): string {
  const library = skillsHome(store.home);
  return section('Where things are', [
    `Workspace: ${project.workdir}`,
    `Argus's record of this project: ${store.dir(project.id)} (project.json holds the tasks and their state; events.jsonl every letter)`,
    `Skills, a folder each whose README.md says in its first line what it is for (one in ${library} takes the place of Argus's own of the same name):`,
    ...[...skills(store.home)].map(([name, dir]) => `- ${name}: ${purpose(dir)} (${resolve(dir, 'README.md')})`),
    `The skills are yours: when your work teaches you something later projects can use, write a new skill in ${library} or improve one there.`,
    `Today is ${new Date().toISOString().slice(0, 10)} (UTC). Your training knowledge may predate it: call nothing the latest unless a source checked now says so.`,
  ].join('\n'));
}

export function managerPrompt(project: Project, store: Store, message: string, resumed: boolean): string {
  if (resumed) return message;
  return join(rules('manager'), where(project, store), section('Message', message));
}

/** The Engineer's or Reviewer's turn on `task`, whichever holds it. */
export function taskPrompt(project: Project, store: Store, task: Task, resumed: boolean): string {
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
  return join(rules(task.holder), where(project, store), earlier.length > 0 && section('Earlier tasks', earlier.join('\n')),
    beside.length > 0 && section('Running beside it, in the same workspace', beside.join('\n')),
    section(`Task ${task.id}, from the ${NAMES[task.from]}`, task.objective), note);
}

export function plannerPrompt(project: Project, store: Store, resumed: boolean): string {
  const tasks = project.tasks.map(task => `- ${task.id} [${task.status === 'open' ? `open, with the ${NAMES[task.holder]}` : 'closed'}] ${task.title}`);
  return join(!resumed && rules('planner'), !resumed && where(project, store),
    section('Tasks', tasks.join('\n') || '(none yet)'), section('Since your last turn', project.inbox.join('\n\n')));
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
