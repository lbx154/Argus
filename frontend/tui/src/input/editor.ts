/**
 * Pure line editor — cursor + edit ops over Unicode CODE POINTS (so CJK and
 * astral chars move/delete as one unit, never split a surrogate pair). No Ink,
 * no React; fully unit-testable. The render layer turns {value, cursor} into a
 * visible caret.
 */

export interface Edit {
  value: string;
  cursor: number; // index into the code-point array, 0..len
}

export const EMPTY: Edit = { value: '', cursor: 0 };

const cp = (s: string): string[] => Array.from(s);

function make(chars: string[], cursor: number): Edit {
  const c = Math.max(0, Math.min(cursor, chars.length));
  return { value: chars.join(''), cursor: c };
}

export function fromString(value: string, cursor = Array.from(value).length): Edit {
  return make(cp(value), cursor);
}

export function insert(e: Edit, text: string): Edit {
  if (!text) return e;
  const chars = cp(e.value);
  const ins = cp(text);
  chars.splice(e.cursor, 0, ...ins);
  return make(chars, e.cursor + ins.length);
}

export function backspace(e: Edit): Edit {
  if (e.cursor <= 0) return e;
  const chars = cp(e.value);
  chars.splice(e.cursor - 1, 1);
  return make(chars, e.cursor - 1);
}

export function deleteForward(e: Edit): Edit {
  const chars = cp(e.value);
  if (e.cursor >= chars.length) return e;
  chars.splice(e.cursor, 1);
  return make(chars, e.cursor);
}

export function left(e: Edit): Edit {
  return e.cursor <= 0 ? e : make(cp(e.value), e.cursor - 1);
}

export function right(e: Edit): Edit {
  const len = cp(e.value).length;
  return e.cursor >= len ? e : make(cp(e.value), e.cursor + 1);
}

export function home(e: Edit): Edit {
  return make(cp(e.value), 0);
}

export function end(e: Edit): Edit {
  const chars = cp(e.value);
  return make(chars, chars.length);
}

function isWordChar(ch: string): boolean {
  return /\S/.test(ch);
}

/** Emacs-style Ctrl-W: delete the whitespace-then-word run before the cursor. */
export function deleteWordBefore(e: Edit): Edit {
  const chars = cp(e.value);
  let i = e.cursor;
  while (i > 0 && !isWordChar(chars[i - 1])) i--; // eat trailing spaces
  while (i > 0 && isWordChar(chars[i - 1])) i--; // eat the word
  if (i === e.cursor) return e;
  chars.splice(i, e.cursor - i);
  return make(chars, i);
}

/** Ctrl-U: kill from the start of line to the cursor. */
export function killToStart(e: Edit): Edit {
  if (e.cursor <= 0) return e;
  const chars = cp(e.value);
  chars.splice(0, e.cursor);
  return make(chars, 0);
}

/** Ctrl-K: kill from cursor to end of line. */
export function killToEnd(e: Edit): Edit {
  const chars = cp(e.value);
  if (e.cursor >= chars.length) return e;
  chars.splice(e.cursor);
  return make(chars, e.cursor);
}

/** Split for rendering a caret: text before / char under caret / text after. */
export function caretSplit(e: Edit): { before: string; at: string; after: string } {
  const chars = cp(e.value);
  return {
    before: chars.slice(0, e.cursor).join(''),
    at: chars[e.cursor] ?? '',
    after: chars.slice(e.cursor + 1).join(''),
  };
}
