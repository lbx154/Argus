// A hand-off is a letter and its addressee. The addressee (`to`) is the one thing the host
// routes by, and the backend CLI holds the model's final answer to it (Codex --output-schema,
// Claude --json-schema), or, with no such answer, takes the hand-off as a tool whose call ends
// the turn (Copilot, Pi), so a turn cannot end in prose that only reads like a hand-off. The
// letter is free text: it reaches the addressee as written, and the operator reads every one.
// A letter that hands over a result names its files, which the page opens; a task the Planner
// opens names the tasks it waits for, which the host holds it back for.

import { isJsonObject, schemaErrors, type PayloadSchema } from '@argus/contracts';
import type { AgentRequest, Form, HandOff } from '../types.js';

const STUCK = 'the work is stuck and needs the Manager';
const OPERATOR = 'a question only the operator can decide';

/** Each addressee and the work it receives. */
const TO: Record<Form, Record<string, string>> = {
  manager: {
    engineer: 'work one person can finish, including several related requests; the first line names the task',
    planner: 'work needing several Engineers or dependent tasks; or a letter about the Planner’s work',
    operator: 'your answer, report or question',
  },
  plan: {
    engineer: 'tasks for Engineers, one letter per task; the first line names each task',
    planner: 'continue when another letter arrives',
    manager: `the operator’s goal is met, or ${STUCK}`,
    operator: OPERATOR,
  },
  engineer: {
    reviewer: 'the delivery, ready for independent review',
    engineer: 'continue working next turn',
    planner: 'a task the Planner gave cannot go on as briefed',
    manager: STUCK,
    operator: OPERATOR,
  },
  review: {
    engineer: 'what needs repair',
    planner: 'your judgment on the Planner’s task',
    manager: 'your judgment on the Manager’s task, or the work is stuck',
    operator: OPERATOR,
  },
};

const LETTER = 'Your letter, passed on as written. The reader sees the workspace and this letter.';
const LETTERS = 'One letter per Engineer task; one letter to any other addressee.';
const AFTER = 'Task ids this task waits for. Earlier letters take the next free ids in order. Empty to start now, or when writing to anyone else.';
const TASK = 'An open task id to send this letter to its holder. Empty to open a new task, or when writing to anyone except the Engineer.';
const OPEN = 'Result file paths relative to the workspace, first to open first. Empty when handing over no files.';

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
