import { act, create } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useSessionComposer } from '../useSessionComposer';

let controller: ReturnType<typeof useSessionComposer>;
let renderer: ReturnType<typeof create>;
function Probe({ sid }: { sid: string | null }) { controller = useSessionComposer(sid); return null; }
function mount(sid: string | null = 's-A') { act(() => { renderer = create(<Probe sid={sid} />); }); }
function select(sid: string | null) { act(() => renderer.update(<Probe sid={sid} />)); }
let saved: Map<string, string>;
beforeEach(() => {
  saved = new Map();
  vi.stubGlobal('window', new EventTarget());
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => saved.get(key) ?? null,
    setItem: (key: string, value: string) => saved.set(key, value),
    removeItem: (key: string) => saved.delete(key),
  });
});
afterEach(() => { act(() => renderer?.unmount()); vi.restoreAllMocks(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('persistent text drafts and in-memory file revisions', () => {

  it('reads external text on storage events and on returning to another project', () => {
    mount();
    const update = (id: string, text: string) => {
      saved.set(`argus.composer.draft.v1:${id}`, JSON.stringify({ text, attachments: [] }));
      window.dispatchEvent(Object.assign(new Event('storage'), { key: `argus.composer.draft.v1:${id}` }));
    };
    act(() => controller.setText('A long draft'));
    act(() => update('s-A', 'A long draft, continued in B'));
    expect(controller.draft.text).toBe('A long draft, continued in B');
    act(() => update('s-B', 'other project'));
    expect(controller.draft.text).toBe('A long draft, continued in B');
    select('s-B');
    expect(controller.draft.text).toBe('other project');
    act(() => { saved.clear(); window.dispatchEvent(Object.assign(new Event('storage'), { key: null })); });
    expect(controller.draft.text).toBe('');
    select('s-A');
    expect(controller.draft.text).toBe('');
  });

  it('fences remote edits during sending and rewriting, including same-text ABA edits', () => {
    mount();
    act(() => controller.setText('original'));
    const original = controller.capture('s-A')!;
    act(() => { controller.consume(original, []); });
    act(() => {
      saved.set('argus.composer.draft.v1:s-A', JSON.stringify({ text: 'remote input', attachments: [] }));
      window.dispatchEvent(Object.assign(new Event('storage'), { key: 'argus.composer.draft.v1:s-A' }));
    });
    act(() => expect(controller.consume(original, [])).toBeNull());
    let rewrite!: NonNullable<ReturnType<typeof controller.beginRewrite>>;
    act(() => { rewrite = controller.beginRewrite('s-A')!; });
    act(() => {
      window.dispatchEvent(Object.assign(new Event('storage'), { key: 'argus.composer.draft.v1:s-A' }));
    });
    act(() => expect(controller.finishRewrite(rewrite, 'stale rewrite')).toBe(false));
    expect(controller.draft.text).toBe('remote input');
    act(() => expect(controller.consume(original, [])).toBeNull());
  });

  it('shares uploaded references while preserving only local File bytes and dropping remotely removed files', () => {
    mount();
    const file = new File(['bytes'], 'shared.txt');
    act(() => { controller.setText('draft'); controller.setAttachments([file]); });
    const operation = controller.capture('s-A')!;
    const attachment = { attachment_id: 'att-123456789abc', original_name: 'shared.txt',
      stored_name: 'shared.txt', relative_path: 'registered/shared.txt', mime: 'text/plain', size_bytes: 5 };
    act(() => controller.rememberUploaded(operation, file, attachment));
    act(() => {
      saved.set('argus.composer.draft.v1:s-A', JSON.stringify({ text: 'edited elsewhere', attachments: [], uploaded: [attachment] }));
      window.dispatchEvent(Object.assign(new Event('storage'), { key: 'argus.composer.draft.v1:s-A' }));
    });
    expect(controller.draft.text).toBe('edited elsewhere');
    expect(controller.draft.attachments[0]).toBe(file);
    expect(controller.draft.uploaded?.[0].file).toBe(file);
    act(() => {
      saved.delete('argus.composer.draft.v1:s-A');
      window.dispatchEvent(Object.assign(new Event('storage'), { key: 'argus.composer.draft.v1:s-A' }));
    });
    expect(controller.draft.attachments).toEqual([]);
    act(() => controller.rememberUploaded(operation, file, attachment));
    expect(controller.draft.uploaded).toEqual([]);
  });

  it('changes the persisted identity only when text changes, including rewrite and undo', () => {
    mount();act(() => controller.setText('first'));const first=controller.draft.requestId;
    expect(first).toBeTruthy();expect(JSON.parse(saved.get('argus.composer.draft.v1:s-A')!).request_id).toBe(first);
    act(() => controller.setText('first'));expect(controller.draft.requestId).toBe(first);
    act(() => controller.setAttachments([new File(['a'],'a.txt')]));expect(controller.draft.requestId).toBe(first);
    act(() => controller.setText('second'));const second=controller.draft.requestId;expect(second).not.toBe(first);
    act(() => controller.setText('first'));expect(controller.draft.requestId).not.toBe(first);expect(controller.draft.requestId).not.toBe(second);
    const before=controller.draft.requestId;let rewrite!: NonNullable<ReturnType<typeof controller.beginRewrite>>;
    act(() => {rewrite=controller.beginRewrite('s-A')!;controller.finishRewrite(rewrite,'rewritten');});
    const rewritten=controller.draft.requestId;expect(rewritten).not.toBe(before);
    act(() => controller.undoRewrite!());expect(controller.draft.text).toBe('first');expect(controller.draft.requestId).not.toBe(rewritten);
  });

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

  it('keeps unconfirmed text, identity and completed references across project changes and reloads', () => {
    mount();const file = new File(['a'], 'a.txt');
    act(() => { controller.setText('unconfirmed');controller.setAttachments([file]); });
    const original = controller.capture('s-A')!;
    const attachment = { attachment_id: 'att-123456789abc', original_name: 'a.txt', stored_name: 'a.txt', relative_path: 'registered/a.txt', mime: 'text/plain', size_bytes: 1 };
    act(() => controller.rememberUploaded(original, file, attachment));
    select('s-B');select('s-A');
    expect(controller.capture('s-A')!.requestId).toBe(original.requestId);
    act(() => renderer.unmount());mount();
    expect(controller.draft.text).toBe('unconfirmed');
    expect(controller.draft.requestId).toBe(original.requestId);
    expect(controller.draft.uploaded?.[0].attachment).toEqual(attachment);
    expect(controller.draft.attachments).toEqual([]);
  });
  it('restores project text after remount but never serializes or restores File bytes', () => {
    mount();
    const file = new File(['A'], 'report.md');
    act(() => { controller.setText('A draft'); controller.setAttachments([file]); });
    select('s-B'); expect(controller.draft.text).toBe(''); expect(controller.draft.attachments).toEqual([]);
    select('s-A'); expect(controller.draft.text).toBe('A draft'); expect(controller.draft.attachments[0]).toBe(file);
    select(null); select('s-A'); expect(controller.draft.text).toBe('A draft');
    expect(JSON.parse(saved.get('argus.composer.draft.v1:s-A')!)).toEqual({ text: 'A draft', attachments: ['report.md'], request_id: controller.draft.requestId });
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

  it('clears only an acknowledged identity and retains unconfirmed drafts without restoration', () => {
    mount();act(() => controller.setText('send'));
    const original = controller.capture('s-A')!;
    act(() => renderer.unmount());mount();
    expect(controller.draft.text).toBe('send');expect(controller.draft.requestId).toBe(original.requestId);
    act(() => { controller.consume(controller.capture('s-A')!, []); });
    expect(saved.has('argus.composer.draft.v1:s-A')).toBe(false);
    act(() => controller.setText('send'));
    expect(controller.draft.requestId).not.toBe(original.requestId);
    act(() => expect(controller.consume(original, [])).toBeNull());
  });

  it('reports storage failure without keeping a second text source', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('blocked'); },
      setItem: () => { throw new Error('quota'); },
      removeItem: () => { throw new Error('blocked'); },
    });
    mount();
    expect(controller.storageError).toBe(true);
    act(() => controller.setText('copy before leaving'));
    expect(controller.draft.text).toBe('');
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

  it('clears only sent File references when its identity is confirmed', () => {
    mount();const sent = new File(['a'], 'a.txt'), remaining = new File(['b'], 'b.txt');
    act(() => {controller.setText('send');controller.setAttachments([sent,remaining]);});
    const original=controller.capture('s-A')!;
    act(() => {controller.consume(original,[sent]);});
    expect(controller.draft.text).toBe('');expect(controller.draft.attachments).toEqual([remaining]);
    act(() => expect(controller.consume(original,[sent])).toBeNull());
  });

  it('does not clear edited or deliberately emptied text on an old acknowledgement', () => {
    mount();act(() => controller.setText('original'));const original=controller.capture('s-A')!;
    act(() => {controller.setText('next');controller.setText('');});
    act(() => expect(controller.consume(original,[])).toBeNull());expect(controller.draft.text).toBe('');
  });

  it('puts the text before a rewrite back, until the rewrite is edited', () => {
    mount(); act(() => controller.setText('original'));
    let rewrite!: NonNullable<ReturnType<typeof controller.beginRewrite>>;
    act(() => { rewrite = controller.beginRewrite('s-A')!; });
    expect(controller.undoRewrite).toBeUndefined();
    act(() => { controller.finishRewrite(rewrite, 'rewritten'); });
    expect(controller.draft.text).toBe('rewritten');
    act(() => controller.undoRewrite!());
    expect(controller.draft.text).toBe('original');
    expect(controller.undoRewrite).toBeUndefined();
    act(() => { rewrite = controller.beginRewrite('s-A')!; });
    act(() => { controller.finishRewrite(rewrite, 'rewritten again'); });
    expect(controller.undoRewrite).toBeDefined();
    // Once edited, the text is the operator's own.
    act(() => controller.setText('rewritten again, edited'));
    expect(controller.undoRewrite).toBeUndefined();
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
