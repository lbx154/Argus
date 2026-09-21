import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { api } from "../api";
import type { Dataset, LineNote } from "./model";

export type MapLines = { lines: LineNote[]; available: boolean; retry_after?: number };

const SETTLED = new Set(["done", "failed", "cancelled", "recorded", "skipped", "aborted", "superseded"]);

/** What the lines between tasks say.
 *
 * `pairs` are the lines this map draws that have nothing to say yet beyond
 * their kind. Saved notes are read at once; when writing is allowed the server
 * is then asked, once for this set of lines, to write the missing ones. A line
 * whose tasks have nothing to do with each other stays without a note, and the
 * server remembers that it was asked. */
export function useMapLines(
  data: Dataset,
  pairs: Array<{ source: string; target: string }>,
  zh: boolean,
  allowGeneration: boolean,
  sessionId?: string,
): LineNote[] {
  const locale = zh ? "zh-CN" : "en-US";
  const source = data.kind === "live" ? "project" : "dataset";
  const name = data.id.replace(/^live:/, "");
  // A note is written against what its two tasks have settled into, so a
  // task finishing is a reason to ask again and a progress event is not.
  const signature = useMemo(() => {
    const settled = new Map(data.tasks.map((task) => [task.id, SETTLED.has(task.status || "")]));
    const known = pairs.filter((pair) => settled.has(pair.source) && settled.has(pair.target)).slice(0, 48);
    return JSON.stringify(known.map((pair) => [pair.source, pair.target, settled.get(pair.source), settled.get(pair.target)]));
  }, [data.tasks, pairs]);
  const body = useMemo(
    () => (JSON.parse(signature) as Array<[string, string]>).map(([s, t]) => ({ source: s, target: t })),
    [signature],
  );
  const scope = ["map-lines", source, name, locale, sessionId, signature];
  const saved = useQuery({
    queryKey: [...scope, "saved"],
    queryFn: ({ signal }) => api.mapLines(source, name, { pairs: body, locale, write: false }, signal, sessionId),
    enabled: body.length > 0,
    staleTime: Infinity,
    gcTime: 2 * 60 * 60 * 1000,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const written = useQuery({
    queryKey: [...scope, "written"],
    queryFn: ({ signal }) => api.mapLines(source, name, { pairs: body, locale, write: true }, signal, sessionId),
    enabled: body.length > 0 && allowGeneration && saved.data?.available === true
      && saved.data.lines.length < body.length,
    staleTime: Infinity,
    gcTime: 2 * 60 * 60 * 1000,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  return (written.data ?? saved.data)?.lines ?? EMPTY;
}

const EMPTY: LineNote[] = [];
