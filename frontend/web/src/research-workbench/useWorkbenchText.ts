import { useCallback } from 'react';
import { useI18n } from '../i18n';

export function useWorkbenchText() {
  const { locale, t } = useI18n();
  const text = useCallback(
    (zh: string, en: string) => locale === 'zh-CN' ? zh : en,
    [locale],
  );
  return { locale, text, t };
}
