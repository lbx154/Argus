import { act, create } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useSessionComposer, type DraftSnapshot } from '../useSessionComposer';

let controller: ReturnType<typeof useSessionComposer>;
let renderer: ReturnType<typeof create>;
function Probe({ sid }: { sid: string | null }) { controller = useSessionComposer(sid); return null; }
function mount(sid: string | null = 's-A') { act(() => { renderer = create(<Probe sid={sid} />); }); }
function select(sid: string | null) { act(() => renderer.update(<Probe sid={sid} />)); }
afterEach(() => { act(() => renderer?.unmount()); vi.unstubAllGlobals(); });

describe('in-memory session composer revisions', () => {
  it('shares one sid slot and never serializes File objects or persists a draft', () => {
    const persist = vi.fn(() => { throw new Error('Draft persistence is out of scope'); });
    for (const key of ['localStorage', 'sessionStorage', 'indexedDB']) vi.stubGlobal(key, { setItem: persist, open: persist });
    mount();
    const file = new File(['A'], 'report.md');
    act(() => { controller.setText('A draft'); controller.setAttachments([file]); });
    select('s-B'); expect(controller.draft.text).toBe(''); expect(controller.draft.attachments).toEqual([]);
    select('s-A'); expect(controller.draft.text).toBe('A draft'); expect(controller.draft.attachments[0]).toBe(file);
    select(null); select('s-A'); expect(controller.draft.text).toBe('A draft');
    expect(persist).not.toHaveBeenCalled();
    act(() => renderer.unmount()); mount('s-A');
    expect(controller.draft.text).toBe(''); // Refresh recovery is intentionally not implemented.
  });

  it('fences same-text ABA edits and new File objects with identical metadata', () => {
    mount();
    const old = new File(['old'], 'report.md', { lastModified: 1 });
    const fresh = new File(['new'], 'report.md', { lastModified: 1 });
    act(() => { controller.setText('same'); controller.setAttachments([old]); });
    const before = controller.capture('s-A')!;
    act(() => { controller.setText(''); controller.setText('same'); controller.setAttachments([fresh]); });
    expect(controller.draft.revision).toBeGreaterThan(before.revision);
    act(() => expect(controller.consume(before, [old])).toBeNull());
    expect(controller.draft.text).toBe('same'); expect(controller.draft.attachments[0]).toBe(fresh);
  });

  it('clears only sent File references, and restores only its own untouched clearance', () => {
    mount();
    const old = new File(['old'], 'report.md');
    const other = new File(['other'], 'report.md');
    act(() => { controller.setText('send'); controller.setAttachments([old, other]); });
    const original = controller.capture('s-A')!;
    let cleared!: DraftSnapshot;
    act(() => { cleared = controller.consume(original, [old])!; });
    expect(controller.draft.attachments).toEqual([other]);
    expect(controller.draft.attachments[0]).toBe(other);
    select('s-B'); act(() => controller.setText('B'));
    act(() => expect(controller.restore(cleared, original)).toBe(true));
    expect(controller.draft.text).toBe('B');
    select('s-A'); expect(controller.draft.text).toBe('send');
    act(() => expect(controller.restore(cleared, original)).toBe(false)); // A repeated callback cannot replay restoration.
  });

  it('does not restore over a deliberately empty newer draft', () => {
    mount(); act(() => controller.setText('send'));
    const original = controller.capture('s-A')!;
    let cleared!: DraftSnapshot;
    act(() => { cleared = controller.consume(original, [])!; });
    act(() => { controller.setText('next'); controller.setText(''); });
    act(() => expect(controller.restore(cleared, original)).toBe(false));
    expect(controller.draft.text).toBe('');
  });

  it('invalidates deleted operations and does not let a previous rewrite finish a new one', () => {
    mount(); act(() => controller.setText('original'));
    let first!: NonNullable<ReturnType<typeof controller.beginRewrite>>;
    act(() => { first = controller.beginRewrite('s-A')!; });
    act(() => expect(controller.beginRewrite('s-A')).toBeNull());
    act(() => controller.drop('s-A'));
    act(() => controller.setText('original'));
    let second!: typeof first;
    act(() => { second = controller.beginRewrite('s-A')!; });
    expect(second.request).not.toBe(first.request);
    act(() => expect(controller.finishRewrite(first, 'stale')).toBe(false));
    expect(controller.rewriting).toBe(true);
    act(() => expect(controller.finishRewrite(second, 'fresh')).toBe(true));
    expect(controller.draft.text).toBe('fresh');
    expect(controller.rewriting).toBe(false);
  });
});
