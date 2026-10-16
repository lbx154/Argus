import { BackendHandshake } from './BackendHandshake';
import { Button } from './primitives';
import { useI18n } from '../i18n';
import { StartPrompts } from './StartPrompts';
import { ArgusMark } from './Wordmark';

/** Full-viewport picker/empty landing shown until a daemon is selectable. */
export function Landing({
  loading,
  hasProjects,
  error,
  onRetry,
  onNew,
  onChoose,
  canCreate,
  creating = false,
}: {
  loading: boolean;
  hasProjects: boolean;
  error?: string;
  onRetry: () => void;
  onNew: (draft?: string) => void;
  onChoose: () => void;
  canCreate: boolean;
  creating?: boolean;
}) {
  const { t, locale } = useI18n();
  return (
    <div className="flex h-full w-full flex-col items-center justify-start gap-4 overflow-y-auto px-6 py-8 text-center lg:justify-center">
      {loading ? <BackendHandshake /> : (
        <>
          <div className="flex flex-col items-center gap-3 [--landing-mark-size:56px] lg:gap-4 lg:[--landing-mark-size:72px]">
            {!hasProjects && !error && <ArgusMark size="var(--landing-mark-size)" className="landing-mark text-ink" />}
            <h1 className="text-2xl font-semibold text-ink">{locale === 'zh-CN' ? hasProjects ? '继续你的项目' : '你想完成什么？' : hasProjects ? 'Continue your work' : 'What would you like to accomplish?'}</h1>
          </div>
          <p className={`max-w-md text-sm leading-relaxed ${error ? 'text-err' : 'text-ink-faint'}`}>
            {error
              ? error
              : hasProjects
              ? t('landing.selectOrCreate')
              : locale === 'zh-CN' ? '告诉 Argus 你的目标，或选一个起点。先交流需求，再一起完成工作。' : 'Tell Argus your goal or choose a starting point. Clarify what you need, then work together.'}
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
            <Button onClick={() => onNew()} disabled={creating} variant="primary">{creating ? t('common.loading') : t('landing.new')}</Button>
          ) : null}
          {/* Phones have no sidebar or tab bar until a project exists; give them the menu. */}
          {!hasProjects && !error ? (
            <span className="lg:hidden"><Button onClick={onChoose}>{t('landing.menu')}</Button></span>
          ) : null}
        </div>
      )}
      {!loading && !error && !hasProjects && canCreate && <div className="w-full max-w-2xl">
        <StartPrompts onPick={onNew} disabled={creating} />
        <p className="mt-4 text-xs text-ink-faint">{locale === 'zh-CN' ? '选择后会填入草稿，你可以修改；不会自动开始任务。' : 'A starting point fills a draft for you to edit. It does not start work automatically.'}</p>
      </div>}
    </div>
  );
}
