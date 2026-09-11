import { BackendHandshake } from './BackendHandshake';
import { PluginLauncher } from './PluginLauncher';
import { Wordmark } from './Wordmark';
import { Button } from './primitives';
import { TAGLINE } from '../lib/soul';
import { useI18n } from '../i18n';

/** Full-viewport picker/empty landing shown until a daemon is selectable. */
export function Landing({
  loading,
  hasProjects,
  error,
  onRetry,
  onNew,
  onChoose,
  canCreate,
}: {
  loading: boolean;
  hasProjects: boolean;
  error?: string;
  onRetry: () => void;
  onNew: () => void;
  onChoose: () => void;
  canCreate: boolean;
}) {
  const { t } = useI18n();
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
      {loading ? <BackendHandshake /> : (
        <>
          <Wordmark size={32} tag={TAGLINE} />
          <p className={`max-w-md text-sm leading-relaxed ${error ? 'text-err' : 'text-ink-faint'}`}>
            {error
              ? error
              : hasProjects
              ? t('landing.selectOrCreate')
              : t('landing.noSessions')}
          </p>
        </>
      )}
      {!loading && (
        <div className="flex flex-wrap justify-center gap-2">
          {error ? (
            <Button onClick={onRetry} variant="danger">{t('common.retry')}</Button>
          ) : null}
          {hasProjects ? (
            <Button onClick={onChoose}>{t('landing.select')}</Button>
          ) : canCreate ? (
            <Button onClick={onNew} variant="primary">{t('landing.new')}</Button>
          ) : null}
          {!error ? <PluginLauncher /> : null}
        </div>
      )}
    </div>
  );
}
