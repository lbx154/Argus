import { readLocalStorage } from './storage';

export const THEME_STYLE_STORAGE_KEY = 'argus.themeStyle';

export type ThemeStyle = 'standard' | 'gradient';

export function readThemeStyle(): ThemeStyle {
  return readLocalStorage(THEME_STYLE_STORAGE_KEY) === 'gradient' ? 'gradient' : 'standard';
}
