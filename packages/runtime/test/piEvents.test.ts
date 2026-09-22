import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import type { JsonObject } from '@argus/contracts';
import { PiEventConsumer } from '../src/piEvents.js';

const cases = JSON.parse(readFileSync(new URL('../fixtures/pi-events.json', import.meta.url), 'utf8')) as Array<{
  id: string; events: JsonObject[];
  expected: { threadId: string | null; agentMessages: string[]; turnCompleted: boolean; turnFailed: boolean; fatalError: string | null };
}>;
for (const fixture of cases) {
  test(`shared Python/TS Pi receipt: ${fixture.id}`, () => {
    const state = new PiEventConsumer();
    for (const event of fixture.events) state.consume(event);
    assert.deepEqual({ threadId: state.threadId, agentMessages: state.agentMessages,
      turnCompleted: state.turnCompleted, turnFailed: state.turnFailed, fatalError: state.fatalError }, fixture.expected);
  });
}
