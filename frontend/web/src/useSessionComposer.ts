import { useCallback, useEffect, useReducer, useRef, type SetStateAction } from 'react';
import { newRequestId, type UploadedAttachment } from './api';

interface CompletedFile {
  file?: File;
  attachment: UploadedAttachment;
}

export interface SessionDraft {
  text: string;
  requestId: string;
  attachments: File[];
  revision: number;
  missingAttachments?: string[];
  uploaded?: CompletedFile[];
}
export interface DraftSnapshot extends SessionDraft { sid: string }
interface RewriteOperation extends DraftSnapshot { request: number }
const EMPTY: SessionDraft = { text: '', requestId: '', attachments: [], revision: 0 };

const DRAFT_PREFIX = 'argus.composer.draft.v1:';
const draftKey = (sid: string) => `${DRAFT_PREFIX}${sid}`;
type FileDraft = Pick<SessionDraft, 'attachments' | 'uploaded' | 'revision'>;

function isUploadedAttachment(value: unknown): value is UploadedAttachment {
  return Boolean(value && typeof value === 'object'
    && ['attachment_id', 'relative_path', 'original_name', 'stored_name', 'mime']
      .every(key => key in value && typeof Reflect.get(value, key) === 'string')
    && 'size_bytes' in value && Number.isSafeInteger(value.size_bytes) && Number(value.size_bytes) >= 0);
}

/** File bytes stay in memory; only completed references survive reloads. */
export function useSessionComposer(sid: string | null) {
  const files = useRef(new Map<string, FileDraft>());
  const rewrites = useRef(new Map<string, RewriteOperation>());
  /** Each session's text before its last rewrite, and the rewrite: undone while the draft is still the rewrite. */
  const rewritten = useRef(new Map<string, { before: string; after: string }>());
  const sequence = useRef(0);
  const mounted = useRef(true);
  const storageErrors = useRef(new Map<string, boolean>());
  const [, redraw] = useReducer((value: number) => value + 1, 0);
  const read = useCallback((id: string): SessionDraft => {
    let local = files.current.get(id);
    if (!local) {
      local = { attachments: [], uploaded: [], revision: ++sequence.current };
      files.current.set(id, local);
    }
    let text = '';
    let requestId = '';
    let missingAttachments: string[] = [];
    let uploaded: CompletedFile[] = [];
    try {
      if (!globalThis.localStorage) throw new Error('Browser storage unavailable');
      const raw = localStorage.getItem(draftKey(id));
      if (raw) {
        const saved: unknown = JSON.parse(raw);
        if (!saved || typeof saved !== 'object' || !('text' in saved) || typeof saved.text !== 'string'
          || !('attachments' in saved) || !Array.isArray(saved.attachments)
          || !saved.attachments.every(name => typeof name === 'string')) throw new Error('Invalid saved draft');
        text = saved.text;
        missingAttachments = saved.attachments;
        if ('uploaded' in saved) {
          if (!Array.isArray(saved.uploaded) || !saved.uploaded.every(isUploadedAttachment)) throw new Error('Invalid uploaded references');
          uploaded = saved.uploaded.map(attachment => ({ attachment,
            file: local.uploaded?.find(item => item.attachment.attachment_id === attachment.attachment_id)?.file,
          }));
        }
        if ('request_id' in saved && typeof saved.request_id === 'string' && saved.request_id) requestId = saved.request_id;
        else {
          const generated = newRequestId();
          localStorage.setItem(draftKey(id), JSON.stringify({ ...saved, request_id: generated }));
          requestId = generated;
        }
      }
    } catch {
      storageErrors.current.set(id, true);
    }
    return { text, requestId, uploaded, attachments: local.attachments, revision: local.revision,
      missingAttachments: missingAttachments.filter(name => !local.attachments.some(file => file.name === name)),
    };
  }, []);
  useEffect(() => {
    mounted.current = true;
    const onStorage = (event: StorageEvent) => {
      if (event.storageArea && event.storageArea !== globalThis.localStorage) return;
      const ids = event.key === null ? [...files.current.keys()]
        : event.key.startsWith(DRAFT_PREFIX) ? [event.key.slice(DRAFT_PREFIX.length)] : [];
      for (const id of ids) {
        const current = read(id);
        // Another tab can share names and uploaded references, but never File bytes.
        const names: string[] = [];
        try {
          const raw = localStorage.getItem(draftKey(id));
          if (raw) names.push(...JSON.parse(raw).attachments);
        } catch { /* read already reports invalid storage */ }
        files.current.set(id, { revision: ++sequence.current,
          attachments: current.attachments.filter(file => names.includes(file.name)
            || current.uploaded?.some(item => item.file === file)),
          uploaded: current.uploaded,
        });
        rewritten.current.delete(id);
      }
      if (ids.length) redraw();
    };
    if (typeof window !== 'undefined') window.addEventListener?.('storage', onStorage);
    return () => {
      mounted.current = false;
      if (typeof window !== 'undefined') window.removeEventListener?.('storage', onStorage);
    };
  }, [read]);

  const persist = useCallback((id: string, draft: SessionDraft) => {
    try {
      if (!globalThis.localStorage) throw new Error('Browser storage unavailable');
      const uploaded = draft.uploaded ?? [];
      const attachments = [...(draft.missingAttachments ?? []), ...draft.attachments
        .filter(file => !uploaded.some(item => item.file === file)).map(file => file.name)];
      if (draft.text || attachments.length || uploaded.length) localStorage.setItem(draftKey(id), JSON.stringify({
        text: draft.text, request_id: draft.requestId, attachments,
        ...(uploaded.length ? { uploaded: uploaded.map(item => item.attachment) } : {}),
      }));
      else localStorage.removeItem(draftKey(id));
      storageErrors.current.set(id, false);
    } catch {
      storageErrors.current.set(id, true);
    }
  }, []);
  const put = useCallback((id: string, value: Pick<SessionDraft, 'text' | 'attachments' | 'missingAttachments' | 'uploaded'>, revision = ++sequence.current): DraftSnapshot => {
    const current = read(id);
    const requestId = current.text === value.text && current.requestId ? current.requestId : newRequestId();
    const draft = { ...value, requestId, attachments: [...value.attachments], revision,
      missingAttachments: value.missingAttachments ?? [],
      uploaded: (value.uploaded ?? []).filter(item => !item.file || value.attachments.includes(item.file)) };
    files.current.set(id, { attachments: draft.attachments, uploaded: draft.uploaded, revision });
    persist(id, draft);
    if (mounted.current) redraw();
    return { sid: id, ...read(id) };
  }, [persist, read]);
  const rememberUploaded = useCallback((operation: DraftSnapshot, file: File, attachment: UploadedAttachment) => {
    const item = { file, attachment };
    operation.uploaded = [...(operation.uploaded ?? []).filter(row => row.file !== file), item];
    const current = read(operation.sid);
    if (!current?.attachments.includes(file)) return;
    const draft = { ...current, uploaded: [...(current.uploaded ?? []).filter(row => row.file !== file), item] };
    // Receipt bookkeeping is not a user edit; do not invalidate the send revision.
    put(operation.sid, draft, current.revision);
  }, [put, read]);
  const removeUploaded = useCallback((attachmentId: string) => {
    if (!sid) return;
    const current = read(sid);
    if (current) put(sid, { ...current, uploaded: current.uploaded?.filter(item => item.attachment.attachment_id !== attachmentId) });
  }, [put, read, sid]);
  const setText = useCallback((value: SetStateAction<string>) => {
    if (!sid) return;
    const current = read(sid);
    put(sid, { ...current, text: typeof value === 'function' ? value(current.text) : value });
  }, [put, read, sid]);
  const setAttachments = useCallback((value: SetStateAction<File[]>) => {
    if (!sid) return;
    const current = read(sid);
    const attachments = typeof value === 'function' ? value(current.attachments) : value;
    put(sid, { ...current, attachments, missingAttachments: current.missingAttachments?.filter(name => !attachments.some(file => file.name === name)) });
  }, [put, read, sid]);
  const capture = useCallback((id: string | null): DraftSnapshot | null => {
    if (!id) return null;
    const current = read(id);
    return { sid: id, ...current, attachments: [...current.attachments] };
  }, [read]);
  const isCurrent = useCallback((operation: DraftSnapshot) => {
    const current = read(operation.sid);
    return mounted.current && current.revision === operation.revision && current.text === operation.text;
  }, [read]);
  const consume = useCallback((operation: DraftSnapshot, sent: File[]): DraftSnapshot | null => {
    const current = read(operation.sid);
    if (!mounted.current || !operation.requestId || current.requestId !== operation.requestId) return null;
    return put(operation.sid, {
      text: '', attachments: current.attachments.filter(file => !sent.includes(file)),
      missingAttachments: current.missingAttachments,
      uploaded: current.uploaded?.filter(item => item.file && !sent.includes(item.file)),
    });
  }, [put, read]);

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
    if (current && text !== undefined) {
      rewritten.current.set(operation.sid, { before: operation.text, after: text });
      put(operation.sid, { ...operation, text });
    } else if (mounted.current) redraw();
    return current;
  }, [isCurrent, put]);
  const drop = useCallback((id: string) => {
    put(id, EMPTY);
    rewrites.current.delete(id);
    rewritten.current.delete(id);
    // Do not reset sequence: a recreated slot must never match a deleted one.
  }, [put]);

  const draft = sid ? read(sid) : EMPTY;
  const rewrite = sid ? rewritten.current.get(sid) : undefined;
  return {
    draft,
    storageError: Boolean(sid && storageErrors.current.get(sid)),
    seed: (id: string, text: string, attachments: File[] = []) => put(id, { text, attachments }),
    dismissMissingAttachments: () => {
      if (sid) put(sid, { ...read(sid), missingAttachments: [] });
    },
    rewriting: Boolean(sid && rewrites.current.has(sid)),
    /** Puts back the text as it was before the rewrite; offered until the rewrite is edited. */
    undoRewrite: rewrite && rewrite.after === draft.text ? () => {
      rewritten.current.delete(sid!);
      setText(rewrite.before);
    } : undefined,
    setText, setAttachments, capture, consume, beginRewrite, finishRewrite, drop, rememberUploaded, removeUploaded,
  };
}
