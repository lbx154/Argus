import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolveShowReasoning } from '../src/showReasoning.js';

/**
 * Observed on 2026-07-26 in a real operator session: the TUI displayed
 * "**Considering file edits and testing** / I'm thinking about..." — the
 * model's inner scratchpad — even though ARGUS_SKILL_SHOW_REASONING defaults to
 * "0", the web README documents it as hidden, and cli/render.py,
 * apps/cli/_follow.py and the web stream all hide it.
 *
 * The TUI alone used a deny-list: anything not in {0,false,no,off} counted as
 * "show". An unset variable is the empty string, which is not in that list, so
 * the default case — no variable at all — showed it.
 */

test('an unset variable hides reasoning, matching every other surface', () => {
  assert.equal(resolveShowReasoning({}), false);
  assert.equal(resolveShowReasoning({ ARGUS_SKILL_SHOW_REASONING: undefined }), false);
});

test('an empty or blank value is not an opt-in', () => {
  assert.equal(resolveShowReasoning({ ARGUS_SKILL_SHOW_REASONING: '' }), false);
  assert.equal(resolveShowReasoning({ ARGUS_SKILL_SHOW_REASONING: '   ' }), false);
});

test('the operator can still opt in, in the words the knob accepts', () => {
  for (const value of ['1', 'true', 'yes', 'on', 'TRUE', ' On ']) {
    assert.equal(
      resolveShowReasoning({ ARGUS_SKILL_SHOW_REASONING: value }),
      true,
      `expected ${JSON.stringify(value)} to opt in`,
    );
  }
});

test('explicit off values stay off', () => {
  for (const value of ['0', 'false', 'no', 'off']) {
    assert.equal(resolveShowReasoning({ ARGUS_SKILL_SHOW_REASONING: value }), false);
  }
});

test('an unrecognised value is not treated as an opt-in', () => {
  // The old deny-list turned every typo into "show". Silence is the safe
  // reading of a setting we did not understand.
  assert.equal(resolveShowReasoning({ ARGUS_SKILL_SHOW_REASONING: 'maybe' }), false);
  assert.equal(resolveShowReasoning({ ARGUS_SKILL_SHOW_REASONING: 'ture' }), false);
});
