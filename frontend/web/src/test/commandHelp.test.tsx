import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { KeybindingHelp } from '../components/KeybindingHelp';

describe('keybinding help panel — command reference', () => {
  it('renders all shared command groups with labels and descriptions', () => {
    const html = renderToStaticMarkup(
      createElement(KeybindingHelp, { open: true, onClose: () => undefined }),
    );
    // Everyday group
    expect(html).toContain('/status');
    // Task management — arg uses angle brackets, HTML-encoded by React
    expect(html).toContain('/task &lt;text&gt;');
    // Configuration — optional arg with pipe-separated values
    expect(html).toContain('/skills [ls|promote');
    // Other group
    expect(html).toContain('/quit');
  });
});
