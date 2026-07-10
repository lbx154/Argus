/** Wrap a keyboard selection through a finite list. Pure for interaction tests. */
export function moveSelection(current: number, count: number, delta: number): number {
  if (count <= 0) return 0;
  const safe = Number.isFinite(current) ? Math.trunc(current) : 0;
  return ((safe + delta) % count + count) % count;
}
