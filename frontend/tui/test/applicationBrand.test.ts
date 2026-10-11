import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import React from 'react';
import { render } from 'ink';
import { DEFAULT_APPLICATION_BRAND, parseApplicationBrand } from '../../core/src/applicationBrand.js';
import { applicationBrand } from '../src/applicationBrand.js';
import { Header } from '../src/components/Header.js';

test('terminal renders the selected product without an Argus mark or missing ramp color', async () => {
  const original = process.env.ARGUS_TUI_APPLICATION_BRAND;
  const output = new PassThrough();
  let text = '';
  output.on('data', (chunk) => { text += String(chunk); });
  process.env.ARGUS_TUI_APPLICATION_BRAND = JSON.stringify({
    ...DEFAULT_APPLICATION_BRAND, id: 'example', name: 'Example',
    tagline: 'Hardware engineering', terminal_wordmark: 'example',
    assets: Object.fromEntries(['mark', 'mark_dark', 'logo', 'logo_dark', 'icon', 'icon_dark']
      .map((name) => [name, `/api/application-brand/assets/${name}`])),
  });
  try {
    assert.equal(applicationBrand().id, 'example');
    const instance = render(React.createElement(Header, { width: 80 }), {
      stdout: output, debug: true, exitOnCtrlC: false, patchConsole: false,
    });
    await new Promise((resolve) => setTimeout(resolve, 30));
    instance.unmount();
    const visible = text.replace(/\x1b\[[0-9;]*m/g, '');
    assert.match(visible, /example/);
    assert.match(visible, /Hardware engineering/);
    assert.doesNotMatch(visible, /argus|◉/i);
    assert.throws(() => parseApplicationBrand({ ...DEFAULT_APPLICATION_BRAND, terminal_wordmark: '\x1bspoof' }));
  } finally {
    if (original === undefined) delete process.env.ARGUS_TUI_APPLICATION_BRAND;
    else process.env.ARGUS_TUI_APPLICATION_BRAND = original;
    output.destroy();
  }
});
