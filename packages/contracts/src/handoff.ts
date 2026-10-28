import definition from '../schemas/handoff_forms.json' with { type: 'json' };
import type { PayloadSchema } from './schema.js';

export interface HandOffForm {
  description: string;
  /** The shared core fields this form offers. */
  core: string[];
  to: string[];
  required: string[];
  properties: Record<string, PayloadSchema>;
}

/** The forms every role's turn ends with, through the `hand_off` tool. */
export const HANDOFF_FORMS: {
  action: string;
  /** Appended to `to`'s description where a form can hand to the operator. */
  operator: string;
  core: Record<string, PayloadSchema>;
  forms: Record<string, HandOffForm>;
} = definition;
