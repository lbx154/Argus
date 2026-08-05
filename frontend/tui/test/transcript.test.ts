import assert from 'node:assert/strict';
import test from 'node:test';

import { mergeTranscriptReplay, transcriptEvents } from '../src/transcript.js';

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

test('late transcript replay preserves live keys instead of reprinting rows', () => {
  const live = [
    {
      type: 'ui.operator',
      text: '你好',
      ts: 10.2,
      message_id: 'local-operator',
    },
    {
      type: 'ui.argus',
      text: '你好，我是 Argus Manager。',
      ts: 11.2,
      message_id: 'web-turn-argus',
    },
  ];

  const merged = mergeTranscriptReplay(live, [
    { role: 'operator', text: '你好', ts: 10 },
    { role: 'argus', text: '你好，我是 Argus Manager。', ts: 11 },
  ]);

  assert.equal(merged.length, 2);
  assert.equal(merged[0], live[0]);
  assert.equal(merged[1], live[1]);
  assert.deepEqual(
    merged.map((event) => event.message_id),
    ['local-operator', 'web-turn-argus'],
  );
});

test('late replay does not collapse two intentional identical live turns', () => {
  const live = [
    {
      type: 'ui.operator',
      text: '再试一次',
      ts: 10,
      message_id: 'local-turn-1',
    },
    {
      type: 'ui.operator',
      text: '再试一次',
      ts: 11,
      message_id: 'local-turn-2',
    },
  ];

  const merged = mergeTranscriptReplay(live, [
    { role: 'operator', text: '再试一次', ts: 10.1 },
    { role: 'operator', text: '再试一次', ts: 11.1 },
  ]);

  assert.equal(merged.length, 2);
  assert.equal(merged[0], live[0]);
  assert.equal(merged[1], live[1]);
});
