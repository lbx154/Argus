import { writeLocalStorage } from './storage';

export const THEME_STYLE_STORAGE_KEY = 'argus.themeStyle';
export type ThemeStyle = 'standard';

/** Retire the old colour-style choice; light/dark remain global preferences. */
export function readThemeStyle(): ThemeStyle {
  return 'standard';
}

export function normalizeThemeStyle(): void {
  writeLocalStorage(THEME_STYLE_STORAGE_KEY, 'standard');
}
