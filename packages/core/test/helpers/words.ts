import type { EventMsg } from '@argus/contracts';

/** A reply's words: those said to the operator, then those kept folded as its process. */
export const words = (row?: EventMsg): string[] => [...row?.steps as string[] | undefined ?? [], ...row?.process as string[] | undefined ?? []];
