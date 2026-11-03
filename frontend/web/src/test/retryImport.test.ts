import { afterEach, describe, expect, it, vi } from 'vitest';
import { isChunkLoadError, reloadForChunkError, retryImport } from '../lib/retryImport';

const chunkError = () => new TypeError('Failed to fetch dynamically imported module: https://example.test/assets/App-x.js');

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('loading code chunks on an unsteady network', () => {
  it('recognises chunk failures from Chrome, Safari and Firefox, and nothing else', () => {
    expect(isChunkLoadError(chunkError())).toBe(true);
    expect(isChunkLoadError(new TypeError('Importing a module script failed.'))).toBe(true);
    expect(isChunkLoadError(new TypeError('error loading dynamically imported module'))).toBe(true);
    expect(isChunkLoadError(new Error('Cannot read properties of undefined'))).toBe(false);
  });

  it('tries a failed chunk again and returns it once it arrives', async () => {
    const load = vi.fn().mockRejectedValueOnce(chunkError()).mockResolvedValueOnce({ default: 'App' });
    await expect(retryImport(load, [0])).resolves.toEqual({ default: 'App' });
    expect(load).toHaveBeenCalledTimes(2);
  });

  it('gives up after its retries, and does not retry errors that are not about downloading', async () => {
    const failing = vi.fn().mockRejectedValue(chunkError());
    await expect(retryImport(failing, [0, 0])).rejects.toThrow('dynamically imported module');
    expect(failing).toHaveBeenCalledTimes(3);
    const broken = vi.fn().mockRejectedValue(new Error('render bug'));
    await expect(retryImport(broken, [0, 0])).rejects.toThrow('render bug');
    expect(broken).toHaveBeenCalledTimes(1);
  });

  it('reloads the page once, not again within a minute', () => {
    const store = new Map<string, string>();
    vi.stubGlobal('localStorage', { getItem: (key: string) => store.get(key) ?? null, setItem: (key: string, value: string) => { store.set(key, value); } });
    const reload = vi.fn();
    vi.stubGlobal('window', { location: { reload } });
    expect(reloadForChunkError(1_000_000)).toBe(true);
    expect(reloadForChunkError(1_030_000)).toBe(false);
    expect(reloadForChunkError(1_070_000)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(2);
  });
});
