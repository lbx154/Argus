// One sentence in place of a raw block. The modals used to print a daemon log
// tail, a metrics JSON dump and CLI output straight onto the page; these
// helpers pick the line or the few numbers a person actually reads, so the
// raw text can wait behind a disclosure.


const LINE_PREFIX = [
  // 2026-09-11 22:07:01,123 · 2026-09-11T22:07:01Z · [2026-09-11 22:07:01]
  /^\[?\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\]?\s*/,
  // INFO: · [WARNING] · ERROR -
  /^\[?(?:INFO|WARNING|WARN|ERROR|DEBUG|CRITICAL|TRACE)\]?\s*[:\-|]?\s*/i,
  // logger name or pid
  /^(?:\[\d+\]|[\w.]+:)\s+/,
];

/** A log line without its timestamp, level and logger name. */
export function plainLogLine(line: string): string {
  let text = line.trim();
  for (const prefix of LINE_PREFIX) text = text.replace(prefix, '');
  return text.trim();
}

const clip = (text: string, n: number) => (text.length <= n ? text : `${text.slice(0, n - 1).trimEnd()}…`);

/** The last line of a log tail that says something, trimmed to one line of the page. */
export function lastMeaningfulLine(text: string, limit = 200): string {
  const lines = String(text ?? '').split(/\r?\n/).map(plainLogLine).filter((line) => line && !/^[-=_.\s]+$/.test(line));
  return lines.length ? clip(lines[lines.length - 1], limit) : '';
}

/** Command output at a glance: its first line that says something, and how long it is. */
export function outputSummary(text: string, limit = 160): { first: string; lines: number } {
  const lines = String(text ?? '').split(/\r?\n/).filter((line) => line.trim());
  return { first: lines.length ? clip(lines[0].trim(), limit) : '', lines: lines.length };
}
