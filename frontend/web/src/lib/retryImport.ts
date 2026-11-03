import { readLocalStorage, writeLocalStorage } from './storage';

/** True when an error says a code chunk could not be downloaded or evaluated. */
export function isChunkLoadError(error: unknown): boolean {
  const text = error instanceof Error ? `${error.name} ${error.message}` : String(error);
  return /dynamically imported module|Importing a module script failed|error loading dynamically imported module|ChunkLoadError|Unable to preload CSS/i.test(text);
}

const wait = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

/** Load a code chunk, trying again on an unsteady network before giving up. */
export async function retryImport<T>(load: () => Promise<T>, delays: number[] = [800, 2000]): Promise<T> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await load();
    } catch (error) {
      if (attempt >= delays.length || !isChunkLoadError(error)) throw error;
      await wait(delays[attempt]);
    }
  }
}

const RELOADED_AT = 'argus.chunk-reload-at';

/** Reload once for a chunk that would not load; a second failure within a minute stays on screen. */
export function reloadForChunkError(now = Date.now()): boolean {
  const last = Number(readLocalStorage(RELOADED_AT) ?? 0);
  if (now - last < 60_000) return false;
  if (!writeLocalStorage(RELOADED_AT, String(now))) return false;
  window.location.reload();
  return true;
}
