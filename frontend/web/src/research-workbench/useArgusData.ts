import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, openEventStream } from './api';
import type { EventMsg } from './types';

function eventKey(event: EventMsg): string {
  const explicit = String(event.event_id ?? event.id ?? '');
  if (explicit) return explicit;
  const message = String(event.message_id ?? '');
  if (message) return `${event.type ?? ''}:${message}:${event.kind ?? ''}`;
  return [
    event.type ?? '',
    event.ts ?? '',
    event.agent_layer ?? event.actor ?? '',
    event.kind ?? '',
    String(event.text ?? event.title ?? event.reason ?? '').slice(0, 160),
  ].join('|');
}

function mergeEvents(current: EventMsg[], incoming: EventMsg[]): EventMsg[] {
  const rows = [...current];
  const index = new Map(rows.map((event, i) => [eventKey(event), i]));
  incoming.forEach((event) => {
    const key = eventKey(event);
    const existing = index.get(key);
    if (existing == null) {
      index.set(key, rows.length);
      rows.push(event);
    } else {
      rows[existing] = { ...rows[existing], ...event };
    }
  });
  return rows
    .sort((left, right) => Number(left.ts ?? 0) - Number(right.ts ?? 0))
    .slice(-600);
}

export function useProjects(enabled = true) {
  return useQuery({
    queryKey: ['v2-projects'],
    queryFn: ({ signal }) => api.projects(signal),
    enabled,
    refetchInterval: 10_000,
  });
}

export function useArgusData(sid: string | null, active = true) {
  const client = useQueryClient();
  const [events, setEvents] = useState<EventMsg[]>([]);
  const [connected, setConnected] = useState(false);
  const refreshTimer = useRef<number | null>(null);
  const enabled = Boolean(sid) && active;

  const snapshot = useQuery({
    queryKey: ['v2-snapshot', sid],
    queryFn: ({ signal }) => api.snapshot(sid!, signal),
    enabled,
    refetchInterval: 5_000,
  });
  const status = useQuery({
    queryKey: ['v2-status', sid],
    queryFn: ({ signal }) => api.status(sid!, signal),
    enabled,
    refetchInterval: 6_000,
  });
  const transcript = useQuery({
    queryKey: ['v2-transcript', sid],
    queryFn: ({ signal }) => api.transcript(sid!, 120, signal),
    enabled,
    refetchInterval: 8_000,
  });
  const artifacts = useQuery({
    queryKey: ['v2-artifacts', sid],
    queryFn: ({ signal }) => api.artifacts(sid!, signal),
    enabled,
    refetchInterval: 8_000,
  });
  const counterexamples = useQuery({
    queryKey: ['v2-counterexamples', sid],
    queryFn: ({ signal }) => api.counterexamples(sid!, signal),
    enabled,
    refetchInterval: 3_000,
  });
  const gitDiff = useQuery({
    queryKey: ['v2-git-diff', sid],
    queryFn: ({ signal }) => api.gitDiff(sid!, signal),
    enabled,
    refetchInterval: 8_000,
  });
  const journal = useQuery({
    queryKey: ['v2-journal', sid],
    queryFn: ({ signal }) => api.journal(sid!, 80, signal),
    enabled,
    refetchInterval: 10_000,
  });
  const eventSeed = useQuery({
    queryKey: ['v2-events', sid],
    queryFn: ({ signal }) => api.events(sid!, 220, signal),
    enabled,
    refetchInterval: 15_000,
  });

  useEffect(() => {
    setEvents([]);
    setConnected(false);
  }, [sid]);

  useEffect(() => {
    if (!sid || !eventSeed.data) return;
    setEvents((current) => mergeEvents(current, eventSeed.data));
  }, [eventSeed.data, sid]);

  useEffect(() => {
    if (!sid || !active) {
      setConnected(false);
      return;
    }
    const stream = openEventStream(
      sid,
      (event) => {
        setEvents((current) => mergeEvents(current, [event]));
        if (refreshTimer.current == null) {
          refreshTimer.current = window.setTimeout(() => {
            refreshTimer.current = null;
            void client.invalidateQueries({ queryKey: ['v2-snapshot', sid] });
            void client.invalidateQueries({ queryKey: ['v2-status', sid] });
            const type = String(event.type ?? '');
            if (/artifact|review.completed|mission.completed|live_view/.test(type)) {
              void client.invalidateQueries({ queryKey: ['v2-artifacts', sid] });
              void client.invalidateQueries({ queryKey: ['v2-counterexamples', sid] });
            }
            if (/ui\.|manager/.test(type)) {
              void client.invalidateQueries({ queryKey: ['v2-transcript', sid] });
            }
          }, 900);
        }
      },
      setConnected,
    );
    return () => {
      stream.close();
      setConnected(false);
      if (refreshTimer.current != null) {
        window.clearTimeout(refreshTimer.current);
        refreshTimer.current = null;
      }
    };
  }, [active, client, sid]);

  const refresh = async () => {
    if (!sid) return;
    await Promise.all([
      client.invalidateQueries({ queryKey: ['v2-snapshot', sid] }),
      client.invalidateQueries({ queryKey: ['v2-status', sid] }),
      client.invalidateQueries({ queryKey: ['v2-transcript', sid] }),
      client.invalidateQueries({ queryKey: ['v2-artifacts', sid] }),
      client.invalidateQueries({ queryKey: ['v2-counterexamples', sid] }),
      client.invalidateQueries({ queryKey: ['v2-git-diff', sid] }),
      client.invalidateQueries({ queryKey: ['v2-journal', sid] }),
      client.invalidateQueries({ queryKey: ['v2-events', sid] }),
    ]);
  };

  const start = useMutation({
    mutationFn: () => api.startDaemon(sid!, snapshot.data?.daemon_commands?.revision),
    onSuccess: refresh,
  });
  const stop = useMutation({
    mutationFn: (drain: boolean) =>
      api.stopDaemon(sid!, drain, snapshot.data?.daemon_commands?.revision),
    onSuccess: refresh,
  });

  const error = useMemo(() => {
    const failed = [snapshot, status, transcript, artifacts, counterexamples, gitDiff, journal, eventSeed]
      .find((query) => query.error);
    return failed?.error instanceof Error ? failed.error : null;
  }, [artifacts, counterexamples, eventSeed, gitDiff, journal, snapshot, status, transcript]);

  return {
    snapshot,
    status,
    transcript,
    artifacts,
    counterexamples,
    gitDiff,
    journal,
    events,
    connected,
    refresh,
    controls: { start, stop },
    error,
  };
}
