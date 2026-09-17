export function readLocalStorage(key: string): string | null {
  try {
    return globalThis.localStorage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

export function writeLocalStorage(key: string, value: string): boolean {
  try {
    globalThis.localStorage?.setItem(key, value);
    return Boolean(globalThis.localStorage);
  } catch {
    return false;
  }
}
