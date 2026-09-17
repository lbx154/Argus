import { useCallback, useEffect, useReducer, useRef, type SetStateAction } from 'react';

export interface SessionDraft {
  text: string;
  attachments: File[];
  revision: number;
}
export interface DraftSnapshot extends SessionDraft { sid: string }
interface RewriteOperation extends DraftSnapshot { request: number }
const EMPTY: SessionDraft = { text: '', attachments: [], revision: 0 };

/** Main/map drafts share one in-memory slot per session. File objects never enter
 * storage. Revisions, not text equality or filenames, fence asynchronous writes. */
export function useSessionComposer(sid: string | null) {
  const drafts = useRef(new Map<string, SessionDraft>());
  const rewrites = useRef(new Map<string, RewriteOperation>());
  const sequence = useRef(0);
  const mounted = useRef(true);
  const [, redraw] = useReducer((value: number) => value + 1, 0);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const put = useCallback((id: string, value: Pick<SessionDraft, 'text' | 'attachments'>): DraftSnapshot => {
    const next = { text: value.text, attachments: [...value.attachments], revision: ++sequence.current };
    drafts.current.set(id, next);
    if (mounted.current) redraw();
    return { sid: id, ...next };
  }, []);
  const setText = useCallback((value: SetStateAction<string>) => {
    if (!sid) return;
    const current = drafts.current.get(sid) ?? EMPTY;
    put(sid, { ...current, text: typeof value === 'function' ? value(current.text) : value });
  }, [put, sid]);
  const setAttachments = useCallback((value: SetStateAction<File[]>) => {
    if (!sid) return;
    const current = drafts.current.get(sid) ?? EMPTY;
    put(sid, { ...current, attachments: typeof value === 'function' ? value(current.attachments) : value });
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
    drafts.current.delete(id);
    rewrites.current.delete(id);
    // Do not reset sequence: a recreated slot must never match a deleted one.
    if (mounted.current) redraw();
  }, []);

  return {
    draft: (sid ? drafts.current.get(sid) : null) ?? EMPTY,
    rewriting: Boolean(sid && rewrites.current.has(sid)),
    setText, setAttachments, capture, consume, restore, beginRewrite, finishRewrite, drop,
  };
}
