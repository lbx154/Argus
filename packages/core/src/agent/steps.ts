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

/** A tool call: the command it runs, or the file a tool for writing changes. */
export function used(name: unknown, args: unknown): Step | null {
  if (!isJsonObject(args)) return null;
  if (typeof args.command === 'string') return { kind: 'command', text: args.command };
  const path = args.path ?? args.file_path ?? args.notebook_path;
  return typeof path === 'string' && /edit|write|create|replace|patch/i.test(String(name)) ? { kind: 'edit', text: path } : null;
}
