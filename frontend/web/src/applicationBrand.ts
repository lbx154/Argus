import { DEFAULT_APPLICATION_BRAND, parseApplicationBrand } from '../../core/src/applicationBrand';

let brand = DEFAULT_APPLICATION_BRAND;
export const applicationBrand = () => brand;

export async function loadApplicationBrand() {
  const response = await fetch('/api/application-brand', { cache: 'no-store' });
  if (!response.ok) throw new Error(`Application identity unavailable (${response.status})`);
  brand = parseApplicationBrand(await response.json());
  if (brand.id === 'argus') return;
  document.title = `${brand.name} · Workbench`;
  document.querySelector('meta[name="apple-mobile-web-app-title"]')?.setAttribute('content', brand.name);
  document.querySelector('link[rel="manifest"]')?.setAttribute('href', '/api/application-brand/manifest.webmanifest');
  for (const link of document.querySelectorAll<HTMLLinkElement>('link[rel="icon"], link[rel="apple-touch-icon"]')) {
    const dark = link.media.includes('dark');
    link.href = (dark ? brand.assets.icon_dark : brand.assets.icon)!;
    link.removeAttribute('type');
  }
}
