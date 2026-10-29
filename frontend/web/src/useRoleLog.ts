import { useQuery } from '@tanstack/react-query';
import type { EventMsg } from './api';

export interface RoleLog { round: number; events: EventMsg[] }
export const roleLogKey = (sid: string | null) => ['role-log', sid] as const;
const EMPTY: RoleLog = { round: 0, events: [] };

/** Populated only by the existing rows stream, independently of project snapshots. */
export function useRoleLog(sid: string | null) {
  return useQuery<RoleLog>({
    queryKey: roleLogKey(sid), enabled: false, initialData: EMPTY,
    staleTime: Infinity, gcTime: Infinity,
  }).data;
}
