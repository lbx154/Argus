/** Python accepts arbitrary integers; the Node wire contract uses exact numbers. */
export class UsageAccountingError extends Error {
  constructor(message: string) { super(message); this.name = 'UsageAccountingError'; }
}

export function exactCount(value: number): number {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new UsageAccountingError('Token count exceeds the nonnegative safe-integer range.');
  }
  return value;
}

// Python int/float also accept Unicode decimal digits and underscores between digits.
function numericText(value: string): string {
  return value.trim().replace(/\p{Decimal_Number}/gu, digit => {
    let start = digit.codePointAt(0)!;
    while (/\p{Decimal_Number}/u.test(String.fromCodePoint(start - 1))) start -= 1;
    return String((digit.codePointAt(0)! - start) % 10);
  });
}

export function tokenCount(value: unknown): number {
  if (typeof value === 'number') {
    if (Number.isNaN(value)) return 0;
    if (!Number.isFinite(value)) throw new UsageAccountingError('Token count must be finite.');
    return exactCount(Math.max(0, Math.trunc(value)));
  }
  if (typeof value !== 'string') return 0;
  const raw = numericText(value);
  if (!/^[+-]?[0-9](?:_?[0-9])*$/.test(raw)) return 0;
  const integer = BigInt(raw.replaceAll('_', ''));
  if (integer <= 0n) return 0;
  if (integer > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new UsageAccountingError('Token count exceeds the nonnegative safe-integer range.');
  }
  return Number(integer);
}

export function nonnegativeFloat(value: unknown): number | null {
  if (typeof value !== 'number' && typeof value !== 'string') return null;
  let parsed: number;
  if (typeof value === 'string') {
    const raw = numericText(value);
    if (!/^[+-]?(?:[0-9](?:_?[0-9])*(?:\.(?:[0-9](?:_?[0-9])*)?)?|\.[0-9](?:_?[0-9])*)(?:[eE][+-]?[0-9](?:_?[0-9])*)?$/.test(raw)) return null;
    parsed = Number(raw.replaceAll('_', ''));
  } else parsed = value;
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

export function addCost(left: number, right: number): number {
  const sum = left + right;
  if (!Number.isFinite(sum)) throw new UsageAccountingError('Provider cost exceeds the finite-number range.');
  return sum;
}
