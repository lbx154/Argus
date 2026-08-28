import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import { test } from 'node:test';
import React from 'react';
import { render } from 'ink';
import {
  HEADER_STATIC_ITEMS,
  StaticHeader,
} from '../src/components/Header.js';

test('static header uses stable unique keys and renders without key warnings', async () => {
  const ids = HEADER_STATIC_ITEMS.map((item) => item.id);
  assert.ok(ids.every((id) => id.trim().length > 0));
  assert.equal(new Set(ids).size, ids.length);

  const stdout = new PassThrough() as PassThrough & {
    columns: number;
    rows: number;
    isTTY: boolean;
  };
  stdout.columns = 80;
  stdout.rows = 10;
  stdout.isTTY = false;
  let output = '';
  stdout.on('data', (chunk) => { output += String(chunk); });

  const errors: unknown[][] = [];
  const originalError = console.error;
  console.error = (...args: unknown[]) => { errors.push(args); };
  try {
    const instance = render(
      React.createElement(StaticHeader, { width: 80 }),
      {
        stdout: stdout as never,
        debug: true,
        exitOnCtrlC: false,
        patchConsole: false,
      },
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
    instance.unmount();
    await new Promise((resolve) => setTimeout(resolve, 5));
  } finally {
    console.error = originalError;
  }

  const visibleOutput = output.replace(/\x1b\[[0-9;]*m/g, '');
  assert.match(visibleOutput, /argus/);
  assert.match(visibleOutput, /◉/);
  assert.match(visibleOutput, /Autonomous Work Lab/);
  assert.doesNotMatch(visibleOutput, /\bARGUS\b/);
  assert.equal(
    errors.some((args) => args.some((arg) => String(arg).includes('unique "key" prop'))),
    false,
  );
});
