import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faBars } from '@fortawesome/free-solid-svg-icons';
import { BackendHandshake } from './BackendHandshake';
import { Button } from './primitives';
import { useI18n } from '../i18n';
import { ShowcasePrompts, StartPrompts } from './StartPrompts';
import { ArgusMark } from './Wordmark';
import { ChatBox } from './ChatBox';
import { useSessionComposer } from '../useSessionComposer';
import { useEffect, useState } from 'react';
import { ErrorMessage } from './ErrorMessage';

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
  onSubmit,
  workdirs = [],
  createError,
  onWorkdirChange,
}: {
  loading: boolean;
  hasProjects: boolean;
  error?: string;
  onRetry: () => void;
  onNew: (draft?: string) => void;
  onChoose: () => void;
  canCreate: boolean;
  creating?: boolean;
  /** The project is made in `workdir`, a directory on the server; empty, in a new one. */
  onSubmit?: (text: string, files: File[], workdir: string) => Promise<boolean>;
  /** Directories earlier projects worked in, offered as the operator types. */
  workdirs?: string[];
  createError?: unknown;
  onWorkdirChange?: () => void;
}) {
  const { t, locale } = useI18n();
  const { draft, setText, setAttachments, drop } = useSessionComposer('home');
  const [focus, setFocus] = useState(0);
  const [completionSelection, setCompletionSelection] = useState(0);
  const [workdir, setWorkdir] = useState('');
  // A new form starts with an empty directory; errors from the previous form expire with it.
  useEffect(() => { onWorkdirChange?.(); }, [onWorkdirChange]);
  return (
    <div className="landing-page">
      <div className="landing-navigation lg:hidden">
        {/* The project list opens from the same mark as on a project's page. */}
        <Button onClick={onChoose} aria-label={t('topbar.openSessions')} title={t('topbar.openSessions')}><FontAwesomeIcon icon={faBars} className="h-4 w-4" /></Button>
      </div>
      {loading ? <BackendHandshake /> : (
        <>
          <div className="landing-intro">
            {!hasProjects && !error && <ArgusMark size="var(--landing-mark-size)" className="landing-mark text-ink" />}
            <h1>{locale === 'zh-CN' ? hasProjects ? '继续你的项目' : '你想完成什么？' : hasProjects ? 'Continue your work' : 'What would you like to accomplish?'}</h1>
          </div>
          <p className={`max-w-md text-sm leading-relaxed ${error ? 'text-err' : 'text-ink-faint'}`}>
            {error
              ? error
              : hasProjects
              ? t('landing.selectOrCreate')
              : locale === 'zh-CN' ? '从问题到成果，和 Argus 一起推进。' : 'Research, build and deliver with Argus.'}
          </p>
        </>
      )}
      {!loading && !error && canCreate && onSubmit && <div className="landing-composer">
        <ChatBox value={draft.text} onChange={setText} attachments={draft.attachments} onAttachmentsChange={setAttachments}
          onSend={async (text, files = []) => {
            const accepted = await onSubmit(text, files, workdir.trim());
            if (accepted) drop('home');
            return accepted;
          }} disabled={creating} pending={false} focusSignal={focus}
          completionSelection={completionSelection} onCompletionSelectionChange={setCompletionSelection} />
        <p className="landing-caption">{locale === 'zh-CN'
          ? '发送后自动建立项目；团队干活时也可以随时说话。'
          : 'Sending creates a project. You can write at any time while the team works.'}</p>
        <label className="landing-workdir">
          <span>{locale === 'zh-CN' ? '工作目录' : 'Folder'}</span>
          <input value={workdir} onChange={event => { setWorkdir(event.target.value); onWorkdirChange?.(); }} list="landing-workdirs" disabled={creating}
            aria-invalid={Boolean(createError)} aria-describedby={createError ? 'landing-workdir-error' : undefined}
            spellCheck={false} autoComplete="off"
            placeholder={locale === 'zh-CN' ? '留空则新建；可填服务器上的路径，没有就创建' : 'Blank for a new folder, or a path on the server, made if missing'} />
          <datalist id="landing-workdirs">{workdirs.map(path => <option key={path} value={path} />)}</datalist>
        </label>
        {Boolean(createError) && <div id="landing-workdir-error" role="alert" className="text-xs text-err"><ErrorMessage error={createError} /></div>}
      </div>}
      {!loading && (
        <div className="flex flex-wrap justify-center gap-2">
          {error ? (
            <Button onClick={onRetry} variant="danger">{t('common.retry')}</Button>
          ) : null}
          {hasProjects ? (
            <Button onClick={onChoose}>{t('landing.select')}</Button>
          ) : canCreate && !onSubmit ? (
            <Button onClick={() => onNew()} disabled={creating} variant="primary">{creating ? t('common.loading') : t('landing.new')}</Button>
          ) : null}
        </div>
      )}
      {!loading && !error && canCreate && <section className="landing-starters">
        <h2>{locale === 'zh-CN' ? '看看它能做什么：这些任务都实际跑过' : 'See what it can do: each of these was run end to end'}</h2>
        <ShowcasePrompts onPick={onSubmit ? text => { setText(text); setFocus(value => value + 1); } : onNew} disabled={creating} />
      </section>}
      {!loading && !error && !hasProjects && canCreate && <section className="landing-starters">
        <h2>{locale === 'zh-CN' ? '也可以从这里开始' : 'Or start with an idea'}</h2>
        <StartPrompts onPick={onSubmit ? text => { setText(text); setFocus(value => value + 1); } : onNew} disabled={creating} />
      </section>}
    </div>
  );
}
