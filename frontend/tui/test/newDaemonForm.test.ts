import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import { test } from 'node:test';
import React from 'react';
import { render } from 'ink';
import { NewDaemonForm } from '../src/components/NewDaemonForm.js';
import {
  daemonDraftValues,
  daemonFormInput,
  newDaemonDraft,
  type NewDaemonDraft,
} from '../src/newDaemonForm.js';

const ANSI = /\u001B\[[0-?]*[ -/]*[@-~]/g;
const words = (output: string) => output.replace(/[│╭╮╰╯─]/g, ' ').replace(/\s+/g, ' ');

async function renderForm(draft: NewDaemonDraft, width: number): Promise<string> {
  const stdout = new PassThrough() as PassThrough & {
    columns: number;
    rows: number;
    isTTY: boolean;
  };
  stdout.columns = width;
  stdout.rows = 30;
  stdout.isTTY = false;
  let output = '';
  stdout.on('data', (chunk) => { output += String(chunk); });
  const instance = render(
    React.createElement(NewDaemonForm, { draft }),
    { stdout: stdout as never, debug: true, exitOnCtrlC: false, patchConsole: false },
  );
  await new Promise((resolve) => setTimeout(resolve, 20));
  instance.unmount();
  await new Promise((resolve) => setTimeout(resolve, 5));
  return output.replace(ANSI, '');
}

test('daemon form captures an optional name and objective before submit', () => {
  let draft = newDaemonDraft();
  assert.equal(draft.field, 'name');
  draft = daemonFormInput(draft, 'AAAI paper', {}).draft;
  draft = daemonFormInput(draft, '', { tab: true }).draft;
  assert.equal(draft.field, 'objective');
  draft = daemonFormInput(draft, '写一篇具身智能论文', {}).draft;
  assert.deepEqual(daemonDraftValues(draft), {
    name: 'AAAI paper',
    objective: '写一篇具身智能论文',
  });
  assert.equal(daemonFormInput(draft, '', { return: true }).intent, 'submit');
  assert.equal(daemonFormInput(draft, '', { escape: true }).intent, 'cancel');
});

test('daemon form prefills slash objectives, enforces limits, and locks while busy', () => {
  let draft = newDaemonDraft('reproduce kernel benchmark');
  assert.equal(draft.field, 'objective');
  assert.equal(daemonDraftValues(draft).objective, 'reproduce kernel benchmark');
  draft = { ...draft, field: 'name' };
  draft = daemonFormInput(draft, 'x'.repeat(100), {}).draft;
  assert.equal(Array.from(draft.name.value).length, 80);
  const busy = { ...draft, busy: true };
  assert.strictEqual(daemonFormInput(busy, 'ignored', {}).draft, busy);
  assert.equal(daemonFormInput(busy, '', { return: true }).intent, undefined);
});

test('daemon confirmation panel explains campaign semantics at 40–120 columns', async () => {
  const draft = newDaemonDraft('Write the AAAI paper');
  for (const width of [40, 60, 120]) {
    const output = await renderForm(draft, width);
    const content = words(output);
    assert.match(content, /\/new — open a fresh daemon/);
    assert.match(content, /Write the AAAI paper/);
    assert.match(content, /Campaign starts immediately/);
    assert.match(content, /Enter create & start/);
    assert.ok(output.split('\n').every((line) => Array.from(line).length <= width));
  }
});
