// A hand-off is a letter and its addressee. The addressee (`to`) is the one thing the host
// routes by, and the backend CLI holds the model's final answer to it (Codex --output-schema,
// Claude --json-schema), or, with no such answer, takes the hand-off as a tool whose call ends
// the turn (Copilot, Pi), so a turn cannot end in prose that only reads like a hand-off. The
// letter is free text: it reaches the addressee as written, and the operator reads every one.
// A letter that hands over a result names its files, which the page opens; a task the Planner
// opens names the tasks it waits for, which the host holds it back for.

import { isJsonObject, schemaErrors, type PayloadSchema } from '@argus/contracts';
import type { AgentRequest, Form, HandOff } from '../types.js';

const STUCK = 'the work is stuck and needs the Manager to get it going';
const OPERATOR = 'only for what the operator alone can decide: the letter is your question, and you wait for the answer while the rest of the work goes on';

/** Whom each role can write to, and what such a letter is for. Every addressee is someone who
 * acts on it: the work is never sent to a state such as done. */
const TO: Record<Form, Record<string, string>> = {
  manager: {
    engineer: 'the work, whole, when one person can carry it through; it opens a task, named by the letter\'s first line, a short title, and the letter is its whole brief',
    planner: 'work of more than one task, such as separate things asked for or parts that can go side by side, for the Planner to divide; or word on the work it runs. The letter is its whole brief',
    operator: 'your answer, report or question',
  },
  plan: {
    engineer: 'the next tasks, a letter each: each opens a task, a result for an Engineer to carry through, named by its letter\'s first line, a short title, and its letter is its whole brief; each starts once the tasks it waits for have closed, and those that wait for none run side by side at once',
    planner: 'nothing to open or tell now: the tasks under way go on, and you read on when the next letter comes',
    manager: `the operator's goal is met, for the Manager to report; or ${STUCK}`,
    operator: OPERATOR,
  },
  engineer: {
    reviewer: 'the work is ready for the independent review (do not start one yourself or through a subagent)',
    engineer: 'you keep working next turn',
    planner: 'a task the Planner gave cannot go on as briefed',
    manager: `a task the Manager gave cannot go on as briefed; or ${STUCK}`,
    operator: OPERATOR,
  },
  review: {
    engineer: 'what to repair before the operator sees it, named in the letter',
    planner: 'your judgment on a task the Planner gave: accepted, or what is wrong',
    manager: `your judgment on a task the Manager gave: accepted, or what is wrong; or ${STUCK}`,
    operator: OPERATOR,
  },
};

const LETTER = 'Your letter, passed on as written: its reader sees the workspace and your letter, not your conversation.';
const LETTERS = 'Your letters, each passed on as written: to the Engineer, one for each task; to anyone else, one. Each reader sees the workspace and its letter, not your conversation.';
const AFTER = 'The tasks this one waits for, by id: an open or closed one, or one of these letters\' tasks above it, which take the next free ids in order. It starts once they have all closed. Empty when it can start now, and in a letter to anyone else.';
const TASK = 'The open task this letter is for, by id, when it is about that task\'s own work: whoever holds it reads the letter as soon as they can, in the turn under way or their next, and no task is opened. Empty for a letter that opens a task, such as work that can go on beside it, and in a letter to anyone else.';
// The result the operator opens: shown beside the letter as written, without a check.
const OPEN = 'What the operator opens to see the result your letter hands over, if it hands one over: the paths of its files, relative to the workspace, the one to open first first. Empty when it hands over none.';

export function formSchema(form: Form, giver?: AgentRequest['giver']): PayloadSchema {
  // The Planner hears only of the tasks it gave: it never had the Manager's.
  const to = Object.fromEntries(Object.entries(TO[form]).filter(([place]) => place !== 'planner' || giver !== 'manager'));
  const meanings = Object.entries(to).map(([place, meaning]) => `${place}, ${meaning}`).join('; ');
  const letter: PayloadSchema = { type: 'string', minLength: 1, maxLength: 20000 };
  const strings = (description: string): PayloadSchema => ({ type: 'array', items: { type: 'string', minLength: 1 }, description });
  // The Planner may open several tasks at once, each waiting for those it names; every other turn
  // ends in one letter, and may hand over a result.
  const fields: Record<string, PayloadSchema> = form === 'plan' ? {
    letters: {
      type: 'array', minItems: 1, description: LETTERS, items: {
        type: 'object', additionalProperties: false, required: ['letter', 'after', 'task'],
        properties: { letter, after: strings(AFTER), task: { type: 'string', description: TASK } },
      },
    },
  } : { letter: { ...letter, description: LETTER }, open: strings(OPEN),
    ...form === 'manager' ? { task: { type: 'string', description: TASK } } : {} };
  return {
    type: 'object', description: 'Your hand-off: the final answer of your turn.', additionalProperties: false,
    properties: { to: { type: 'string', enum: Object.keys(to), description: `Who holds the work next: ${meanings}.` }, ...fields },
    required: ['to', ...Object.keys(fields)],
  };
}

/** The form as a tool, for a backend whose turn ends with a tool call; its arguments are the form. */
export const handOffTool = (schema: PayloadSchema) => ({
  name: 'hand_off', description: `${schema.description} Call it last: the call ends your turn.`, parameters: schema,
});

/** The hand-off a turn's final answer records, or the reason Argus refuses it. */
export function judge(form: Form, schema: PayloadSchema, payload: unknown): string | HandOff {
  if (payload === undefined) return 'The turn ended without the hand-off form.';
  const errors = schemaErrors(payload, schema);
  if (errors.length || !isJsonObject(payload)) return errors.join('; ');
  const items = Array.isArray(payload.letters) ? payload.letters.filter(isJsonObject) : [payload];
  const tasks = items.map(item => ({ task: String(item.task ?? '').trim(), after: Array.isArray(item.after) ? item.after.map(String) : [] }));
  const open = Array.isArray(payload.open) ? payload.open.map(String) : [];
  const to = String(payload.to);
  // Only letters to the Engineer are for tasks: one to anyone else opens none and is for none.
  return {
    form, to, letters: items.map(item => String(item.letter).trim()),
    ...to === 'engineer' && tasks.some(({ task, after }) => task || after.length) ? { tasks } : {}, ...open.length ? { open } : {},
  };
}
