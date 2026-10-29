import { renderToStaticMarkup } from 'react-dom/server';
import { MESSAGE_ATTACHMENT_ACCEPT } from '../lib/attachments';
import { describe, expect, it } from 'vitest';
import { applyCompletion, slashCompletions } from '../../../core/src/commands';
import { ChatBox } from '../components/ChatBox';
import { completionMenu } from '../lib/promptCompletion';
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
    expect(applyCompletion(visible[selected])).toBe('/cancel');
  });

  it('renders shared command names and usage', () => {
    const menu = completionMenu('/sta', null, undefined, (key) => key, 'en');
    expect(menu?.kind).toBe('command');
    const html = renderToStaticMarkup(
      <SlashCompletionMenu items={menu!.items} label="Slash command suggestions" selected={0} onSelect={() => undefined} />,
    );
    expect(html).toContain('/status');
    expect(html).toContain('roles, queued work and health');
    expect(html).toContain('role="listbox"');
    expect(html).toContain('aria-label="Slash command suggestions"');
  });

  it('renders no menu after argument entry starts', () => {
    expect(completionMenu('/task write', null, undefined, (key) => key, 'en')).toBeNull();
    const html = renderToStaticMarkup(
      <ChatBox attachments={[]} onAttachmentsChange={() => {}} value="/task write" onChange={() => undefined}
        onSend={() => false} onCancel={() => undefined} disabled={false} pending={false}
        slashSelection={0} onSlashSelectionChange={() => undefined} />,
    );
    expect(html).not.toContain('role="listbox"');
    expect(html).toContain('aria-expanded="false"');
  });

  it('links the textarea to the stable slash listbox and selected option', () => {
    const html = renderToStaticMarkup(
      <ChatBox attachments={[]} onAttachmentsChange={() => {}}
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
    expect(html).toContain(`aria-activedescendant="${slashCompletionOptionId('cancel')}"`);
    expect(html).toContain(`id="${SLASH_COMPLETION_LISTBOX_ID}"`);
    // Options stay out of the Tab order: the textarea answers the keys.
    expect(html.match(/role="option"/g)!.length).toBe(html.match(/role="option" tabindex="-1"/g)!.length);
    expect(html).toContain('aria-keyshortcuts="Control+R Meta+R"');
    expect(html).toContain('type="file"');
    expect(html).toContain(`accept="${MESSAGE_ATTACHMENT_ACCEPT}"`);
    expect(MESSAGE_ATTACHMENT_ACCEPT).toContain('.zip');
  });

  it('shows the operator-owned Task/Auto/Chat category selector', () => {
    const html = renderToStaticMarkup(
      <ChatBox attachments={[]} onAttachmentsChange={() => {}}
        value="ship it"
        onChange={() => undefined}
        onSend={() => false}
        onCancel={() => undefined}
        disabled={false}
        pending={false}
        slashSelection={0}
        onSlashSelectionChange={() => undefined}
        routeOverride="task"
        onRouteOverrideChange={() => undefined}
      />,
    );
    expect(html).toContain('message category');
    expect(html).toContain('role="radiogroup"');
    expect(html).toMatch(/aria-checked="true"[^>]*>Task</);
    expect(html).toMatch(/aria-checked="false"[^>]*>Auto</);
    expect(html).toMatch(/aria-checked="false"[^>]*>Chat</);
  });

  it('keeps stop control but omits the synthetic quiet-progress row', () => {
    const html = renderToStaticMarkup(
      <ChatBox attachments={[]} onAttachmentsChange={() => {}}
        value=""
        onChange={() => undefined}
        onSend={() => false}
        onCancel={() => undefined}
        disabled={false}
        pending
        slashSelection={0}
        onSlashSelectionChange={() => undefined}
      />,
    );
    expect(html).toContain('Esc stops this reply');
    expect(html).not.toContain('Argus is working on your message');
    expect(html).not.toContain('Still working; no new update');
  });
});
