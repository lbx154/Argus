import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { api } from "../api";
import type { Dataset } from "./model";

export type CardWords = { title: string; summary: string };
export type MapCardWords = { cards: Record<string, CardWords>; available: boolean; retry_after?: number };

const SETTLED = new Set(["done", "failed", "cancelled", "recorded", "skipped", "aborted", "superseded"]);
const EMPTY: Record<string, CardWords> = {};

/** A short title and a sentence for every task's card.
 *
 * A task's full explanation is written when a reader opens it. Until then its
 * card would show the planner's own text, so the server is asked, once for the
 * map, for a few words per task. Saved words are read at once; when writing is
 * allowed the missing ones are then written. A reader's own turns are not
 * rewritten: their cards already say what the reader asked, in their words. */
export function useMapWords(
  data: Dataset,
  zh: boolean,
  allowGeneration: boolean,
  sessionId?: string,
): Record<string, CardWords> {
  const locale = zh ? "zh-CN" : "en-US";
  const source = data.kind === "live" ? "project" : "dataset";
  const name = data.id.replace(/^live:/, "");
  // Words are written against what a task has settled into, so a task
  // finishing is a reason to ask again and a progress event is not.
  const signature = useMemo(
    () => JSON.stringify(data.tasks
      .filter((task) => task.kind !== "turn" && task.turn_kind !== "qa")
      .slice(-40)
      .map((task) => [task.id, SETTLED.has(task.status || "")])),
    [data.tasks],
  );
  const tasks = useMemo(() => (JSON.parse(signature) as Array<[string, boolean]>).map(([id]) => id), [signature]);
  const scope = ["map-card-words", source, name, locale, sessionId, signature];
  const saved = useQuery({
    queryKey: [...scope, "saved"],
    queryFn: ({ signal }) => api.mapCardWords(source, name, { tasks, locale, write: false }, signal, sessionId),
    enabled: tasks.length > 0,
    staleTime: Infinity,
    gcTime: 2 * 60 * 60 * 1000,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const written = useQuery({
    queryKey: [...scope, "written"],
    queryFn: ({ signal }) => api.mapCardWords(source, name, { tasks, locale, write: true }, signal, sessionId),
    enabled: tasks.length > 0 && allowGeneration && saved.data?.available === true
      && Object.keys(saved.data.cards).length < tasks.length,
    staleTime: Infinity,
    gcTime: 2 * 60 * 60 * 1000,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  return (written.data ?? saved.data)?.cards ?? EMPTY;
}
