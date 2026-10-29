import { act, create } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useSessionComposer, type DraftSnapshot } from '../useSessionComposer';

let controller: ReturnType<typeof useSessionComposer>;
let renderer: ReturnType<typeof create>;
function Probe({ sid }: { sid: string | null }) { controller = useSessionComposer(sid); return null; }
function mount(sid: string | null = 's-A') { act(() => { renderer = create(<Probe sid={sid} />); }); }
function select(sid: string | null) { act(() => renderer.update(<Probe sid={sid} />)); }
let saved: Map<string, string>;
beforeEach(() => {
  saved = new Map();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => saved.get(key) ?? null,
    setItem: (key: string, value: string) => saved.set(key, value),
    removeItem: (key: string) => saved.delete(key),
  });
});
afterEach(() => { act(() => renderer?.unmount()); vi.restoreAllMocks(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('persistent text drafts and in-memory file revisions', () => {
  it('persists completed references, retains unfinished reminders and fences removed files', () => {
    mount();
    const first = new File(['a'], 'first.txt');
    const second = new File(['b'], 'second.txt');
    const attachment = { attachment_id: 'att-123456789abc', original_name: 'first.txt',
      stored_name: 'first.txt', relative_path: 'registered/first.txt', mime: 'text/plain', size_bytes: 1 };
    act(() => { controller.setText('send'); controller.setAttachments([first, second]); });
    const original = controller.capture('s-A')!;
    act(() => controller.rememberUploaded(original, first, attachment));
    expect(controller.draft.revision).toBe(original.revision);
    expect(original.uploaded?.[0].attachment).toEqual(attachment);
    act(() => renderer.unmount());
    mount();
    expect(controller.draft.attachments).toEqual([]);
    expect(controller.draft.missingAttachments).toEqual(['second.txt']);
    expect(controller.draft.uploaded).toEqual([{ attachment }]);
    act(() => controller.removeUploaded(attachment.attachment_id));
    expect(controller.draft.uploaded).toEqual([]);
    act(() => { controller.setAttachments([first]); });
    const stale = controller.capture('s-A')!;
    act(() => controller.setAttachments([]));
    act(() => controller.rememberUploaded(stale, first, attachment));
    expect(controller.draft.uploaded).toEqual([]);
  });

  it('restores completed references after a failed dispatch without overwriting a newer draft', () => {
    mount();
    const file = new File(['a'], 'a.txt');
    act(() => { controller.setText('original'); controller.setAttachments([file]); });
    const original = controller.capture('s-A')!;
    const attachment = { attachment_id: 'att-123456789abc', original_name: 'a.txt',
      stored_name: 'a.txt', relative_path: 'registered/a.txt', mime: 'text/plain', size_bytes: 1 };
    act(() => controller.rememberUploaded(original, file, attachment));
    let cleared!: DraftSnapshot;
    act(() => { cleared = controller.consume(original, [file])!; });
    expect(controller.draft.uploaded).toEqual([]);
    act(() => { expect(controller.restore(cleared, original)).toBe(true); });
    expect(controller.draft.uploaded?.[0].attachment).toEqual(attachment);
    act(() => controller.setText('newer'));
    act(() => { expect(controller.restore(cleared, original)).toBe(false); });
    expect(controller.draft.text).toBe('newer');
  });
  it('restores project text after remount but never serializes or restores File bytes', () => {
    mount();
    const file = new File(['A'], 'report.md');
    act(() => { controller.setText('A draft'); controller.setAttachments([file]); });
    select('s-B'); expect(controller.draft.text).toBe(''); expect(controller.draft.attachments).toEqual([]);
    select('s-A'); expect(controller.draft.text).toBe('A draft'); expect(controller.draft.attachments[0]).toBe(file);
    select(null); select('s-A'); expect(controller.draft.text).toBe('A draft');
    expect(saved.get('argus.composer.draft.v1:s-A')).toBe(JSON.stringify({ text: 'A draft', attachments: ['report.md'] }));
    act(() => renderer.unmount()); mount('s-A');
    expect(controller.draft.text).toBe('A draft');
    expect(controller.draft.attachments).toEqual([]);
    expect(controller.draft.missingAttachments).toEqual(['report.md']);
    act(() => controller.setAttachments([file]));
    expect(controller.draft.missingAttachments).toEqual([]);
  });

  it('restores each project independently and lets the user discard missing-file reminders', () => {
    mount(); act(() => { controller.setText('first'); controller.setAttachments([new File(['secret bytes'], 'input.csv')]); });
    select('s-B'); act(() => controller.setText('second'));
    act(() => renderer.unmount()); mount('s-B');
    expect(controller.draft.text).toBe('second');
    select('s-A');
    expect(controller.draft.text).toBe('first');
    act(() => controller.dismissMissingAttachments());
    expect(controller.draft.missingAttachments).toEqual([]);
    expect(saved.get('argus.composer.draft.v1:s-A')).not.toContain('secret bytes');
    expect(saved.get('argus.composer.draft.v1:s-A')).not.toContain('input.csv');
  });

  it('clears a sent draft durably and restores it after a failed send', () => {
    mount(); act(() => controller.setText('send'));
    const original = controller.capture('s-A')!;
    let cleared!: DraftSnapshot;
    act(() => { cleared = controller.consume(original, [])!; });
    expect(saved.has('argus.composer.draft.v1:s-A')).toBe(false);
    act(() => { controller.restore(cleared, original); });
    act(() => renderer.unmount()); mount();
    expect(controller.draft.text).toBe('send');
    act(() => controller.drop('s-A'));
    expect(saved.has('argus.composer.draft.v1:s-A')).toBe(false);
  });

  it('surfaces storage failures without losing the in-memory draft', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('blocked'); },
      setItem: () => { throw new Error('quota'); },
      removeItem: () => { throw new Error('blocked'); },
    });
    mount();
    expect(controller.storageError).toBe(true);
    act(() => controller.setText('copy before leaving'));
    expect(controller.draft.text).toBe('copy before leaving');
    expect(controller.storageError).toBe(true);
  });

  it('reports malformed stored data for its own project without exposing another draft', () => {
    saved.set('argus.composer.draft.v1:s-A', '{"text":12,"attachments":[]}');
    mount(); expect(controller.storageError).toBe(true);
    expect(controller.draft.text).toBe('');
    select('s-B'); act(() => controller.setText('safe'));
    expect(controller.storageError).toBe(false);
    select('s-A'); expect(controller.storageError).toBe(true);
  });

  it('seeds only the newly created project without replacing another unsent draft', () => {
    mount(); act(() => controller.setText('keep'));
    act(() => { controller.seed('s-new', 'starter'); });
    expect(controller.draft.text).toBe('keep');
    select('s-new'); expect(controller.draft.text).toBe('starter');
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
