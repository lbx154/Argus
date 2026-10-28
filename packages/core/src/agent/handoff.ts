// A hand-off is a letter and its addressee. The addressee (`to`) is the one thing the host
// reads, and the backend CLI holds the model's final answer to it (Codex --output-schema,
// Claude --json-schema), so a turn cannot end in prose that only reads like a hand-off. The
// letter is free text: it reaches the addressee as written, and the operator reads every one.

import { isJsonObject, schemaErrors, type PayloadSchema } from '@argus/contracts';
import type { AgentRequest, Form, HandOff } from '../types.js';

const STUCK = 'the process is stuck (an environment, tool, access, credential or quota failure, or the same error repeating) and needs the Manager to debug it';
const OPERATOR = 'only for what the operator alone can decide (a choice they reserved, requirements of theirs that conflict, words of theirs that read two ways where the reading changes the result): the letter is your question';

/** Whom each role can write to, and what that letter means. */
const TO: Record<Form, Record<string, string>> = {
  manager: {
    engineer: 'one coherent piece of work; the letter is its whole brief',
    planner: 'work in several dependent steps for the Planner to divide; the letter is its whole brief',
    operator: OPERATOR,
    manager: 'nothing for the team: the letter is your answer to the operator',
  },
  plan: {
    engineer: 'the next task starts; the letter is its whole brief: the decision, its inputs and its check',
    manager: STUCK,
    operator: OPERATOR,
    done: 'the operator\'s complete goal is met',
  },
  engineer: {
    reviewer: 'the work is ready for the independent review (do not start one yourself or through a subagent)',
    engineer: 'you keep working next turn',
    planner: 'the task is mis-scoped',
    manager: STUCK,
    operator: OPERATOR,
  },
  review: {
    done: 'you accept it, only after checking each requirement and check of the task against the deliverable: tests the Engineer wrote show the code matches its reading of the task, not that the reading is right',
    engineer: 'concrete in-scope repairs, named in the letter',
    planner: 'the task or the plan is wrong',
    manager: STUCK,
    operator: OPERATOR,
  },
};

const LETTER = 'Your letter, sent as written. Its reader sees the workspace and this letter, not your conversation: '
  + 'work you hand on gets its whole brief (what to deliver, how it is checked, the operator\'s limits in their words, files to read first); '
  + 'otherwise say what changed, the decisive check and what is still missing. The operator reads every letter: '
  + 'begin with one plain sentence in their language that stands alone.';

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

/** The hand-off a turn's final answer records, or the reason Argus refuses it. */
export function judge(form: Form, payload: unknown, check: AgentRequest['check']): string | HandOff {
  if (payload === undefined) return 'The turn ended without the hand-off form.';
  const errors = schemaErrors(payload, formSchema(form));
  if (errors.length || !isJsonObject(payload)) return errors.join('; ');
  const handoff: HandOff = { form, to: String(payload.to), letter: String(payload.letter).trim() };
  return check?.(handoff) ?? handoff;
}
