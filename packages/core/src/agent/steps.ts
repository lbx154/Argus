// The steps of a role's turn, read from its backend's stream: what it says, the commands it runs,
// the files it changes. What only reads is no step, and neither is the form the turn ends with.

import { isJsonObject } from '@argus/contracts';
import type { Step } from '../types.js';

/** A message of the role's; the form, the JSON object its turn ends with, is none. */
export function said(text: unknown): Step | null {
  const words = typeof text === 'string' ? text.trim() : '';
  try { if (isJsonObject(JSON.parse(words))) return null; } catch { /* words, not the form */ }
  return words ? { kind: 'message', text: words } : null;
}

/** The backend's summary of what the role is thinking, its parts in order. */
export function thought(summary: unknown): Step | null {
  const text = Array.isArray(summary) ? summary.filter(part => typeof part === 'string').join('\n\n').trim() : '';
  return text ? { kind: 'thought', text } : null;
}

/** The words of a message being written, as far as they have come: of the form, its letter's. */
export function writing(text: string): string {
  if (!text.trimStart().startsWith('{')) return text;
  const letter = /"letter"\s*:\s*"((?:[^"\\]|\\.)*)/.exec(text)?.[1];
  // A \u escape cut short is left for the next words.
  try { return letter === undefined ? '' : JSON.parse(`"${letter.replace(/\\u[0-9a-fA-F]{0,3}$/, '')}"`) as string; } catch { return ''; }
}

/** A tool call: the command it runs, or the file a tool for writing changes. */
export function used(name: unknown, args: unknown): Step | null {
  if (!isJsonObject(args)) return null;
  if (typeof args.command === 'string') return { kind: 'command', text: args.command };
  const path = args.path ?? args.file_path ?? args.notebook_path;
  return typeof path === 'string' && /edit|write|create|replace|patch/i.test(String(name)) ? { kind: 'edit', text: path } : null;
}
