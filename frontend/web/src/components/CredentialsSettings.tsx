import { useEffect, useState, type FormEvent } from 'react';
import { api } from '../api';
import { useI18n } from '../i18n';
import { requestFailureText } from '../lib/requestFailure';

/** Mounted only while Settings is open. Values never enter a query cache or browser storage. */
export function CredentialsSettings() {
  const { locale, t } = useI18n();
  const zh = locale === 'zh-CN';
  const [names, setNames] = useState<string[]>([]);
  const [name, setName] = useState('');
  const [value, setValue] = useState('');
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    let active = true;
    void api.credentials().then(result => {
      if (active) { setNames(result.names); setReady(true); }
    }, reason => { if (active) setError(requestFailureText(reason, t).text); });
    return () => { active = false; };
  }, [t]);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError(''); setSaved(false);
    try {
      const result = await api.saveCredential(name, value);
      setValue(''); setName(''); setNames(result.names); setReady(true); setSaved(true);
    } catch (reason) { setError(requestFailureText(reason, t).text); }
    finally { setBusy(false); }
  };
  const remove = async (name: string) => {
    setBusy(true); setError(''); setSaved(false);
    try { setNames((await api.deleteCredential(name)).names); }
    catch (reason) { setError(requestFailureText(reason, t).text); }
    finally { setBusy(false); }
  };
  const fieldClass = 'mt-1 w-full min-w-0 rounded border border-line bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-blue';
  return <section className="settings-section">
    <h3 className="text-sm font-semibold text-ink">{zh ? '传给角色的环境变量' : 'Environment for roles'}</h3>
    <p className="my-2 text-xs leading-5 text-ink-faint">{zh
      ? '对所有项目生效，新启动的角色会收到这些值。同名保存会覆盖；已保存的值只写入，不会显示。'
      : 'Applies to all projects and newly started roles. Saving the same name replaces its value. Saved values are never displayed.'}</p>
    <form onSubmit={event => void save(event)} className="space-y-3" autoComplete="off">
      <label className="block text-xs text-ink-dim">{zh ? '环境变量名' : 'Environment variable name'}
        <input data-autofocus name="credential-name" value={name} onChange={event => { setName(event.target.value); setSaved(false); }}
          required pattern="[A-Za-z_][A-Za-z0-9_]*" autoCapitalize="none" spellCheck={false} className={`${fieldClass} font-mono`} />
      </label>
      <label className="block text-xs text-ink-dim">{zh ? '新值' : 'New value'}
        <textarea name="credential-value" rows={2} autoComplete="off" spellCheck={false} value={value}
          onChange={event => { setValue(event.target.value); setSaved(false); }} className={fieldClass} />
      </label>
      <button type="submit" disabled={busy} className="rounded border border-blue/35 px-3 py-2 text-xs text-blue disabled:opacity-40">{zh ? '保存变量' : 'Save variable'}</button>
    </form>
    {saved && <p role="status" className="mt-2 text-xs text-ink-dim">{zh ? '已保存' : 'Saved'}</p>}
    {error && <p role="alert" className="mt-2 text-xs text-err">{error}</p>}
    {ready && !names.length && <p className="mt-3 text-xs text-ink-faint">{zh ? '尚未登记变量。' : 'No variables registered.'}</p>}
    <ul className="mt-3 space-y-2">{names.map(name => <li key={name} className="flex items-center gap-2">
      <code className="min-w-0 flex-1 break-all text-xs">{name}</code>
      <button type="button" disabled={busy} onClick={() => void remove(name)} aria-label={`${zh ? '删除' : 'Delete'} ${name}`}
        className="shrink-0 rounded border border-line px-3 py-2 text-xs text-ink-dim disabled:opacity-40">{zh ? '删除' : 'Delete'}</button>
    </li>)}</ul>
  </section>;
}
