import { afterEach, expect, it, vi } from 'vitest';
import { mapCopyKey, readerPreview } from '../map/copyMode';
import { briefCopyKey } from '../research-brief/model';

afterEach(() => vi.unstubAllGlobals());

it.each([
  ['', null], ['?reader_preview=other', null],
  ['?reader_preview=source-first', 'source-first'], ['?reader_preview=learning-path', 'learning-path'],
] as const)('shares current and historical browser cache selection for %s', (search, mode) => {
  vi.stubGlobal('window', { location: { search } });
  expect(readerPreview()).toBe(mode);
  const key = mapCopyKey('project', 's-research', 'zh-CN', 's-research');
  expect(key).toEqual(briefCopyKey('s-research', 'zh-CN'));
  expect(key).toEqual(['map-copy', 'project', 's-research', 'zh-CN', 's-research', ...(mode ? [mode] : [])]);
});

it('keeps both previews separate and preserves an explicitly captured mode after a URL change', () => {
  vi.stubGlobal('window', { location: { search: '?reader_preview=learning-path' } });
  const learning = mapCopyKey('project', 's', 'en-US', 's');
  const source = mapCopyKey('project', 's', 'en-US', 's', 'source-first');
  const normal = mapCopyKey('project', 's', 'en-US', 's', null);
  expect(learning).not.toEqual(source);
  expect(learning).not.toEqual(normal);
  expect(source).not.toEqual(normal);
  expect(briefCopyKey('s', 'en-US', 'source-first')).toEqual(source);
});
