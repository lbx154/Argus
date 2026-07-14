import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SlashCompletionMenu } from '../components/SlashCompletionMenu';

describe('slash completion menu', () => {
  it('renders shared command names and usage', () => {
    const html = renderToStaticMarkup(
      <SlashCompletionMenu query="/sta" selected={0} onSelect={() => undefined} />,
    );
    expect(html).toContain('/status');
    expect(html).toContain('roles, queued work, journal, and health');
    expect(html).toContain('role="listbox"');
  });

  it('renders no menu after argument entry starts', () => {
    const html = renderToStaticMarkup(
      <SlashCompletionMenu query="/task write" selected={0} onSelect={() => undefined} />,
    );
    expect(html).toBe('');
  });
});
