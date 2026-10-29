import { Languages, Moon, Sun } from 'lucide-react';
import { useI18n } from '../i18n';
import type { ThemeMode } from './TopBar';

/** Both surfaces use their existing workbench theme preference. */
export function AppearanceControls({ themeMode, onCycleTheme, labelled = false }: {
  themeMode: ThemeMode;
  onCycleTheme: () => void;
  /** In a menu, each control names what it does next to its icon. */
  labelled?: boolean;
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
      className={labelled ? 'icon-control flex h-8 w-full items-center gap-2 px-2 text-xs' : 'icon-control flex h-8 w-8 shrink-0 items-center justify-center'}>
      <Languages size={labelled ? 15 : 18} strokeWidth={1.75} aria-hidden="true" />
      {labelled && <span>{locale === 'zh-CN' ? 'English' : '中文'}</span>}
    </button>
    <button type="button" data-testid="appearance-theme" onClick={onCycleTheme} title={themeLabel} aria-label={themeLabel}
      className={labelled ? 'icon-control flex h-8 w-full items-center gap-2 px-2 text-xs' : 'icon-control flex h-8 w-8 shrink-0 items-center justify-center'}>
      <ThemeIcon size={labelled ? 15 : 18} strokeWidth={1.75} aria-hidden="true" />
      {labelled && <span>{themeMode === 'light'
        ? locale === 'zh-CN' ? '夜间模式' : 'Dark mode'
        : locale === 'zh-CN' ? '日间模式' : 'Light mode'}</span>}
    </button>
  </>;
}
