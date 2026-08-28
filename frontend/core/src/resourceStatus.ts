export function formatResourceTtl(seconds: number): string {
  const total = Math.ceil(seconds);
  if (total < 60) return `${total}s`;
  if (total < 3_600) return `${Math.floor(total / 60)}m ${total % 60}s`;
  if (total < 86_400) {
    return `${Math.floor(total / 3_600)}h ${Math.floor((total % 3_600) / 60)}m`;
  }
  return `${Math.floor(total / 86_400)}d ${Math.floor((total % 86_400) / 3_600)}h`;
}
