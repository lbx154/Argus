import type { ConfigKnob } from '../api';

export const OPERATOR_AVAILABLE_KNOB = 'ARGUS_SKILL_OPERATOR_AVAILABLE';
const FALSE_VALUES = new Set(['0', 'false', 'no', 'off', 'disable', 'disabled']);

/** True when the effective config says nobody is there to answer questions. */
export function noOperatorMode(knobs: ConfigKnob[] | undefined): boolean {
  const knob = (knobs ?? []).find((item) => item.name === OPERATOR_AVAILABLE_KNOB);
  return Boolean(knob && FALSE_VALUES.has(knob.value.trim().toLowerCase()));
}
