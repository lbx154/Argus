// The hand-off form a turn ends with. The backend CLI holds the model's final answer to the
// form's schema (Codex --output-schema, Claude --json-schema), so a turn cannot end in prose
// that only reads like a hand-off; Argus then judges what the model filled in.

import { HANDOFF_FORMS, isJsonObject, schemaErrors, type PayloadSchema } from '@argus/contracts';
import type { AgentRequest, Form, HandOff } from '../types.js';

/** What each place a form can send the work to means. */
const TO: Record<Form, string> = {
  manager: 'team, the request becomes the team\'s ticket (goal, acceptance, constraints, context), their whole brief: they do not see this conversation; operator, a question only the operator can decide (ask); manager, nothing for the team: your reply is the whole answer.',
  plan: 'engineer, the tasks below start, or with none the work in flight decides what comes next (invent no work that needs results still in flight); manager, the process is stuck for a mechanical reason no plan can route around (a failure that keeps repeating, an environment, tool, credential or quota failure, a loop of hand-offs), and the Manager debugs it; operator, only the operator can unblock it; done, the operator\'s complete goal is met, with no tasks.',
  engineer: 'reviewer, the work is ready for the independent review (do not start one yourself or through a subagent); done, the task is complete and requires no independent review; engineer, you keep working next turn; planner, the task is mis-scoped; manager, the process is stuck (an environment, tool, access, credential or quota failure, or the same error repeating) and needs the Manager to debug it; operator, a decision only the operator can make.',
  review: 'done accepts it, only after you checked each requirement and acceptance check of the task against the deliverable: tests the Engineer wrote show the code matches its reading of the task, not that the reading is right; engineer returns concrete in-scope repairs, named in note; planner, the task or plan is wrong; manager, the process is stuck (an environment, tool or access failure you cannot judge past) and needs the Manager to debug it; operator, a decision only the operator can make.',
};

/** The fields of the shared forms the flow reads; those forms carry more for the Python runtime. */
const FIELDS: Record<Form, string[]> = {
  manager: ['goal', 'acceptance', 'constraints', 'context', 'plan', 'lifetime', 'workflow_mode', 'require_independent_review'],
  plan: ['tasks', 'retire'],
  engineer: [],
  review: [],
};
const TASK_FIELDS = ['title', 'objective', 'key', 'deps', 'hypothesis', 'decision_rule', 'acceptance_check', 'expected_regressions', 'non_goals', 'require_independent_review'];

const REPLY: PayloadSchema = {
  type: 'string',
  description: 'What the operator reads from you this turn, in their language: the answer, or what the team will deliver. With to=operator, only what they need before your question.',
};

const pick = (schema: PayloadSchema, names: string[]): PayloadSchema => ({
  ...schema, properties: Object.fromEntries(names.map(name => [name, schema.properties![name]!])),
  ...(schema.required ? { required: schema.required.filter(name => names.includes(name)) } : {}),
});

export function formSchema(form: Form): PayloadSchema {
  const spec = HANDOFF_FORMS.forms[form]!;
  const properties: Record<string, PayloadSchema> = {
    ...Object.fromEntries(spec.core.map(name => [name, HANDOFF_FORMS.core[name]!])),
    to: { type: 'string', enum: spec.to, description: `Who holds the work next: ${TO[form]} ${HANDOFF_FORMS.operator}` },
    ...(form === 'manager' ? { reply: REPLY } : {}),
    ...Object.fromEntries(FIELDS[form].map(name => [name, spec.properties[name]!])),
  };
  if (properties.tasks?.items) properties.tasks = { ...properties.tasks, items: pick(properties.tasks.items, TASK_FIELDS) };
  return {
    type: 'object', description: 'Your hand-off: the final answer of your turn.', properties,
    required: [...spec.required, ...(form === 'manager' ? ['reply'] : [])], additionalProperties: false,
  };
}

/** The hand-off a turn's final answer records, or the reason Argus refuses it. */
export function judge(form: Form, payload: unknown, check: AgentRequest['check']): string | HandOff {
  if (payload === undefined) return 'The turn ended without the hand-off form.';
  const errors = schemaErrors(payload, formSchema(form));
  if (errors.length || !isJsonObject(payload)) return errors.join('; ');
  const { to, summary, ...fields } = payload;
  // The rule every form shares: a question to the operator is their turn, asked properly.
  const ask = isJsonObject(payload.ask) ? payload.ask : undefined;
  if ((to === 'operator') !== Boolean(ask)) return 'to=operator goes with ask, and ask only with to=operator.';
  const labels = Array.isArray(ask?.options)
    ? ask.options.map(option => String(isJsonObject(option) ? option.label : '').toLowerCase().split(/\s+/).filter(Boolean).join(' '))
    : [];
  if (labels.length === 1 || new Set(labels).size < labels.length) {
    return 'ask.options: offer two to five different answers, or none for an open question.';
  }
  const handoff: HandOff = { form, to: String(to), summary: String(summary), fields };
  return check?.(handoff) ?? handoff;
}
