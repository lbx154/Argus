import { DEFAULT_APPLICATION_BRAND, parseApplicationBrand } from '../../core/src/applicationBrand.js';

export function applicationBrand() {
  const raw = process.env.ARGUS_TUI_APPLICATION_BRAND;
  return raw ? parseApplicationBrand(JSON.parse(raw)) : DEFAULT_APPLICATION_BRAND;
}
