import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import { test } from 'node:test';
import ansiEscapes from 'ansi-escapes';
import React from 'react';
import { render } from 'ink';
import stringWidth from 'string-width';
import { PromptBox } from '../src/components/PromptBox.js';
import { fromString } from '../src/input/editor.js';
import {
  createImeCursorOutput,
  ImeCursorProvider,
  type ImeCursorController,
  type ImeCursorTarget,
} from '../src/imeCursor.js';

const settle = () => new Promise<void>((resolve) => setImmediate(resolve));

function terminal(): PassThrough & {
  columns: number;
  rows: number;
  isTTY: boolean;
} {
  const stream = new PassThrough() as PassThrough & {
    columns: number;
    rows: number;
    isTTY: boolean;
  };
  stream.columns = 80;
  stream.rows = 24;
  stream.isTTY = true;
  return stream;
}

test('IME cursor anchors at the input cell and restores before the next Ink frame', async () => {
  const target = terminal();
  let output = '';
  target.on('data', (chunk) => { output += String(chunk); });
  const ime = createImeCursorOutput(target as never, { force: true });
  ime.controller.activate({ rowsAboveFrameBottom: 2, column: 21 });

  ime.stdout.write('frame\n');
  await settle();
  assert.ok(output.endsWith(`\r${ansiEscapes.cursorUp(3)}${ansiEscapes.cursorForward(21)}${ansiEscapes.cursorShow}`));

  output = '';
  ime.stdout.write('next frame\n');
  assert.ok(output.startsWith(`${ansiEscapes.cursorHide}\r${ansiEscapes.cursorDown(3)}\rnext frame\n`));
  await settle();
  ime.dispose();
});

test('full-screen Ink frames anchor relative to their final rendered line', async () => {
  const target = terminal();
  let output = '';
  target.on('data', (chunk) => { output += String(chunk); });
  const ime = createImeCursorOutput(target as never, { force: true });
  ime.controller.activate({ rowsAboveFrameBottom: 2, column: 9 });

  ime.stdout.write(`${ansiEscapes.clearTerminal}frame without trailing newline`);
  await settle();
  assert.ok(output.endsWith(`\r${ansiEscapes.cursorUp(2)}${ansiEscapes.cursorForward(9)}${ansiEscapes.cursorShow}`));
  ime.dispose();
});

test('PromptBox places the native caret after CJK terminal cells', async () => {
  let seen: ImeCursorTarget | null = null;
  const controller: ImeCursorController = {
    enabled: true,
    activate(target) {
      seen = target;
      return () => {};
    },
  };
  const stdout = terminal();
  stdout.isTTY = false;
  const instance = render(
    React.createElement(
      ImeCursorProvider,
      { controller },
      React.createElement(PromptBox, {
        edit: fromString('你好'),
        width: 80,
        rowsBelow: 1,
      }),
    ),
    {
      stdout: stdout as never,
      debug: true,
      exitOnCtrlC: false,
      patchConsole: false,
    },
  );
  await new Promise((resolve) => setTimeout(resolve, 20));
  instance.unmount();

  assert.deepEqual(seen, {
    rowsAboveFrameBottom: 2,
    column: 3 + stringWidth('◆ talk to Argus › ') + stringWidth('你好'),
  });
});
