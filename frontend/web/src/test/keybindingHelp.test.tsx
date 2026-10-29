import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { KeybindingHelp } from '../components/KeybindingHelp';

describe('keybinding help panel', () => {
  it('shows browser keyboard shortcuts', () => {
    const html = renderToStaticMarkup(
      createElement(KeybindingHelp, { open: true, onClose: () => undefined }),
    );
    // Ctrl/⌘+R is the browser's reload, never a rewrite.
    expect(html).not.toContain('Ctrl+R');
    // Off Apple devices the keys say Ctrl, never ⌘.
    expect(html).toContain('Ctrl+K');
    expect(html).not.toContain('Ctrl+T');
    expect(html).not.toContain('Ctrl+.');
    expect(html).not.toContain('⌘');
    expect(html).not.toContain('Ctrl+O');
  });
});
