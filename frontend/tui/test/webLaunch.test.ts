import assert from 'node:assert/strict';
import { test } from 'node:test';
import { browserCommand, webUiUrl } from '../src/webLaunch.js';

test('webUiUrl uses a browser-safe host and optional project', () => {
  assert.equal(webUiUrl('0.0.0.0', 8799), 'http://127.0.0.1:8799/');
  assert.equal(
    webUiUrl('localhost', 9000, 's-a b'),
    'http://localhost:9000/?project=s-a+b',
  );
});

test('browserCommand is headless-safe and platform aware', () => {
  assert.equal(browserCommand('http://x', 'linux', {}), null);
  assert.deepEqual(browserCommand('http://x', 'linux', { VSCODE_IPC_HOOK_CLI: '/tmp/vscode.sock' }), {
    command: 'code', args: ['--open-url', 'http://x'],
  });
  assert.deepEqual(browserCommand('http://x', 'linux', { DISPLAY: ':0' }), {
    command: 'xdg-open', args: ['http://x'],
  });
  assert.deepEqual(browserCommand('http://x', 'darwin', {}), {
    command: 'open', args: ['http://x'],
  });
});
