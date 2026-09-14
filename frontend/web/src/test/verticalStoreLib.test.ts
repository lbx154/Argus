import { describe, expect, it } from 'vitest';
import { ApiError } from '../../../core/src/http';
import type { VerticalRow } from '../../../core/src/types';
import {
  EMPTY_FILTER, deriveTags, failureDetail, filterRows, formatSize, hasStoreCapability, hostedTrialBuild, isStoreConflict,
  isStoreMissing, matchesKind, pollInterval, progressPercent, purposeText, visibleActions,
} from '../lib/verticalStore';

const row = (over: Partial<VerticalRow> & { name: string }): VerticalRow => ({
  purpose: 'Purpose', purpose_zh: null, kind: 'available', version: '1.0.0', installed_version: null, enabled: false,
  update_available: false, requires: [], shared: [], python_requirements: [], missing_python: [], tags: [], size_bytes: null,
  used_by: [], operation: null, managed_by_host: false, actions: ['install'], ...over,
});

const rows: VerticalRow[] = [
  row({ name: 'research', kind: 'builtin', enabled: true, actions: ['disable'], tags: ['science', 'default'] }),
  row({ name: 'software', kind: 'package', enabled: true, actions: ['disable'], tags: ['engineering'] }),
  row({ name: 'kernel_engineering', kind: 'installed', enabled: true, actions: ['update', 'disable', 'uninstall'], tags: ['engineering', 'gpu'], requires: ['software'] }),
  row({ name: 'materials', kind: 'available', purpose: 'Materials discovery', purpose_zh: '材料发现', tags: ['science'] }),
];

describe('kind filter', () => {
  it('counts bundled verticals as installed and keeps built-in separate', () => {
    expect(matchesKind('installed', 'installed')).toBe(true);
    expect(matchesKind('package', 'installed')).toBe(true);
    expect(matchesKind('builtin', 'installed')).toBe(false);
    expect(matchesKind('builtin', 'builtin')).toBe(true);
    expect(matchesKind('available', 'available')).toBe(true);
    expect(matchesKind('installed', 'available')).toBe(false);
    expect(matchesKind('available', 'all')).toBe(true);
  });

  it('combines kind, tag and query', () => {
    expect(filterRows(rows, EMPTY_FILTER).map((r) => r.name)).toEqual(['research', 'software', 'kernel_engineering', 'materials']);
    expect(filterRows(rows, { ...EMPTY_FILTER, kind: 'installed' }).map((r) => r.name)).toEqual(['software', 'kernel_engineering']);
    expect(filterRows(rows, { ...EMPTY_FILTER, tag: 'science' }).map((r) => r.name)).toEqual(['research', 'materials']);
    expect(filterRows(rows, { ...EMPTY_FILTER, kind: 'installed', tag: 'gpu' }).map((r) => r.name)).toEqual(['kernel_engineering']);
  });

  it('searches name, purpose, Chinese purpose, tags and requirements without caring about case', () => {
    expect(filterRows(rows, { ...EMPTY_FILTER, query: 'KERNEL' }).map((r) => r.name)).toEqual(['kernel_engineering']);
    expect(filterRows(rows, { ...EMPTY_FILTER, query: 'discovery' }).map((r) => r.name)).toEqual(['materials']);
    expect(filterRows(rows, { ...EMPTY_FILTER, query: '材料' }).map((r) => r.name)).toEqual(['materials']);
    expect(filterRows(rows, { ...EMPTY_FILTER, query: 'gpu' }).map((r) => r.name)).toEqual(['kernel_engineering']);
    expect(filterRows(rows, { ...EMPTY_FILTER, query: '  ' })).toHaveLength(4);
    expect(filterRows(rows, { ...EMPTY_FILTER, query: 'nothing here' })).toHaveLength(0);
  });
});

describe('tags', () => {
  it('lists each tag once, sorted, ignoring blanks', () => {
    expect(deriveTags(rows)).toEqual(['default', 'engineering', 'gpu', 'science']);
    expect(deriveTags([row({ name: 'x', tags: [' ', 'b', 'a', 'b'] })])).toEqual(['a', 'b']);
    expect(deriveTags([])).toEqual([]);
  });
});

describe('actions a card may show', () => {
  it('is exactly the server list on a self-hosted copy', () => {
    expect(visibleActions(rows[2], false)).toEqual(['update', 'disable', 'uninstall']);
    expect(visibleActions(rows[3], false)).toEqual(['install']);
  });

  it('drops install, update and uninstall when the host owns the store or the row', () => {
    expect(visibleActions(rows[2], true)).toEqual(['disable']);
    expect(visibleActions(rows[3], true)).toEqual([]);
    expect(visibleActions({ ...rows[2], managed_by_host: true }, false)).toEqual(['disable']);
    expect(visibleActions(row({ name: 'x', actions: ['enable'] }), true)).toEqual(['enable']);
  });

  it('ignores actions this interface does not know', () => {
    expect(visibleActions(row({ name: 'x', actions: ['install', 'launch' as never] }), false)).toEqual(['install']);
  });
});

describe('text helpers', () => {
  it('prefers the Chinese purpose only under the Chinese interface', () => {
    expect(purposeText(rows[3], 'zh-CN')).toBe('材料发现');
    expect(purposeText(rows[3], 'en')).toBe('Materials discovery');
    expect(purposeText(rows[0], 'zh-CN')).toBe('Purpose');
    expect(purposeText({ purpose: 'P', purpose_zh: '  ' }, 'zh-CN')).toBe('P');
  });

  it('formats sizes and says nothing for an unknown one', () => {
    expect(formatSize(null)).toBe('—');
    expect(formatSize(undefined)).toBe('—');
    expect(formatSize(-1)).toBe('—');
    expect(formatSize(0)).toBe('0 B');
    expect(formatSize(512)).toBe('512 B');
    expect(formatSize(2_500_000)).toBe('2.4 MB');
    expect(formatSize(15 * 1024 ** 3)).toBe('15 GB');
  });

  it('shows the server percent as is, clamps it, and hides unknown progress', () => {
    const op = { status: 'running' as const, action: 'install', message: null, started: '2026-09-14T08:00:00Z', finished: null };
    expect(progressPercent({ ...op, progress: 1 })).toBe(1);
    expect(progressPercent({ ...op, progress: 40 })).toBe(40);
    expect(progressPercent({ ...op, progress: 73 })).toBe(73);
    expect(progressPercent({ ...op, progress: 250 })).toBe(100);
    expect(progressPercent({ ...op, progress: 0 })).toBeNull();
    expect(progressPercent({ ...op, progress: Number.NaN })).toBeNull();
    expect(progressPercent({ ...op, status: 'done', progress: 1 })).toBeNull();
    expect(progressPercent(null)).toBeNull();
  });
});

describe('polling and errors', () => {
  it('follows a running job closely while open, slowly otherwise, and rests when idle and closed', () => {
    expect(pollInterval(true, true)).toBe(1_500);
    expect(pollInterval(true, false)).toBe(4_000);
    expect(pollInterval(false, true)).toBe(4_000);
    expect(pollInterval(false, false)).toBe(0);
  });

  it('reads the service sentence out of an ApiError', () => {
    const withDetail = new ApiError('POST /api/verticals/x/manage/uninstall → 409: used by projects s-1, s-2', 409, 'POST', '/api/verticals/x/manage/uninstall', '', 'used by projects s-1, s-2');
    expect(failureDetail(withDetail)).toBe('used by projects s-1, s-2');
    const legacy = new ApiError('POST /api/verticals/x/manage/install → 409: dependency missing', 409, 'POST', '/api/verticals/x/manage/install');
    expect(failureDetail(legacy)).toBe('dependency missing');
    expect(failureDetail(new ApiError('GET /api/verticals → 500', 500, 'GET', '/api/verticals'))).toBe('');
    expect(failureDetail(new Error('network lost'))).toBe('network lost');
    expect(failureDetail(undefined)).toBe('');
  });

  it('classifies a 409 as a conflict the user may force and a 404 as a missing store', () => {
    const conflict = new ApiError('x', 409, 'POST', '/p');
    const missing = new ApiError('x', 404, 'GET', '/api/verticals');
    expect(isStoreConflict(conflict)).toBe(true);
    expect(isStoreConflict(missing)).toBe(false);
    expect(isStoreConflict(new Error('409'))).toBe(false);
    expect(isStoreMissing(missing)).toBe(true);
    expect(isStoreMissing(conflict)).toBe(false);
  });

  it('checks the capability list and the hosted build flag', () => {
    expect(hasStoreCapability(['a', 'verticals.store.v1'], 'verticals.store.v1')).toBe(true);
    expect(hasStoreCapability(['a'], 'verticals.store.v1')).toBe(false);
    expect(hasStoreCapability(undefined, 'verticals.store.v1')).toBe(false);
    expect(hostedTrialBuild({ VITE_ARGUS_HOSTED_TRIAL: '1' })).toBe(true);
    expect(hostedTrialBuild({ VITE_ARGUS_HOSTED_TRIAL: '0' })).toBe(false);
    expect(hostedTrialBuild({})).toBe(false);
  });
});
