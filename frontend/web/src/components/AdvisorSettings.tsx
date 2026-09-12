import { useEffect, useId, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type AdvisorConfig } from '../api';
import { useI18n } from '../i18n';

export function AdvisorSettings({ sid, primaryModel }: { sid: string; primaryModel?: string }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const text = (chinese: string, english: string) => zh ? chinese : english;
  const client = useQueryClient();
  const modelList = useId();
  const query = useQuery({ queryKey: ['advisor-settings', sid],
    queryFn: ({ signal }) => api.advisorSettings(sid, signal), enabled: !!sid, retry: false });
  const [draft, setDraft] = useState<AdvisorConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const currentSid = useRef(sid);
  currentSid.current = sid;
  useEffect(() => {
    setDraft(query.data?.saved ?? null);
  }, [sid, query.data]);
  useEffect(() => {
    setError('');
    setSaved(false);
    setBusy(false);
  }, [sid]);
  const update = (patch: Partial<AdvisorConfig>) => { setDraft(value => value && { ...value, ...patch }); setSaved(false); };
  const save = async () => {
    if (!draft || busy) return;
    const target = sid;
    setBusy(true);
    setError('');
    setSaved(false);
    try {
      const { schema_version: _version, ...values } = draft;
      const result = await api.saveAdvisorSettings(target, { ...values, model: values.model.trim() });
      client.setQueryData(['advisor-settings', target], result);
      if (currentSid.current === target) setSaved(true);
    } catch (cause) {
      if (currentSid.current === target) setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (currentSid.current === target) setBusy(false);
    }
  };
  return <section className="rounded-lg border border-line glass-card p-3" aria-label={text('顾问模型', 'Advisor model')}>
    <h3 className="text-sm font-semibold text-ink">{text('顾问模型', 'Advisor model')}</h3>
    <p className="mt-1 text-xs leading-5 text-ink-dim">{text('团队遇到难题时可以向它提问。只在咨询时调用，用量计入当前项目。', 'The team can consult this model on specific questions. It runs only when asked, and usage belongs to this project.')}</p>
    {primaryModel ? <p className="mt-1 text-xs text-ink-faint">{text('当前执行模型：', 'Current execution model: ')}{primaryModel}</p> : null}
    {query.isPending ? <p role="status" className="mt-2 text-xs text-ink-faint">{text('正在读取设置…', 'Loading settings…')}</p>
      : query.isError ? <div className="mt-2 flex items-center gap-2 text-xs text-err" role="alert">
        {text('顾问设置读取失败。', 'Could not load advisor settings.')}
        <button type="button" onClick={() => void query.refetch()} className="underline">{text('重试', 'Retry')}</button>
      </div> : draft ? <>
        <label className="mt-3 flex items-center gap-2 text-xs text-ink">
          <input type="checkbox" checked={draft.enabled} disabled={busy} onChange={event => update({ enabled: event.target.checked })} />
          {text('允许团队咨询顾问', 'Let the team consult the advisor')}
        </label>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-xs text-ink-dim">{text('接入', 'Runner')}
            <select value={draft.backend} disabled={busy} onChange={event => update({ backend: event.target.value })}
              className="mt-1 h-9 w-full rounded border border-line bg-bg px-2 text-xs text-ink">
              <option value="">{text('选择接入', 'Choose a runner')}</option>
              {query.data?.supported_backends.map(backend => <option key={backend} value={backend}>{backend}</option>)}
            </select>
          </label>
          <label className="text-xs text-ink-dim">{text('顾问使用的模型', 'Advisor model ID')}
            <input value={draft.model} list={modelList} disabled={busy} onChange={event => update({ model: event.target.value })}
              placeholder={text('填写该接入的模型标识', 'Model ID for this runner')}
              className="mt-1 h-9 w-full rounded border border-line bg-bg px-2 text-xs text-ink" />
            <datalist id={modelList}>{query.data?.model_options?.filter(option => option.backend === draft.backend)
              .map(option => <option key={option.model} value={option.model} />)}</datalist>
          </label>
          <label className="text-xs text-ink-dim">{text('思考强度', 'Reasoning effort')}
            <select value={draft.effort} disabled={busy} onChange={event => update({ effort: event.target.value })}
              className="mt-1 h-9 w-full rounded border border-line bg-bg px-2 text-xs text-ink">
              <option value="">{text('模型默认', 'Model default')}</option>
              {[['minimal', '最低'], ['low', '低'], ['medium', '中'], ['high', '高'], ['xhigh', '很高'], ['max', '最高']].map(([value, label]) => <option key={value} value={value}>{zh ? label : value}</option>)}
            </select>
          </label>
          <label className="text-xs text-ink-dim">{text('每次角色调用最多咨询', 'Consultations per role call')}
            <input type="number" min={1} max={20} value={draft.max_calls_per_turn} disabled={busy}
              onChange={event => update({ max_calls_per_turn: Number(event.target.value) })}
              className="mt-1 h-9 w-full rounded border border-line bg-bg px-2 text-xs text-ink" />
          </label>
        </div>
        {query.data?.overridden_fields.length ? <p className="mt-2 text-xs text-ink-dim">{text('运行服务另有指定设置；当前顾问：', 'The runtime overrides some saved settings. Current advisor: ')}{query.data.config.enabled ? `${query.data.config.backend} · ${query.data.config.model}` : text('未启用', 'disabled')}</p> : null}
        <div className="mt-3 flex items-center gap-3">
          <button type="button" onClick={() => void save()} disabled={busy || (draft.enabled && (!draft.backend || !draft.model.trim()))}
            className="rounded border border-line px-3 py-2 text-xs text-ink hover:border-blue disabled:opacity-40">{busy ? text('保存中…', 'Saving…') : text('保存顾问设置', 'Save advisor settings')}</button>
          {saved ? <span role="status" className="text-xs text-ink-dim">{text('已保存，下次咨询生效。', 'Saved for the next consultation.')}</span> : null}
        </div>
        {error ? <p role="alert" className="mt-2 text-xs text-err">{error}</p> : null}
      </> : null}
  </section>;
}
