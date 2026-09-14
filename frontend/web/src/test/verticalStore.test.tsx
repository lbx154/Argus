import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { VerticalRow, VerticalsPayload } from '../../../core/src/types';
import { VerticalCard, VerticalStoreEntry, VerticalStoreView } from '../components/VerticalStore';
import { EMPTY_FILTER, type StoreFilter } from '../lib/verticalStore';

const host = vi.hoisted(() => ({ locale: 'en' }));
vi.mock('../i18n', () => ({
  useI18n: () => ({
    locale: host.locale,
    t: (key: string, vars?: Record<string, string | number>) => vars ? `${key} ${Object.values(vars).join(', ')}` : key,
  }),
}));
vi.mock('../api', () => ({ api: {}, VERTICAL_STORE_CAPABILITY: 'verticals.store.v1' }));

const row = (over: Partial<VerticalRow> & { name: string }): VerticalRow => ({
  purpose: 'Purpose', purpose_zh: null, kind: 'available', version: '1.0.0', installed_version: null, enabled: false,
  update_available: false, requires: [], shared: [], python_requirements: [], missing_python: [], tags: [], size_bytes: null,
  used_by: [], operation: null, managed_by_host: false, actions: ['install'], ...over,
});

const research = row({ name: 'research', kind: 'builtin', enabled: true, version: null, actions: ['disable'], tags: ['science'] });
const software = row({ name: 'software', kind: 'package', enabled: true, version: '0.1.7', actions: ['disable'], tags: ['engineering'] });
const kernel = row({
  name: 'kernel_engineering', kind: 'installed', enabled: true, installed_version: '1.2.0', version: '1.3.0', update_available: true,
  actions: ['update', 'disable', 'uninstall'], requires: ['software', 'ghost'], shared: ['research'], tags: ['engineering', 'gpu'],
  size_bytes: 2_500_000, used_by: ['s-1', 's-2'], missing_python: ['pandas', 'torch'], python_requirements: ['pandas', 'torch', 'numpy'],
});
const materials = row({ name: 'materials', kind: 'available', purpose: 'Materials discovery', purpose_zh: '材料发现与筛选', tags: ['science'] });
const installing = row({
  name: 'chem', kind: 'available', actions: [],
  operation: { status: 'running', action: 'install', progress: 0.4, message: 'Downloading 3 of 7 files', started: '2026-09-14T08:00:00Z', finished: null },
});
const failedInstall = row({
  name: 'bio', kind: 'available', actions: ['install'],
  operation: { status: 'failed', action: 'install', progress: 0, message: 'checksum mismatch', started: '2026-09-14T08:00:00Z', finished: '2026-09-14T08:01:00Z' },
});

const payload: VerticalsPayload = {
  verticals: [research, software, kernel, materials],
  catalog: { source: 'https://verticals.example/catalog.json', fetched_at: '2026-09-14T08:00:00Z', release_tag: 'v0.3.0', error: null },
  host: { managed_by_host: false, store_root: '/home/u/.argus/verticals' },
};

const known = new Set(['research', 'software', 'kernel_engineering', 'materials']);
const card = (item: VerticalRow, over: Partial<Parameters<typeof VerticalCard>[0]> = {}) => renderToStaticMarkup(
  <VerticalCard row={item} locale={host.locale} hosted={false} busy={false} known={known} onAct={async () => true} onJump={() => undefined} {...over} />,
);
const view = (over: Partial<Parameters<typeof VerticalStoreView>[0]> = {}, filter: StoreFilter = EMPTY_FILTER) => renderToStaticMarkup(
  <VerticalStoreView payload={payload} filter={filter} onFilter={() => undefined} hosted={false} supported loading={false} refreshing={false}
    error="" pending={null} failures={{}} onAct={async () => true} onRefresh={() => undefined} onJump={() => undefined} {...over} />,
);
const cardOf = (html: string, name: string) => {
  const start = html.indexOf(`data-testid="vertical-${name}"`);
  expect(start).toBeGreaterThan(-1);
  return html.slice(start, html.indexOf('</article>', start));
};
const actionsOf = (html: string) => [...html.matchAll(/data-action="([a-z]+)"/g)].map((m) => m[1]);

afterEach(() => { host.locale = 'en'; vi.unstubAllEnvs(); });

describe('vertical cards', () => {
  it('shows one card per kind with its badge and only the actions the server allows', () => {
    const html = view();
    expect(html.match(/<article /g)).toHaveLength(4);
    expect(cardOf(html, 'research')).toContain('data-kind="builtin"');
    expect(cardOf(html, 'research')).toContain('verticals.kind.builtin');
    expect(cardOf(html, 'software')).toContain('verticals.kind.package');
    expect(cardOf(html, 'kernel_engineering')).toContain('verticals.kind.installed');
    expect(cardOf(html, 'materials')).toContain('verticals.kind.available');
    expect(actionsOf(cardOf(html, 'research'))).toEqual(['disable']);
    expect(actionsOf(cardOf(html, 'software'))).toEqual(['disable']);
    expect(actionsOf(cardOf(html, 'kernel_engineering'))).toEqual(['update', 'disable', 'uninstall']);
    expect(actionsOf(cardOf(html, 'materials'))).toEqual(['install']);
  });

  it('never invents a button: an empty action list renders no controls', () => {
    const html = card(row({ name: 'quiet', kind: 'installed', actions: [] }));
    expect(actionsOf(html)).toEqual([]);
    expect(html).not.toContain('verticals.action.');
  });

  it('marks enabled state, an available update and the versions', () => {
    const html = card(kernel);
    expect(html).toContain('verticals.enabled');
    expect(html).toContain('data-testid="vertical-update"');
    expect(html).toContain('verticals.updateTo 1.3.0');
    expect(html).toContain('verticals.installedVersion 1.2.0');
    expect(html).toContain('verticals.catalogVersion 1.3.0');
    expect(card(research)).toContain('verticals.enabled');
    expect(card(row({ name: 'off', kind: 'installed', enabled: false, actions: ['enable'] }))).toContain('verticals.disabledState');
    expect(card(materials)).not.toContain('verticals.disabledState');
  });

  it('links requirements to cards that exist and leaves unknown ones as plain chips', () => {
    const html = card(kernel);
    expect(html).toContain('aria-label="verticals.jumpTo software"');
    expect(html).not.toContain('aria-label="verticals.jumpTo ghost"');
    expect(html).toContain('>ghost<');
    expect(html).toContain('verticals.shared');
    expect(html).toContain('verticals.size');
    expect(html).toContain('2.4 MB');
    expect(html).toContain('#gpu');
  });

  it('warns about Python packages missing from the environment Argus runs from', () => {
    const html = card(kernel);
    expect(html).toContain('data-testid="vertical-missing-python"');
    expect(html).toContain('verticals.missingPython pandas, torch');
    expect(card(materials)).not.toContain('vertical-missing-python');
  });

  it('counts the projects using a vertical and hides the list until asked', () => {
    const html = card(kernel);
    expect(html).toContain('verticals.usedBy 2');
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain('>s-1<');
    expect(card(row({ name: 'one', used_by: ['s-9'] }))).toContain('verticals.usedByOne');
    expect(card(materials)).not.toContain('data-testid="vertical-used-by"');
  });

  it('shows the running operation as a bar with its percent and message, and disables the buttons', () => {
    const html = card(row({ ...installing, actions: ['install'] }));
    expect(html).toContain('data-testid="vertical-progress"');
    expect(html).toContain('role="progressbar"');
    expect(html).toContain('aria-valuenow="40"');
    expect(html).toContain('width:40%');
    expect(html).toContain('verticals.operation.install');
    expect(html).toContain('Downloading 3 of 7 files');
    expect(html).toMatch(/data-action="install"[^>]*disabled=""|disabled=""[^>]*data-action="install"/);
  });

  it('uses an indeterminate bar when the job cannot say how far along it is', () => {
    const html = card(row({ ...installing, operation: { ...installing.operation!, progress: 0 } }));
    expect(html).toContain('role="progressbar"');
    expect(html).not.toContain('aria-valuenow');
    expect(html).toContain('animate-pulse');
  });

  it('reports a failed operation with the store message', () => {
    const html = card(failedInstall);
    expect(html).toContain('data-testid="vertical-operation-failed"');
    expect(html).toContain('verticals.operationFailed verticals.action.install');
    expect(html).toContain('checksum mismatch');
    expect(actionsOf(html)).toEqual(['install']);
  });

  it('asks twice before uninstalling and offers to force after a 409 about projects', () => {
    const idle = card(kernel);
    expect(idle).toContain('data-action="uninstall"');
    expect(idle).not.toContain('vertical-confirm-uninstall');
    const conflict = card(kernel, { failure: { text: 'used by projects s-1, s-2', offerForce: true } });
    expect(conflict).toContain('data-testid="vertical-failure"');
    expect(conflict).toContain('used by projects s-1, s-2');
    expect(conflict).toContain('verticals.removeAnyway');
    const plain = card(kernel, { failure: { text: 'store is locked', offerForce: false } });
    expect(plain).toContain('store is locked');
    expect(plain).not.toContain('verticals.removeAnyway');
  });

  it('speaks Chinese purpose text only under the Chinese interface', () => {
    expect(card(materials)).toContain('Materials discovery');
    expect(card(materials)).not.toContain('材料发现');
    host.locale = 'zh-CN';
    expect(card(materials, { locale: 'zh-CN' })).toContain('材料发现与筛选');
    expect(card(research, { locale: 'zh-CN' })).toContain('Purpose');
  });
});

describe('hosted workspace', () => {
  it('hides install, update and uninstall but keeps enable and disable, and explains why', () => {
    const html = view({ payload: { ...payload, host: { ...payload.host, managed_by_host: true } }, hosted: true });
    expect(html).toContain('verticals.hostedNote');
    expect(html).not.toContain('verticals.intro');
    expect(actionsOf(html)).toEqual(['disable', 'disable', 'disable']);
    expect(html).not.toContain('data-action="install"');
    expect(html).not.toContain('data-action="uninstall"');
    expect(html).not.toContain('data-action="update"');
    expect(html).not.toContain('data-testid="vertical-refresh-catalog"');
    expect(view({ payload: { ...payload, verticals: [row({ name: 'off', kind: 'installed', actions: ['enable', 'uninstall'] })] }, hosted: true })).toContain('data-action="enable"');
  });

  it('hides host-owned actions per row when only that row is managed by the host', () => {
    const html = card({ ...kernel, managed_by_host: true });
    expect(actionsOf(html)).toEqual(['disable']);
  });
});

describe('store page', () => {
  it('shows the catalog source, release tag and a refresh control', () => {
    const html = view();
    expect(html).toContain('data-testid="vertical-catalog"');
    expect(html).toContain('verticals.catalogSource https://verticals.example/catalog.json');
    expect(html).toContain('verticals.catalogRelease v0.3.0');
    expect(html).toContain('verticals.catalogFetched');
    expect(html).toContain('data-testid="vertical-refresh-catalog"');
    expect(html).toContain('verticals.refreshCatalog');
    expect(view({ refreshing: true })).toContain('verticals.refreshing');
    expect(view({ payload: { ...payload, catalog: { ...payload.catalog, fetched_at: null, release_tag: null } } })).toContain('verticals.catalogNever');
  });

  it('puts a catalog error and a load error at the top, not on the cards', () => {
    const html = view({ payload: { ...payload, catalog: { ...payload.catalog, error: 'timed out after 10s' } } });
    expect(html).toContain('data-testid="vertical-catalog-error"');
    expect(html).toContain('verticals.catalogError timed out after 10s');
    expect(html.match(/<article /g)).toHaveLength(4);
    expect(view({ error: 'verticals.loadFailed' })).toContain('data-testid="vertical-store-error"');
    expect(view()).not.toContain('vertical-catalog-error');
  });

  it('says the backend is too old instead of showing an empty or failed store', () => {
    const html = view({ payload: null, supported: false });
    expect(html).toContain('data-testid="vertical-store-too-old"');
    expect(html).toContain('verticals.tooOld');
    expect(html).not.toContain('<article ');
    expect(html).not.toContain('type="search"');
  });

  it('derives tag chips from the rows and reflects the active filters', () => {
    const html = view();
    expect(html).toContain('aria-label="verticals.tagFilter"');
    expect(html).toContain('>#engineering<');
    expect(html).toContain('>#gpu<');
    expect(html).toContain('>#science<');
    expect(html).toContain('aria-label="verticals.kindFilter"');
    expect(html).toContain('verticals.filter.builtin');
    expect(html).toContain('placeholder="verticals.searchPlaceholder"');
    expect(html).toContain('verticals.count 4, 4');
    const installed = view({}, { ...EMPTY_FILTER, kind: 'installed' });
    expect(installed.match(/<article /g)).toHaveLength(2);
    expect(installed).toContain('data-testid="vertical-software"');
    expect(installed).toContain('data-testid="vertical-kernel_engineering"');
    expect(installed).toContain('verticals.clearFilters');
    const tagged = view({}, { ...EMPTY_FILTER, tag: 'science' });
    expect(tagged.match(/<article /g)).toHaveLength(2);
    expect(view({}, { ...EMPTY_FILTER, query: 'no such vertical' })).toContain('verticals.noMatches');
    expect(view({ payload: { ...payload, verticals: [] } })).toContain('verticals.none');
    expect(view({ payload: null, loading: true })).toContain('verticals.loading');
  });

  it('offers the sidebar entry with its label, and only the icon when slim', () => {
    const full = renderToStaticMarkup(<VerticalStoreEntry onOpen={() => undefined} />);
    expect(full).toContain('aria-label="verticals.entry"');
    expect(full).toContain('<span>verticals.entry</span>');
    const slim = renderToStaticMarkup(<VerticalStoreEntry compact onOpen={() => undefined} />);
    expect(slim).toContain('aria-label="verticals.entry"');
    expect(slim).not.toContain('<span>');
  });
});
