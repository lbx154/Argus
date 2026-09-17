import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { isImeComposing } from '../lib/ime';

/** A keydown as it arrives while an IME candidate window is open. */
function composingEvent(key: string, extra: Record<string, unknown> = {}) {
  return {
    key,
    keyCode: 229,
    nativeEvent: { isComposing: true },
    preventDefault: vi.fn(),
    ...extra,
  } as never;
}

function committedEvent(key: string, extra: Record<string, unknown> = {}) {
  return {
    key,
    keyCode: key === 'Enter' ? 13 : 0,
    nativeEvent: { isComposing: false },
    preventDefault: vi.fn(),
    ...extra,
  } as never;
}

describe('IME composition guard', () => {
  it('detects a keystroke that belongs to the candidate window', () => {
    expect(isImeComposing(composingEvent('Enter'))).toBe(true);
  });

  it('lets a committed keystroke through', () => {
    expect(isImeComposing(committedEvent('Enter'))).toBe(false);
  });

  it('honours the legacy keyCode 229 even without isComposing', () => {
    // Some IMEs never set isComposing but still report 229.
    expect(
      isImeComposing({
        key: 'Enter',
        keyCode: 229,
        nativeEvent: { isComposing: false },
      } as never),
    ).toBe(true);
  });

  it('treats arrow keys during composition as the IME paging candidates', () => {
    expect(isImeComposing(composingEvent('ArrowDown'))).toBe(true);
    expect(isImeComposing(committedEvent('ArrowDown'))).toBe(false);
  });
});

describe('every keyboard handler guards composition', () => {
  // Pinning the call sites: a new Enter handler that forgets the guard sends
  // a half-typed Chinese message the moment the user picks their first word.
  const guarded = [
    'ChatBox.tsx',
    'CommandPalette.tsx',
    'PendingReplyDialog.tsx',
    'NewDaemonModal.tsx',
    'OperationsModal.tsx',
  ];

  it.each(guarded)('%s calls isImeComposing', async (file) => {
    const { readFileSync } = await import('node:fs');
    const text = readFileSync(
      new URL(`../components/${file}`, import.meta.url),
      'utf8',
    );

    expect(text).toContain('isImeComposing');
  });
});

describe('ChatBox still renders', () => {
  it('mounts without a composition event present', async () => {
    const { ChatBox } = await import('../components/ChatBox');

    const html = renderToStaticMarkup(
      <ChatBox attachments={[]} onAttachmentsChange={() => {}}
        value=""
        onChange={() => {}}
        onSend={async () => true}
        onCancel={() => {}}
        disabled={false}
        pending={false}
        focusSignal={0}
        slashSelection={0}
        onSlashSelectionChange={() => {}}
      />,
    );

    expect(html).toContain('textarea');
  });
});
