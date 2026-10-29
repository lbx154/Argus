import { DEFAULT_APPLICATION_BRAND, parseApplicationBrand } from '../../core/src/applicationBrand';

let brand = DEFAULT_APPLICATION_BRAND;
export const applicationBrand = () => brand;

const BRAND_CACHE_KEY = 'argus.application-brand.v1';

async function fetchApplicationBrand(storage: () => Storage | undefined) {
  const response = await fetch('/api/application-brand', { cache: 'no-store' });
  if (!response.ok) throw new Error(`Application identity unavailable (${response.status})`);
  const value = parseApplicationBrand(await response.json());
  try {
    storage()?.setItem(BRAND_CACHE_KEY, JSON.stringify(value));
  } catch {
    // Only the next reload's head start is lost.
  }
  return value;
}

/** A reload in this tab draws with the identity it last fetched and refreshes it behind. */
export async function loadApplicationBrand(storage: () => Storage | undefined = () => globalThis.sessionStorage) {
  let kept = null;
  try {
    const raw = storage()?.getItem(BRAND_CACHE_KEY);
    kept = raw ? parseApplicationBrand(JSON.parse(raw)) : null;
  } catch {
    kept = null;
  }
  if (kept) {
    applyApplicationBrand(kept);
    void fetchApplicationBrand(storage).then(applyApplicationBrand, () => undefined);
    return;
  }
  applyApplicationBrand(await fetchApplicationBrand(storage));
}

function applyApplicationBrand(value: typeof brand) {
  brand = value;
  if (brand.id === 'argus') return;
  document.title = brand.name;
  document.querySelector('meta[name="apple-mobile-web-app-title"]')?.setAttribute('content', brand.name);
  document.querySelector('link[rel="manifest"]')?.setAttribute('href', '/api/application-brand/manifest.webmanifest');
  for (const link of document.querySelectorAll<HTMLLinkElement>('link[rel="icon"], link[rel="apple-touch-icon"]')) {
    const dark = link.media.includes('dark');
    link.href = (dark ? brand.assets.icon_dark : brand.assets.icon)!;
    link.removeAttribute('type');
  }
}
