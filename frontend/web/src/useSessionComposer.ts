import { useCallback, useEffect, useReducer, useRef, type SetStateAction } from 'react';
import type { UploadedAttachment } from './api';

interface CompletedFile {
  file?: File;
  attachment: UploadedAttachment;
}

export interface SessionDraft {
  text: string;
  attachments: File[];
  revision: number;
  missingAttachments?: string[];
  uploaded?: CompletedFile[];
}
export interface DraftSnapshot extends SessionDraft { sid: string }
interface RewriteOperation extends DraftSnapshot { request: number }
const EMPTY: SessionDraft = { text: '', attachments: [], revision: 0 };

const draftKey = (sid: string) => `argus.composer.draft.v1:${sid}`;

function isUploadedAttachment(value: unknown): value is UploadedAttachment {
  return Boolean(value && typeof value === 'object'
    && ['attachment_id', 'relative_path', 'original_name', 'stored_name', 'mime']
      .every(key => key in value && typeof Reflect.get(value, key) === 'string')
    && 'size_bytes' in value && Number.isSafeInteger(value.size_bytes) && Number(value.size_bytes) >= 0);
}

/** File bytes stay in memory; only completed references survive reloads. */
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
    let uploaded: CompletedFile[] = [];
    try {
      const raw = globalThis.localStorage?.getItem(draftKey(id));
      if (raw) {
        const saved: unknown = JSON.parse(raw);
        if (!saved || typeof saved !== 'object' || !('text' in saved) || typeof saved.text !== 'string'
          || !('attachments' in saved) || !Array.isArray(saved.attachments)
          || !saved.attachments.every(name => typeof name === 'string')) throw new Error('Invalid saved draft');
        text = saved.text;
        missingAttachments = saved.attachments;
        if ('uploaded' in saved) {
          if (!Array.isArray(saved.uploaded) || !saved.uploaded.every(isUploadedAttachment)) throw new Error('Invalid uploaded references');
          uploaded = saved.uploaded.map(attachment => ({ attachment }));
        }
      }
    } catch {
      storageErrors.current.set(id, true);
    }
    const value = { text, missingAttachments, uploaded, attachments: [], revision: ++sequence.current };
    drafts.current.set(id, value);
    return value;
  };
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const persist = useCallback((id: string, draft: SessionDraft) => {
    try {
      if (!globalThis.localStorage) throw new Error('Browser storage unavailable');
      const uploaded = draft.uploaded ?? [];
      const attachments = [...(draft.missingAttachments ?? []), ...draft.attachments
        .filter(file => !uploaded.some(item => item.file === file)).map(file => file.name)];
      if (draft.text || attachments.length || uploaded.length) localStorage.setItem(draftKey(id), JSON.stringify({
        text: draft.text, attachments,
        ...(uploaded.length ? { uploaded: uploaded.map(item => item.attachment) } : {}),
      }));
      else localStorage.removeItem(draftKey(id));
      storageErrors.current.set(id, false);
    } catch {
      storageErrors.current.set(id, true);
    }
  }, []);
  const put = useCallback((id: string, value: Pick<SessionDraft, 'text' | 'attachments' | 'missingAttachments' | 'uploaded'>): DraftSnapshot => {
    const next = { text: value.text, attachments: [...value.attachments], revision: ++sequence.current };
    const draft = { ...next, missingAttachments: value.missingAttachments ?? [],
      uploaded: (value.uploaded ?? []).filter(item => !item.file || value.attachments.includes(item.file)) };
    drafts.current.set(id, draft);
    persist(id, draft);
    if (mounted.current) redraw();
    return { sid: id, ...draft };
  }, [persist]);
  const rememberUploaded = useCallback((operation: DraftSnapshot, file: File, attachment: UploadedAttachment) => {
    const item = { file, attachment };
    operation.uploaded = [...(operation.uploaded ?? []).filter(row => row.file !== file), item];
    const current = drafts.current.get(operation.sid);
    if (!current?.attachments.includes(file)) return;
    const draft = { ...current, uploaded: [...(current.uploaded ?? []).filter(row => row.file !== file), item] };
    // Receipt bookkeeping is not a user edit; do not invalidate the send revision.
    drafts.current.set(operation.sid, draft);
    persist(operation.sid, draft);
    if (mounted.current) redraw();
  }, [persist]);
  const removeUploaded = useCallback((attachmentId: string) => {
    if (!sid) return;
    const current = drafts.current.get(sid);
    if (current) put(sid, { ...current, uploaded: current.uploaded?.filter(item => item.attachment.attachment_id !== attachmentId) });
  }, [put, sid]);
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
      uploaded: operation.uploaded?.filter(item => item.file && !sent.includes(item.file)),
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
    seed: (id: string, text: string, attachments: File[] = []) => put(id, { text, attachments }),
    dismissMissingAttachments: () => {
      if (sid) put(sid, { ...(drafts.current.get(sid) ?? EMPTY), missingAttachments: [] });
    },
    rewriting: Boolean(sid && rewrites.current.has(sid)),
    setText, setAttachments, capture, consume, restore, beginRewrite, finishRewrite, drop, rememberUploaded, removeUploaded,
  };
}
