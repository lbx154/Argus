import { errorText } from '../lib/format';
import { useEffect, useState } from 'react';
import { workspaceApi } from './workspaceApi';

export function useWorkspaceBlobUrl(sid: string, workspaceId: string, path: string) {
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    setUrl(''); setError('');
    if (!sid || !workspaceId || !path) return;
    const controller = new AbortController();
    let objectUrl = '';
    workspaceApi.rawBlob(sid, workspaceId, path, controller.signal).then((blob) => {
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    }, (caught: Error) => { if (!controller.signal.aborted) setError(errorText(caught)); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [path, sid, workspaceId]);
  return { url, error };
}
