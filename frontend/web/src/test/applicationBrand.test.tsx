import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, expect, it, vi } from 'vitest';
import { DEFAULT_APPLICATION_BRAND, parseApplicationBrand } from '../../../core/src/applicationBrand';
import { applicationBrand, loadApplicationBrand } from '../applicationBrand';
import { ArgusMark, Wordmark } from '../components/Wordmark';
import { translate } from '../i18n';

const assets = Object.fromEntries(['mark', 'mark_dark', 'logo', 'logo_dark', 'icon', 'icon_dark']
  .map((name) => [name, `/api/application-brand/assets/${name}`]));
const custom = { ...DEFAULT_APPLICATION_BRAND, id: 'example', name: 'Example', terminal_wordmark: 'example', assets };
const links = [
  { media: '(prefers-color-scheme: light)', href: '', removeAttribute: vi.fn() },
  { media: '(prefers-color-scheme: dark)', href: '', removeAttribute: vi.fn() },
];

afterEach(async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => DEFAULT_APPLICATION_BRAND }));
  await loadApplicationBrand();
  vi.unstubAllGlobals();
});

it('keeps default Argus artwork and labels', () => {
  expect(renderToStaticMarkup(<Wordmark />)).toContain('data-logo="rounded-horizontal"');
  expect(translate('chat.messageArgus', {}, 'en')).toBe('message Argus');
});

it('loads server identity before rendering official asset URLs and chrome', async () => {
  const meta = { setAttribute: vi.fn() };
  const manifest = { setAttribute: vi.fn() };
  vi.stubGlobal('document', {
    title: '', querySelector: (selector: string) => selector.startsWith('meta') ? meta : manifest,
    querySelectorAll: () => links,
  });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => custom }));
  await loadApplicationBrand();
  expect(applicationBrand().name).toBe('Example');
  expect(document.title).toBe('Example · Workbench');
  expect(meta.setAttribute).toHaveBeenCalledWith('content', 'Example');
  expect(manifest.setAttribute).toHaveBeenCalledWith('href', '/api/application-brand/manifest.webmanifest');
  expect(links.map((link) => link.href)).toEqual([assets.icon, assets.icon_dark]);
  const html = renderToStaticMarkup(<><Wordmark /><ArgusMark size={31} /></>);
  expect(html).toContain('app-brand-light');
  expect(html).toContain('app-brand-dark');
  expect(html).toContain('alt="Example"');
  expect(html).toContain(assets.logo);
  expect(html).toContain(assets.mark);
  expect(html).not.toContain('data-logo="rounded');
  expect(translate('chat.messageArgus', {}, 'zh-CN')).toBe('向 Example 发送消息');
  expect(translate('unknown', { value: 'Argus evidence stays unchanged' }, 'en')).toBe('unknown');
});

it('rejects unavailable or malformed identity instead of claiming default success', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }));
  await expect(loadApplicationBrand()).rejects.toThrow('503');
  expect(() => parseApplicationBrand({ ...custom, name: '\x1bspoof' })).toThrow();
  expect(() => parseApplicationBrand({ ...custom, assets: { ...assets, mark: 'https://elsewhere/icon' } })).toThrow();
  expect(() => parseApplicationBrand({ ...custom, assets: {} })).toThrow();
});

it('draws a reload with the identity this tab last fetched while it refreshes', async () => {
  const values = new Map<string, string>();
  const storage = () => ({
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
  }) as unknown as Storage;
  vi.stubGlobal('document', { title: '', querySelector: () => null, querySelectorAll: () => [] });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => custom }));
  await loadApplicationBrand(storage);
  // The server is slow or down on the next load: the kept identity shows at once.
  vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => undefined)));
  await loadApplicationBrand(storage);
  expect(applicationBrand().name).toBe('Example');
  values.set('argus.application-brand.v1', '{broken');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }));
  await expect(loadApplicationBrand(storage)).rejects.toThrow('503');
});
