import { BackendHandshake } from './BackendHandshake';
import { Button } from './primitives';
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
  const { t, locale } = useI18n();
  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-4 px-6 text-center">
      {loading ? <BackendHandshake /> : (
        <>
          <h1 className="text-2xl font-semibold text-ink">{locale === 'zh-CN' ? hasProjects ? '继续你的项目' : '开始一个项目' : hasProjects ? 'Continue your work' : 'Start a project'}</h1>
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
        </div>
      )}
    </div>
  );
}
