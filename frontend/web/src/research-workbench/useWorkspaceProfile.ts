import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { workspaceApi } from './workspaceApi';
import { readLocalStorage, writeLocalStorage } from '../lib/storage';

export function useWorkspaceProfile(sid: string, storageScope: string) {
  const profiles = useQuery({
    queryKey: ['workspace-profiles', sid],
    queryFn: ({ signal }) => workspaceApi.profiles(sid, signal),
    staleTime: 10_000,
  });
  const storageKey = `argus-v2-workspace-profile:${storageScope}:${sid}`;
  const [workspaceId, setWorkspaceIdState] = useState(() => readLocalStorage(storageKey) || '');
  const active = useMemo(() => {
    const rows = profiles.data?.profiles ?? [];
    return rows.find((row) => row.id === workspaceId)
      ?? rows.find((row) => row.id === profiles.data?.default_id)
      ?? rows.find((row) => row.canonical)
      ?? rows[0]
      ?? null;
  }, [profiles.data, workspaceId]);
  useEffect(() => {
    if (active && active.id !== workspaceId) setWorkspaceIdState(active.id);
  }, [active, workspaceId]);
  const setWorkspaceId = (value: string) => {
    setWorkspaceIdState(value);
    writeLocalStorage(storageKey, value);
  };
  return { profiles, active, workspaceId: active?.id ?? '', setWorkspaceId };
}
