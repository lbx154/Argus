// A hand-off is a letter and its addressee. The addressee (`to`) is the one thing the host
// routes by, and the backend CLI holds the model's final answer to it (Codex --output-schema,
// Claude --json-schema), or, with no such answer, takes the hand-off as a tool whose call ends
// the turn (Copilot, Pi), so a turn cannot end in prose that only reads like a hand-off. The
// letter is free text: it reaches the addressee as written, and the operator reads every one.
// A result the Manager hands the operator names its files, which the page opens.

import { isJsonObject, schemaErrors, type PayloadSchema } from '@argus/contracts';
import type { AgentRequest, Form, HandOff } from '../types.js';

const STUCK = 'the work is stuck and needs the Manager to get it going';
const OPERATOR = 'only for what the operator alone can decide: the letter is your question, and you wait for the answer while the rest of the work goes on';

/** Whom each role can write to, and what such a letter is for. Every addressee is someone who
 * acts on it: the work is never sent to a state such as done. */
const TO: Record<Form, Record<string, string>> = {
  manager: {
    engineer: 'the work, whole, when one person can carry it through; it opens a task, named by the letter\'s first line, a short title, and the letter is its whole brief',
    planner: 'work too large for one task, whose later parts depend on what the earlier ones find, for the Planner to divide; or word on the work it runs. The letter is its whole brief',
    operator: 'your answer, report or question',
  },
  plan: {
    engineer: 'the next tasks, a letter each: each opens a task, a result for an Engineer to carry through, named by its letter\'s first line, a short title, and its letter is its whole brief; they run side by side',
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
// The result the operator opens: shown beside the letter as written, without a check.
const OPEN = 'What the operator opens, when your letter to the operator hands over a result: the paths of its files, relative to the workspace, the one to open first first. Empty for any other letter.';

export function formSchema(form: Form, giver?: AgentRequest['giver']): PayloadSchema {
  // The Planner hears only of the tasks it gave: it never had the Manager's.
  const to = Object.fromEntries(Object.entries(TO[form]).filter(([place]) => place !== 'planner' || giver !== 'manager'));
  const meanings = Object.entries(to).map(([place, meaning]) => `${place}, ${meaning}`).join('; ');
  const letter: PayloadSchema = { type: 'string', minLength: 1, maxLength: 20000 };
  // The Planner may open several tasks at once; every other turn ends in one letter.
  const [name, letters]: [string, PayloadSchema] = form === 'plan'
    ? ['letters', { type: 'array', minItems: 1, items: letter, description: LETTERS }] : ['letter', { ...letter, description: LETTER }];
  return {
    type: 'object', description: 'Your hand-off: the final answer of your turn.', additionalProperties: false,
    properties: {
      to: { type: 'string', enum: Object.keys(to), description: `Who holds the work next: ${meanings}.` }, [name]: letters,
      ...form === 'manager' ? { open: { type: 'array', items: { type: 'string', minLength: 1 }, description: OPEN } } : {},
    },
    required: ['to', name, ...form === 'manager' ? ['open'] : []],
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
  const letters = Array.isArray(payload.letters) ? payload.letters : [payload.letter];
  const open = Array.isArray(payload.open) ? payload.open.map(String) : [];
  return { form, to: String(payload.to), letters: letters.map(letter => String(letter).trim()), ...open.length ? { open } : {} };
}
