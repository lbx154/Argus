// A hand-off is a letter and its addressee. The addressee (`to`) is the one thing the host
// reads, and the backend CLI holds the model's final answer to it (Codex --output-schema,
// Claude --json-schema), or, with no such answer, takes the hand-off as a tool whose call ends
// the turn (Copilot, Pi), so a turn cannot end in prose that only reads like a hand-off. The
// letter is free text: it reaches the addressee as written, and the operator reads every one.

import { isJsonObject, schemaErrors, type PayloadSchema } from '@argus/contracts';
import type { AgentRequest, Form, HandOff } from '../types.js';

const STUCK = 'the work is stuck and needs the Manager to get it going';
const OPERATOR = 'only for what the operator alone can decide: the letter is your question, and the work waits for the answer';

/** Whom each role can write to, and what such a letter is for. Every addressee is someone who
 * acts on it: the work is never sent to a state such as done. */
const TO: Record<Form, Record<string, string>> = {
  manager: {
    engineer: 'one coherent piece of work, opened as a task; the letter is its whole brief',
    planner: 'work in several dependent steps for the Planner to divide, or word on the work it runs; the letter is its whole brief',
    operator: 'your answer, report or question',
  },
  plan: {
    engineer: 'the next task; the letter is its whole brief: the decision, its inputs and its check',
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
    engineer: 'concrete in-scope repairs, named in the letter',
    planner: 'your judgment on a task the Planner gave: accepted, or what is wrong',
    manager: `your judgment on a task the Manager gave: accepted, or what is wrong; or ${STUCK}`,
    operator: OPERATOR,
  },
};

const LETTER = 'Your letter, passed on as written: its reader sees the workspace and your letter, not your conversation.';

export function formSchema(form: Form): PayloadSchema {
  const to = TO[form];
  const meanings = Object.entries(to).map(([place, meaning]) => `${place}, ${meaning}`).join('; ');
  return {
    type: 'object', description: 'Your hand-off: the final answer of your turn.', additionalProperties: false,
    properties: {
      to: { type: 'string', enum: Object.keys(to), description: `Who holds the work next: ${meanings}.` },
      letter: { type: 'string', minLength: 1, maxLength: 20000, description: LETTER },
    },
    required: ['to', 'letter'],
  };
}

/** The form as a tool, for a backend whose turn ends with a tool call; its arguments are the form. */
export const handOffTool = (schema: PayloadSchema) => ({
  name: 'hand_off', description: `${schema.description} Call it last: the call ends your turn.`, parameters: schema,
});

/** The hand-off a turn's final answer records, or the reason Argus refuses it. */
export function judge(form: Form, payload: unknown, check: AgentRequest['check']): string | HandOff {
  if (payload === undefined) return 'The turn ended without the hand-off form.';
  const errors = schemaErrors(payload, formSchema(form));
  if (errors.length || !isJsonObject(payload)) return errors.join('; ');
  const handoff: HandOff = { form, to: String(payload.to), letter: String(payload.letter).trim() };
  return check?.(handoff) ?? handoff;
}
