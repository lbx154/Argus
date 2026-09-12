import { Languages, Moon, Sun } from 'lucide-react';
import { useI18n } from '../i18n';
import type { ThemeMode } from './TopBar';

/** Both surfaces use their existing workbench theme preference. */
export function AppearanceControls({ themeMode, onCycleTheme }: {
  themeMode: ThemeMode;
  onCycleTheme: () => void;
}) {
  const { locale, setLocale, t } = useI18n();
  const languageLabel = t('language.switchTo', {
    language: locale === 'zh-CN' ? t('language.english') : t('language.chinese'),
  });
  const themeLabel = t('sidebar.theme', {
    current: themeMode, next: themeMode === 'light' ? 'dark' : 'light',
  });
  const ThemeIcon = themeMode === 'light' ? Sun : Moon;
  return <>
    <button type="button" data-testid="appearance-language" onClick={() => setLocale(locale === 'zh-CN' ? 'en' : 'zh-CN')}
      title={languageLabel} aria-label={languageLabel}
      className="icon-control flex h-8 w-8 shrink-0 items-center justify-center">
      <Languages size={18} strokeWidth={1.75} aria-hidden="true" />
    </button>
    <button type="button" data-testid="appearance-theme" onClick={onCycleTheme} title={themeLabel} aria-label={themeLabel}
      className="icon-control flex h-8 w-8 shrink-0 items-center justify-center">
      <ThemeIcon size={18} strokeWidth={1.75} aria-hidden="true" />
    </button>
  </>;
}
