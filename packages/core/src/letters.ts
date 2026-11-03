// Letters a role writes while its turn goes on, without ending it. Each session has a letterbox, an
// address on this server only it is told (ARGUS_LETTERS in its environment); what it posts there goes
// to the readers it names, and a copy to those it copies, as the work goes on.

import { createHmac, randomBytes } from 'node:crypto';

/** Delivers `text` to each reader named; the readers it reached, or throws saying why it cannot. */
export type Post = (to: string[], cc: string[], text: string) => Promise<string[]>;

const secret = randomBytes(32);
const boxes = new Map<string, Post>();
let base: string | null = null;

/** Where the server takes letters: set once it listens; till then no session has a letterbox. */
export function lettersAt(url: string | null): void { base = url; }

/** The address of the letterbox of session `key`, the same for the session's life in this process
 * (its CLI keeps the address it started with), which now delivers with `post`. */
export function letterbox(key: string, post: Post): string | undefined {
  if (!base) return undefined;
  const token = createHmac('sha256', secret).update(key).digest('hex').slice(0, 32);
  boxes.set(token, post);
  return `${base}/letters/${token}`;
}

/** Whether sessions have letterboxes: in a server, once it listens. */
export const lettersOn = (): boolean => base !== null;

/** The letterbox an address names, or none. */
export const boxAt = (token: string): Post | undefined => boxes.get(token);

/** The readers a letterbox address lists in `name` (to or cc), comma-separated. */
export const readers = (value: string | null): string[] => (value ?? '').split(',').map(reader => reader.trim().toLowerCase()).filter(Boolean);
