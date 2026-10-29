import { BackendHandshake } from './BackendHandshake';
import { Button } from './primitives';
import { useI18n } from '../i18n';
import { ShowcasePrompts, StartPrompts } from './StartPrompts';
import { ArgusMark } from './Wordmark';
import { ChatBox } from './ChatBox';
import { useSessionComposer } from '../useSessionComposer';
import { useState } from 'react';

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
}) {
  const { t, locale } = useI18n();
  const { draft, setText, setAttachments, drop } = useSessionComposer('home');
  const [focus, setFocus] = useState(0);
  const [slashSelection, setSlashSelection] = useState(0);
  const [workdir, setWorkdir] = useState('');
  return (
    <div className="landing-page">
      <div className="landing-navigation lg:hidden">
        <Button onClick={onChoose}>{t('landing.menu')}</Button>
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
          }} onCancel={() => undefined} disabled={creating} pending={false} focusSignal={focus}
          slashSelection={slashSelection} onSlashSelectionChange={setSlashSelection} />
        <p className="landing-caption">{locale === 'zh-CN'
          ? '发送后自动建立项目；团队干活时也可以随时说话。'
          : 'Sending creates a project. You can write at any time while the team works.'}</p>
        <label className="landing-workdir">
          <span>{locale === 'zh-CN' ? '工作目录' : 'Folder'}</span>
          <input value={workdir} onChange={event => setWorkdir(event.target.value)} list="landing-workdirs" disabled={creating}
            spellCheck={false} autoComplete="off"
            placeholder={locale === 'zh-CN' ? '新建；也可以填服务器上已有的目录' : 'A new one, or a directory on the server'} />
          <datalist id="landing-workdirs">{workdirs.map(path => <option key={path} value={path} />)}</datalist>
        </label>
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
