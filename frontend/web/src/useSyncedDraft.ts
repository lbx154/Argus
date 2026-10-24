import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type BrowserDraftValue, type BrowserState } from './api';
import type { DraftSnapshot, SessionDraft } from './useSessionComposer';
import { errorText } from './lib/format';

const empty = (): BrowserDraftValue => ({ text: '', attachments: [], uploaded: [] });
function serialized(draft: SessionDraft): BrowserDraftValue {
  return {
    text: draft.text,
    attachments: [...(draft.missingAttachments ?? []), ...draft.attachments
      .filter(file => !draft.uploaded?.some(item => item.file === file)).map(file => file.name)],
    uploaded: draft.uploaded?.map(item => item.attachment) ?? [],
  };
}
function remoteValue(state: BrowserState): BrowserDraftValue {
  return { ...empty(), ...state.value };
}

export function useSyncedDraft(
  sid: string | null,
  draft: SessionDraft,
  capture: (id: string | null) => DraftSnapshot | null,
  replace: (id: string, value: BrowserDraftValue) => void,
) {
  const [sync, setSync] = useState<{ sid: string | null; status: 'loading' | 'ready' | 'saving' | 'conflict' | 'error'; revision: number; error?: string; remote?: BrowserDraftValue }>({ sid: null, status: 'loading', revision: 0 });
  const lastSaved = useRef(new Map<string, string>());
  const inFlight = useRef(new Map<string, Promise<void>>());
  const callbacks = useRef({ capture, replace });
  callbacks.current = { capture, replace };
  const [attempt, retry] = useState(0);
  const syncRef = useRef(sync);
  syncRef.current = sync;
  const payload = JSON.stringify(serialized(draft));
  useEffect(() => {
    const focus = () => { if (syncRef.current.status !== 'saving') retry(value => value + 1); };
    window.addEventListener('focus', focus);
    return () => window.removeEventListener('focus', focus);
  }, []);

  useEffect(() => {
    if (!sid) return;
    let cancelled = false;
    setSync({ sid, status: 'loading', revision: 0 });
    const load = async () => {
      await inFlight.current.get(sid);
      return api.browserDraft(sid);
    };
    void load().then(result => {
      if (cancelled) return;
      const remote = remoteValue(result);
      const local = serialized(callbacks.current.capture(sid)!);
      const baseline = lastSaved.current.get(sid);
      const remoteText = JSON.stringify(remote);
      const localText = JSON.stringify(local);
      const localHasContent = local.text || local.attachments.length || local.uploaded.length;
      const locallyChanged = baseline !== undefined && localText !== baseline;
      const remotelyChanged = baseline !== undefined && remoteText !== baseline;
      if (localText !== remoteText && (baseline === undefined ? localHasContent && result.revision > 0 : locallyChanged && remotelyChanged)) {
        setSync({ sid, status: 'conflict', revision: result.revision, remote });
      } else {
        lastSaved.current.set(sid, remoteText);
        if (baseline === undefined ? !localHasContent : !locallyChanged && remotelyChanged) callbacks.current.replace(sid, remote);
        setSync({ sid, status: 'ready', revision: result.revision });
      }
    }).catch(error => {
      if (!cancelled) setSync({ sid, status: 'error', revision: 0, error: errorText(error) });
    });
    return () => { cancelled = true; };
  }, [sid, attempt]);

  useEffect(() => {
    if (!sid || sync.sid !== sid || sync.status !== 'ready' || payload === lastSaved.current.get(sid)) return;
    const timer = window.setTimeout(() => {
      setSync(current => ({ ...current, status: 'saving' }));
      const save = api.saveBrowserDraft(sid, JSON.parse(payload) as BrowserDraftValue, sync.revision).then(result => {
        lastSaved.current.set(sid, payload);
        setSync(current => current.sid === sid && current.status === 'saving' ? { sid, status: 'ready', revision: result.revision } : current);
      }).catch(async error => {
        if (error && typeof error === 'object' && 'status' in error && error.status === 409) {
          try {
            const remote = await api.browserDraft(sid);
            setSync(current => current.sid === sid && current.status === 'saving' ? { sid, status: 'conflict', revision: remote.revision, remote: remoteValue(remote) } : current);
          } catch (readError) {
            setSync(current => current.sid === sid && current.status === 'saving' ? { ...current, status: 'error', error: errorText(readError) } : current);
          }
        } else {
          setSync(current => current.sid === sid && current.status === 'saving' ? { ...current, status: 'error', error: errorText(error) } : current);
        }
      });
      inFlight.current.set(sid, save);
      void save.then(() => { if (inFlight.current.get(sid) === save) inFlight.current.delete(sid); });
    }, 600);
    return () => window.clearTimeout(timer);
  }, [sid, payload, sync]);

  const resolve = useCallback((useRemote: boolean) => {
    if (!sid || sync.sid !== sid || !sync.remote) return;
    if (useRemote) {
      callbacks.current.replace(sid, sync.remote);
    }
    lastSaved.current.set(sid, JSON.stringify(sync.remote));
    setSync(current => ({ ...current, status: 'ready', remote: undefined }));
  }, [sid, sync]);
  return {
    status: sync.sid === sid ? sync.status : 'loading',
    pending: payload !== lastSaved.current.get(sid ?? ''),
    error: sync.error,
    resolve,
    retry: () => retry(value => value + 1),
  };
}
