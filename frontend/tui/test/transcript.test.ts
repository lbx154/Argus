import assert from 'node:assert/strict';
import test from 'node:test';

import { transcriptEvents } from '../src/transcript.js';

test('transcriptEvents restores operator and Argus turns for resume', () => {
  assert.deepEqual(
    transcriptEvents([
      { role: 'operator', text: '继续上次任务', ts: 10 },
      { role: 'argus', text: '正在继续', ts: 11 },
    ]),
    [
      { type: 'ui.operator', text: '继续上次任务', ts: 10 },
      { type: 'ui.argus', text: '正在继续', ts: 11 },
    ],
  );
});

test('transcriptEvents ignores empty and internal turns', () => {
  assert.deepEqual(
    transcriptEvents([
      { role: 'system', text: 'hidden' },
      { role: 'operator', text: '   ' },
      { role: 'argus', text: ' visible ' },
    ]),
    [{ type: 'ui.argus', text: 'visible' }],
  );
});
