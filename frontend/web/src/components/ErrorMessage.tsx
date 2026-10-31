import { useI18n, type Locale } from '../i18n';
import { errorText, rawErrorText } from '../lib/format';

/** Keep diagnostic wording available without using it as interface copy. */
export function ErrorDetails({ error }: { error: unknown }) {
  const { t } = useI18n();
  const raw = rawErrorText(error);
  return raw ? <details className="mt-1 text-xs text-ink-dim">
    <summary className="cursor-pointer">{t('error.details')}</summary>
    <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap [overflow-wrap:anywhere] font-mono">{raw}</pre>
  </details> : null;
}

export function ErrorMessage({ error, code, locale }: { error: unknown; code?: string; locale?: Locale }) {
  const current = useI18n();
  return <><span>{errorText(error, locale ?? current.locale, code)}</span><ErrorDetails error={error} /></>;
}
