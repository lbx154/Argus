export interface MapNote {
  id: string;
  node_id: string;
  text: string;
  author?: string;
  ts: number;
}

/** Notes keyed by node, oldest first, so a card reads as a running margin. */
export function groupNotesByNode(
  notes: readonly MapNote[],
): Record<string, MapNote[]> {
  const grouped: Record<string, MapNote[]> = {};
  for (const note of notes) {
    (grouped[note.node_id] ??= []).push(note);
  }
  for (const list of Object.values(grouped)) {
    list.sort((a, b) => a.ts - b.ts || a.id.localeCompare(b.id));
  }
  return grouped;
}
