import { useCallback, useEffect, useReducer, useRef, type SetStateAction } from 'react';

export interface SessionDraft {
  text: string;
  attachments: File[];
  revision: number;
  missingAttachments?: string[];
}
export interface DraftSnapshot extends SessionDraft { sid: string }
interface RewriteOperation extends DraftSnapshot { request: number }
const EMPTY: SessionDraft = { text: '', attachments: [], revision: 0 };

const draftKey = (sid: string) => `argus.composer.draft.v1:${sid}`;

/** File bytes stay in memory. Only text and reminders about unuploaded files
 * survive reloads; revisions still fence asynchronous writes. */
export function useSessionComposer(sid: string | null) {
  const drafts = useRef(new Map<string, SessionDraft>());
  const rewrites = useRef(new Map<string, RewriteOperation>());
  const sequence = useRef(0);
  const mounted = useRef(true);
  const storageErrors = useRef(new Map<string, boolean>());
  const [, redraw] = useReducer((value: number) => value + 1, 0);
  const read = (id: string): SessionDraft => {
    const cached = drafts.current.get(id);
    if (cached) return cached;
    let text = '';
    let missingAttachments: string[] = [];
    try {
      const raw = globalThis.localStorage?.getItem(draftKey(id));
      if (raw) {
        const saved: unknown = JSON.parse(raw);
        if (!saved || typeof saved !== 'object' || !('text' in saved) || typeof saved.text !== 'string'
          || !('attachments' in saved) || !Array.isArray(saved.attachments)
          || !saved.attachments.every(name => typeof name === 'string')) throw new Error('Invalid saved draft');
        text = saved.text;
        missingAttachments = saved.attachments;
      }
    } catch {
      storageErrors.current.set(id, true);
    }
    const value = { text, missingAttachments, attachments: [], revision: ++sequence.current };
    drafts.current.set(id, value);
    return value;
  };
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const put = useCallback((id: string, value: Pick<SessionDraft, 'text' | 'attachments' | 'missingAttachments'>): DraftSnapshot => {
    const next = { text: value.text, attachments: [...value.attachments], revision: ++sequence.current };
    const draft = { ...next, missingAttachments: value.missingAttachments ?? [] };
    drafts.current.set(id, draft);
    try {
      if (!globalThis.localStorage) throw new Error('Browser storage unavailable');
      const attachments = [...draft.missingAttachments, ...draft.attachments.map(file => file.name)];
      if (draft.text || attachments.length) localStorage.setItem(draftKey(id), JSON.stringify({ text: draft.text, attachments }));
      else localStorage.removeItem(draftKey(id));
      storageErrors.current.set(id, false);
    } catch {
      storageErrors.current.set(id, true);
    }
    if (mounted.current) redraw();
    return { sid: id, ...draft };
  }, []);
  const setText = useCallback((value: SetStateAction<string>) => {
    if (!sid) return;
    const current = drafts.current.get(sid) ?? EMPTY;
    put(sid, { ...current, text: typeof value === 'function' ? value(current.text) : value });
  }, [put, sid]);
  const setAttachments = useCallback((value: SetStateAction<File[]>) => {
    if (!sid) return;
    const current = drafts.current.get(sid) ?? EMPTY;
    const attachments = typeof value === 'function' ? value(current.attachments) : value;
    put(sid, { ...current, attachments, missingAttachments: current.missingAttachments?.filter(name => !attachments.some(file => file.name === name)) });
  }, [put, sid]);
  const capture = useCallback((id: string | null): DraftSnapshot | null => {
    if (!id) return null;
    // Materialize even an empty slot so dropping it invalidates captured work.
    let current = drafts.current.get(id);
    if (!current) {
      current = { ...EMPTY, attachments: [], revision: ++sequence.current };
      drafts.current.set(id, current);
    }
    return { sid: id, ...current, attachments: [...current.attachments] };
  }, []);
  const isCurrent = useCallback((operation: DraftSnapshot) => mounted.current
    && drafts.current.get(operation.sid)?.revision === operation.revision, []);
  const consume = useCallback((operation: DraftSnapshot, sent: File[]): DraftSnapshot | null => {
    if (!isCurrent(operation)) return null;
    return put(operation.sid, {
      text: '', attachments: operation.attachments.filter(file => !sent.includes(file)),
      missingAttachments: operation.missingAttachments,
    });
  }, [isCurrent, put]);
  const restore = useCallback((cleared: DraftSnapshot, original: DraftSnapshot) => {
    if (cleared.sid !== original.sid || !isCurrent(cleared)) return false;
    put(original.sid, original);
    return true;
  }, [isCurrent, put]);

  const beginRewrite = useCallback((id: string | null) => {
    if (!id || rewrites.current.has(id)) return null;
    const draft = capture(id)!;
    const operation = { ...draft, request: ++sequence.current };
    rewrites.current.set(id, operation);
    redraw();
    return operation;
  }, [capture]);
  const finishRewrite = useCallback((operation: RewriteOperation, text?: string) => {
    if (rewrites.current.get(operation.sid) !== operation) return false;
    rewrites.current.delete(operation.sid);
    const current = isCurrent(operation);
    if (current && text !== undefined) put(operation.sid, { ...operation, text });
    else if (mounted.current) redraw();
    return current;
  }, [isCurrent, put]);
  const drop = useCallback((id: string) => {
    drafts.current.set(id, { ...EMPTY, attachments: [], revision: ++sequence.current });
    rewrites.current.delete(id);
    try {
      if (!globalThis.localStorage) throw new Error('Browser storage unavailable');
      localStorage.removeItem(draftKey(id));
      storageErrors.current.delete(id);
    } catch { storageErrors.current.set(id, true); }
    // Do not reset sequence: a recreated slot must never match a deleted one.
    if (mounted.current) redraw();
  }, []);

  return {
    draft: sid ? read(sid) : EMPTY,
    storageError: Boolean(sid && storageErrors.current.get(sid)),
    seed: (id: string, text: string) => put(id, { text, attachments: [] }),
    dismissMissingAttachments: () => {
      if (sid) put(sid, { ...(drafts.current.get(sid) ?? EMPTY), missingAttachments: [] });
    },
    rewriting: Boolean(sid && rewrites.current.has(sid)),
    setText, setAttachments, capture, consume, restore, beginRewrite, finishRewrite, drop,
  };
}
