export interface PasteChunk {
  handled: boolean;
  active: boolean;
  text: string;
  pasted: boolean;
}

const START = /(?:\u001b)?\[200~/g;
const END = /(?:\u001b)?\[201~/g;

function clean(text: string): string {
  return text
    .replace(START, '')
    .replace(END, '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '');
}

/**
 * Consume one Ink input callback as bracketed/plain paste input.
 *
 * Some terminals deliver ``[200~``, body chunks, LF callbacks, then ``[201~``;
 * others deliver the whole paste once. While a bracket is active, Ink exposes
 * a bare LF as an empty, keyless callback, so that callback becomes ``\n``.
 */
export function consumePasteChunk(input: string, active: boolean): PasteChunk {
  const hasStart = /(?:\u001b)?\[200~/.test(input);
  const hasEnd = /(?:\u001b)?\[201~/.test(input);
  const multi = Array.from(input).length > 1;
  const handled = active || hasStart || hasEnd || multi;
  if (!handled) return { handled: false, active, text: input, pasted: false };

  let text = clean(input);
  if (active && !input) text = '\n';
  const nextActive = hasEnd ? false : active || hasStart;
  return {
    handled: true,
    active: nextActive,
    text,
    pasted: hasStart || hasEnd || active || multi,
  };
}
