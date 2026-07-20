import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { applyCompletion, slashCompletions } from '../../../core/src/commands';
import { ChatBox } from '../components/ChatBox';
import {
  SlashCompletionMenu,
  clampSlashCompletionSelection,
  SLASH_COMPLETION_LISTBOX_ID,
  SLASH_COMPLETION_VISIBLE_ROWS,
  slashCompletionOptionId,
} from '../components/SlashCompletionMenu';

describe('slash completion menu', () => {
  it('caps repeated ArrowDown at the last visible row for bare / and applies that row', () => {
    const completions = slashCompletions('/');
    expect(completions.length).toBeGreaterThan(SLASH_COMPLETION_VISIBLE_ROWS);

    const visible = completions.slice(0, SLASH_COMPLETION_VISIBLE_ROWS);
    let selected = 0;
    for (let i = 0; i < 20; i += 1) {
      selected = clampSlashCompletionSelection(selected + 1, visible.length);
    }

    expect(selected).toBe(visible.length - 1);
    expect(applyCompletion(visible[selected])).toBe('/find ');
  });

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

  it('links the textarea to the stable slash listbox and selected option', () => {
    const html = renderToStaticMarkup(
      <ChatBox
        value="/"
        onChange={() => undefined}
        onSend={() => false}
        onCancel={() => undefined}
        disabled={false}
        pending={false}
        slashSelection={7}
        onSlashSelectionChange={() => undefined}
      />,
    );
    expect(html).toContain(`aria-controls="${SLASH_COMPLETION_LISTBOX_ID}"`);
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain(`aria-activedescendant="${slashCompletionOptionId('find')}"`);
    expect(html).toContain(`id="${SLASH_COMPLETION_LISTBOX_ID}"`);
  });

  it('renders rotating Argus heartbeat copy with honest quiet time', () => {
    const html = renderToStaticMarkup(
      <ChatBox
        value=""
        onChange={() => undefined}
        onSend={() => false}
        onCancel={() => undefined}
        disabled={false}
        pending
        phase="Manager · waiting for the next model event · 10s quiet"
        heartbeat
        quietS={10}
        startedAt={Date.now() - 10_000}
        slashSelection={0}
        onSlashSelectionChange={() => undefined}
      />,
    );
    expect(html).toContain('Manager alive');
    expect(html).toContain('10s quiet');
    expect(html).toContain('turning it over');
  });
});
