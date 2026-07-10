import type { ProjectRow } from './api.js';
import {
  resolveProjectSelection,
  type ProjectSelection,
} from '../../core/src/projects.js';

export function initialProjectSelection(
  projects: ProjectRow[],
  requested?: string,
): ProjectSelection {
  return resolveProjectSelection(projects, requested);
}

export type InteractiveStartup =
  | { kind: 'fresh' }
  | { kind: 'pick' }
  | { kind: 'resume'; project: string };

/**
 * A plain interactive ``argus`` launch is a new conversation, never an
 * implicit resume of whichever daemon happened to be active most recently.
 * Resuming is explicit via ``--project`` at launch or ``/resume`` in-app.
 */
export function interactiveStartup(requested?: string, pick = false): InteractiveStartup {
  const project = requested?.trim() || '';
  if (project) return { kind: 'resume', project };
  return pick ? { kind: 'pick' } : { kind: 'fresh' };
}

/** Backward-compatible convenience for callers that only need the ID. */
export function initialProjectId(projects: ProjectRow[], requested?: string): string | null {
  return initialProjectSelection(projects, requested).id;
}
