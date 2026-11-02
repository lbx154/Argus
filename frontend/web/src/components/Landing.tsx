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
import { utcTime } from '../lib/daily';

/** Full-viewport picker/empty landing shown until a daemon is selectable. */
export function Landing({
  loading,
  hasProjects,
  error,
  onRetry,
  onNew,
  onChoose,
  onCreateChief,
  canCreate,
  creating = false,
  onSubmit,
  workdirs = [],
  createError,
  onWorkdirChange,
  focusSignal = 0,
  onFocusComposer,
}: {
  loading: boolean;
  hasProjects: boolean;
  error?: string;
  onRetry: () => void;
  onNew: (draft?: string) => void;
  onChoose: () => void;
  /** Made once: the chief, given the directions the operator follows and, local HH:MM, when it writes each day. */
  onCreateChief?: (interests: string, at: string | null) => Promise<boolean>;
  canCreate: boolean;
  creating?: boolean;
  /** The project is made in `workdir`, a directory on the server; empty, in a new one. */
  onSubmit?: (text: string, files: File[], workdir: string) => Promise<boolean>;
  /** Directories earlier projects worked in, offered as the operator types. */
  workdirs?: string[];
  createError?: unknown;
  onWorkdirChange?: () => void;
  focusSignal?: number;
  onFocusComposer?: () => void;
}) {
  const { t, locale } = useI18n();
  const { draft, setText, setAttachments, drop } = useSessionComposer('home');
  const [completionSelection, setCompletionSelection] = useState(0);
  const [workdir, setWorkdir] = useState('');
  // A new form starts with an empty directory; errors from the previous form expire with it.
  useEffect(() => { onWorkdirChange?.(); }, [onWorkdirChange]);
  return (
    // While its project is made, the conversation it began shows instead.
    <div className="landing-page" style={creating ? { display: 'none' } : undefined}>
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
      {!loading && !error && onCreateChief && <ChiefSetup onCreate={onCreateChief} disabled={creating} zh={locale === 'zh-CN'} />}
      {!loading && !error && canCreate && onSubmit && <div className="landing-composer">
        <ChatBox value={draft.text} onChange={setText} attachments={draft.attachments} onAttachmentsChange={setAttachments}
          onSend={async (text, files = []) => {
            const accepted = await onSubmit(text, files, workdir.trim());
            if (accepted) drop('home');
            return accepted;
          }} disabled={creating} pending={false} focusSignal={focusSignal}
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
        <ShowcasePrompts onPick={onSubmit ? text => { setText(text); onFocusComposer?.(); } : onNew} disabled={creating} />
      </section>}
      {!loading && !error && !hasProjects && canCreate && <section className="landing-starters">
        <h2>{locale === 'zh-CN' ? '也可以从这里开始' : 'Or start with an idea'}</h2>
        <StartPrompts onPick={onSubmit ? text => { setText(text); onFocusComposer?.(); } : onNew} disabled={creating} />
      </section>}
    </div>
  );
}

/** The chief, set up once: what the operator follows, and when it writes to them each day. */
function ChiefSetup({ onCreate, disabled, zh }: { onCreate: (interests: string, at: string | null) => Promise<boolean>; disabled: boolean; zh: boolean }) {
  const [interests, setInterests] = useState('');
  const [time, setTime] = useState('08:00');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!interests.trim() || busy) return;
    setBusy(true);
    try { await onCreate(interests.trim(), time ? utcTime(time) : null); } finally { setBusy(false); }
  };
  return (
    <section className="landing-chief">
      <h2>{zh ? '设立总负责人' : 'Set up your chief'}</h2>
      <p>{zh
        ? '它照看你所有的项目，盯着你关心的方向（arXiv、博客、GitHub），每天给你写一封信：各项目进展、哪些在等你，以及值得看的新东西。'
        : 'It looks after all your projects and follows your directions (arXiv, blogs, GitHub), and writes to you each day: how the projects move, what waits for you, and what is new and worth your time.'}</p>
      <textarea value={interests} onChange={event => setInterests(event.target.value)} rows={3} disabled={disabled || busy}
        aria-label={zh ? '你关心的方向' : 'The directions you follow'}
        placeholder={zh ? '你关心的方向，例如：agent 的验证器与奖励模型；agentic RL；自动化科研' : 'The directions you follow, e.g. verifiers and reward models for agents; agentic RL; automated research'} />
      <div className="landing-chief-row">
        <label>{zh ? '每天来信' : 'Daily letter at'}
          <input type="time" value={time} onChange={event => setTime(event.target.value)} disabled={disabled || busy} />
        </label>
        <Button variant="primary" onClick={() => void submit()} disabled={disabled || busy || !interests.trim()}>
          {busy ? (zh ? '正在设立…' : 'Setting up…') : (zh ? '设立并写第一封信' : 'Set up and write the first letter')}
        </Button>
      </div>
    </section>
  );
}
