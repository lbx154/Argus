import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ChatBox } from '../components/ChatBox';
import { completionMenu } from '../lib/promptCompletion';

describe('prompt completion menu', () => {
  it('renders no menu after argument entry starts', () => {
    expect(completionMenu('/task write', null, undefined, (key) => key, 'en')).toBeNull();
    const html = renderToStaticMarkup(
      <ChatBox attachments={[]} onAttachmentsChange={() => {}} value="/task write" onChange={() => undefined}
        onSend={() => false} disabled={false} pending={false}
        completionSelection={0} onCompletionSelectionChange={() => undefined} />,
    );
    expect(html).not.toContain('role="listbox"');
    expect(html).toContain('aria-expanded="false"');
  });

  it('keeps the file picker when no completion is active', () => {
    const html = renderToStaticMarkup(<ChatBox attachments={[]} onAttachmentsChange={() => {}} value="" onChange={() => undefined}
      onSend={() => false} disabled={false} pending={false} completionSelection={0} onCompletionSelectionChange={() => undefined} />);
    expect(html).toContain('type="file"');
    expect(html).not.toContain('accept="');
  });

  it('shows the operator-owned Task/Auto/Chat category selector', () => {
    const html = renderToStaticMarkup(
      <ChatBox attachments={[]} onAttachmentsChange={() => {}}
        value="ship it"
        onChange={() => undefined}
        onSend={() => false}
        disabled={false}
        pending={false}
        completionSelection={0}
        onCompletionSelectionChange={() => undefined}
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

  it('shows no stop of its own, no Esc hint and no synthetic quiet-progress row', () => {
    const html = renderToStaticMarkup(
      <ChatBox attachments={[]} onAttachmentsChange={() => {}}
        value=""
        onChange={() => undefined}
        onSend={() => false}
        disabled={false}
        pending
        completionSelection={0}
        onCompletionSelectionChange={() => undefined}
      />,
    );
    expect(html).not.toMatch(/Esc|Stop/);
    expect(html).not.toContain('Argus is working on your message');
    expect(html).not.toContain('Still working; no new update');
  });
});
