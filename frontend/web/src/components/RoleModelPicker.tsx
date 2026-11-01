import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type ConfigSnapshot, type ModelProbeResult, type RoleConfigChoice } from '../api';
import { useConfig } from '../hooks';
import { useI18n } from '../i18n';
import { AGENT_ROLES, agentRoleName, type AgentRole } from '../lib/agentRoles';
import { backendLabel } from '../lib/backend';
import { requestFailureText } from '../lib/requestFailure';
import { Modal, ModalHeader } from './Modal';
import { Spinner } from './primitives';
import { ErrorMessage } from './ErrorMessage';
import './roleModelPicker.css';
import { RolePrice } from './RolePrice';

const draftOf = (config: ConfigSnapshot): RoleConfigChoice[] => AGENT_ROLES.map(role => {
  const saved = config.roles.find(row => row.role === role);
  return { role, backend: saved?.backend ?? 'codex', model: saved?.model_automatic ? '' : saved?.model ?? '', effort: saved?.reasoning_effort ?? '' };
});
const same = (a: RoleConfigChoice, b: RoleConfigChoice) => a.backend === b.backend && a.model === b.model && a.effort === b.effort;

/** Four independent routes, edited in one revision-checked transaction. */
export function RoleModelPicker({ sid, open, onClose, onOpenSettings }: {
  sid: string; open: boolean; onClose: () => void; onOpenSettings?: () => void;
}) {
  const { locale, t } = useI18n();
  const l = (zh: string, en: string) => locale === 'zh-CN' ? zh : en;
  const client = useQueryClient();
  const config = useConfig(sid, open);
  const [base, setBase] = useState<{ rows: RoleConfigChoice[]; revision: number } | null>(null);
  const [rows, setRows] = useState<RoleConfigChoice[]>([]);
  const [active, setActive] = useState<AgentRole>('manager');
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [failure, setFailure] = useState('');
  const [probe, setProbe] = useState<{ key: string; result?: ModelProbeResult; error?: string } | null>(null);
  const memory = useRef(new Map<string, RoleConfigChoice>());
  const lifetime = useRef(0);

  useEffect(() => {
    if (!open) {
      setBase(null); setRows([]); setFailure(''); setProbe(null); setSearch('');
      memory.current.clear(); lifetime.current++;
    } else if (!base && config.data) {
      const rows = draftOf(config.data);
      setBase({ rows, revision: config.data.revision ?? 0 }); setRows(rows);
    }
  }, [open, base, config.data]);

  const selected = rows.find(row => row.role === active);
  const backend = selected?.backend ?? '';
  const available = config.data?.available_backends ?? config.data?.backends ?? {};
  const cliAvailable = Object.hasOwn(available, backend);
  const queryKey = ['role-model-choices', sid, active, backend];
  const choices = useQuery({ queryKey,
    queryFn: ({ signal }) => api.modelChoices(sid, signal, { role: active, backend }),
    enabled: open && !!backend && cliAvailable, staleTime: 60_000, retry: false });
  const catalog = choices.data?.backend === backend ? choices.data : undefined;
  const options = catalog?.options ?? [];
  const selectedOption = options.find(option => option.model === selected?.model);
  const defaultOption = options.find(option => option.default);
  const effortOptions = selectedOption?.efforts ?? (!selected?.model ? defaultOption?.efforts : undefined);
  const changes = rows.filter(row => !base?.rows.some(previous => previous.role === row.role && same(row, previous)));
  const conflict = base && config.data?.revision !== base.revision;
  const probeKey = JSON.stringify([active, backend, selected?.model, selected?.effort]);
  const shownProbe = probe?.key === probeKey ? probe : null;

  const update = (patch: Partial<RoleConfigChoice>) => {
    setFailure('');
    setRows(previous => previous.map(row => row.role === active ? { ...row, ...patch } : row));
  };
  const chooseCli = (backend: string) => {
    if (!selected || backend === selected.backend) return;
    memory.current.set(`${active}/${selected.backend}`, selected);
    const remembered = memory.current.get(`${active}/${backend}`);
    update({ backend, model: remembered?.model ?? '', effort: remembered?.effort ?? '' });
    setSearch(''); setProbe(null);
  };
  const refresh = async () => {
    if (refreshing) return;
    setRefreshing(true); setFailure('');
    try {
      await config.refetch();
      if (cliAvailable) client.setQueryData(queryKey, await api.modelChoices(sid, undefined, { role: active, backend, refresh: true }));
      await client.invalidateQueries({ queryKey: ['role-model-choices', sid] });
    }
    catch (error) { setFailure(requestFailureText(error, t).text); }
    finally { setRefreshing(false); }
  };
  const testModel = async () => {
    if (!selected) return;
    const key = probeKey, generation = lifetime.current;
    setProbe({ key });
    try {
      const result = await api.probeModel(sid, selected.model, { role: active, backend, effort: selected.effort });
      if (generation === lifetime.current) setProbe({ key, result });
    } catch (error) { if (generation === lifetime.current) setProbe({ key, error: requestFailureText(error, t).text }); }
  };
  const save = async () => {
    if (busy || !base || !changes.length) return;
    setBusy(true); setFailure('');
    try {
      await api.setRoleConfig(sid, changes, base.revision);
      await Promise.all([
        client.invalidateQueries({ queryKey: ['config', sid] }),
        client.invalidateQueries({ queryKey: ['snapshot', sid] }),
        client.invalidateQueries({ queryKey: ['model-choices', sid] }),
        client.invalidateQueries({ queryKey: ['role-model-choices', sid] }),
      ]);
      onClose();
    } catch (error) { setFailure(requestFailureText(error, t).text); }
    finally { setBusy(false); }
  };
  const filtered = options.filter(option => option.model !== 'default' && `${option.model} ${option.name ?? ''} ${option.provider ?? ''}`.toLowerCase().includes(search.trim().toLowerCase()));
  const modelLabel = (model: string) => model || l('使用 CLI 默认模型', 'Use CLI default');
  const selectModel = (model: string) => {
    const supported = options.find(option => option.model === model)?.efforts;
    update({ model, ...selected?.effort && supported && !supported.includes(selected.effort) ? { effort: '' } : {} });
  };

  return <Modal open={open} onClose={() => { if (!busy) onClose(); }} label={l('团队模型与 Agent CLI', 'Team models & Agent CLIs')} width="max-w-4xl" align="bottom">
    <ModalHeader title={l('给每个角色选对工具', 'The right tools for each role')}
      sub={l('四个角色独立选择 CLI 与模型，保存后从各自的下一轮生效。', 'Choose a CLI and model for each role. Saved changes apply on its next turn.')} />
    <div className="role-picker" data-role-model-picker>
      {!base && <div className="role-picker-loading"><Spinner /></div>}
      {base && selected && <>
        <div className="role-route-tabs" role="tablist" aria-label={l('团队角色', 'Team roles')}>
          {AGENT_ROLES.map(role => {
            const route = rows.find(row => row.role === role)!;
            const changed = changes.some(row => row.role === role);
            return <button type="button" key={role} role="tab" aria-selected={role === active} data-role-tab={role}
              id={`route-tab-${role}`} aria-controls="role-route-editor" disabled={busy}
              onClick={() => { setActive(role); setSearch(''); setFailure(''); }}>
              <span className="role-route-name">{agentRoleName(role, t)}{changed && <span className="role-route-changed" title={l('未保存', 'Unsaved')}>●</span>}</span>
              <span className="role-route-cli">{backendLabel(route.backend, config.data)}</span>
              <span className="role-route-model" title={route.model}>{modelLabel(route.model)}</span>
              <RolePrice sid={sid} route={route} available={Object.hasOwn(available, route.backend)} />
            </button>;
          })}
        </div>
        <section id="role-route-editor" role="tabpanel" aria-labelledby={`route-tab-${active}`} className="role-route-editor">
          <div className="role-route-controls">
            <label><span>Agent CLI</span>
              <select value={cliAvailable ? backend : ''} onChange={event => chooseCli(event.target.value)} disabled={busy} data-role-cli aria-label={l('Agent CLI 运行工具', 'Agent CLI')}>
                {!cliAvailable && <option value="" disabled>{l('请选择本机 CLI', 'Choose a local CLI')}</option>}
                {Object.entries(available).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
              </select>
            </label>
            <div className="role-cli-status" role="status">
              {!cliAvailable ? l('未发现原 CLI，请重选或设置路径；不会自动切换', 'CLI unavailable. Choose again or set its path; no automatic fallback.')
                : choices.isFetching ? l('正在从 CLI 获取模型…', 'Loading models from CLI…')
                : catalog?.installed === false ? l('未找到此 CLI，请先安装或检查路径', 'CLI not found. Install it or check its path.')
                : catalog?.error ? l('目录暂不可用，可重试或填写模型 ID', 'Catalog unavailable. Retry or enter a model ID.')
                : catalog?.catalog_kind === 'manual' ? l('此版本未提供可解析目录，请填写模型 ID', 'No readable catalog in this CLI version. Enter a model ID.')
                : catalog?.catalog_kind === 'aliases' ? l('CLI 内置模型别名；也支持完整模型 ID', 'Built-in CLI aliases; full model IDs are also accepted')
                : l(`目录 ${options.length} 个模型 · 可选 ${options.filter(model => model.available !== false).length} 个`, `${options.length} models · ${options.filter(model => model.available !== false).length} selectable`)}
            </div>
            <button type="button" className="role-picker-secondary" disabled={refreshing || choices.isFetching || busy} onClick={() => void refresh()} data-refresh-models>
              {refreshing ? l('刷新中…', 'Refreshing…') : l('刷新 CLI / 模型 / 价格', 'Refresh CLIs / models / rates')}
            </button>
          </div>
          <p className="role-picker-hint">{l('四个角色分别查询费率、按各自用量累计；CLI 声明价和公开参考价不等于订阅或账户账单。未知费率不按零元计算。', 'Each role has its own rates and usage. CLI/public reference prices are not subscription bills. Unknown rates are not zero.')}</p>
          <label className="role-model-search"><span>{l('搜索模型', 'Search models')}</span>
            <input value={search} onChange={event => setSearch(event.target.value)} placeholder={l('名称、提供商或模型 ID，例如 sol、astra', 'Name, provider or model ID, e.g. sol, astra')} data-role-model-search />
          </label>
          {choices.isLoading && <div className="role-picker-loading"><Spinner /></div>}
          {(choices.isError || catalog?.error) && <p className="role-picker-warning" role="alert">{l('无法完整读取模型目录。请确认 CLI 已登录；不会自动换用其他 CLI 或模型。', 'Could not load the full catalog. Check CLI sign-in; Argus will not silently switch your CLI or model.')}</p>}
          {!choices.isLoading && <div className="role-model-list" role="radiogroup" aria-label={l('此角色的模型', 'Model for this role')}>
            <label className="role-model-option" data-role-model="">
              <input type="radio" name="role-model" checked={!selected.model} onChange={() => selectModel('')} disabled={busy} />
              <span><strong>{l('使用 CLI 默认模型', 'Use CLI default')}</strong><small>{defaultOption?.model === 'default' ? defaultOption.name : defaultOption?.model || l('由此 CLI 决定', 'Chosen by this CLI')}</small></span>
            </label>
            {filtered.map(option => <label key={option.model} className="role-model-option" data-role-model={option.model}>
              <input type="radio" name="role-model" checked={selected.model === option.model} onChange={() => selectModel(option.model)} disabled={busy || option.available === false} />
              <span><strong>{option.name || option.model}</strong><small>{option.model}</small></span>
              {option.available === false && <em title={l('CLI 报告无法选择此模型，请刷新目录或检查账号权限', 'The CLI could not select this model. Refresh or check account access.')}>{l('暂不可选', 'Unavailable')}</em>}
              {option.default && <em>{l('CLI 默认', 'CLI default')}</em>}
              {option.price && <small className="role-model-price">${option.price.input} / ${option.price.output}<br />{l('输入 / 输出 · 每百万', 'in / out · per 1M')}</small>}
            </label>)}
            {search && !filtered.length && <p className="role-picker-hint">{l('没有匹配结果；也可以在下方输入完整模型 ID。', 'No matches. You can enter a complete model ID below.')}</p>}
          </div>}
          <div className="role-route-detail">
            <label><span>{l('模型 ID（支持自定义）', 'Model ID (custom supported)')}</span>
              <input value={selected.model} onChange={event => selectModel(event.target.value.trim())} spellCheck={false} disabled={busy}
                placeholder={backend === 'pi' || backend === 'dsh' ? 'provider/model-id' : l('留空使用 CLI 默认', 'Leave empty for CLI default')} data-role-model-id />
            </label>
            <label><span>{l('推理强度', 'Thinking level')}</span>
              <select value={selected.effort} onChange={event => update({ effort: event.target.value })} disabled={busy} data-role-effort>
                <option value="">{l('CLI / 模型默认', 'CLI / model default')}</option>
                {[...new Set([...(effortOptions ?? []), ...selected.effort ? [selected.effort] : []])].map(effort => <option key={effort} value={effort}>{effort}</option>)}
              </select>
            </label>
          </div>
          {!effortOptions && <p className="role-picker-hint">{l('暂未取得此模型的推理强度选项，保留 CLI 默认设置。', 'Thinking-level options are not available for this model yet; the CLI default is preserved.')}</p>}
          <div className="role-route-actions">
            <button type="button" className="role-picker-secondary" onClick={() => void testModel()} disabled={busy || !cliAvailable || catalog?.installed === false || (!!shownProbe && !shownProbe.result && !shownProbe.error)} data-test-role-model>
              {shownProbe && !shownProbe.result && !shownProbe.error ? l('测试中…', 'Testing…') : l('测试此配置', 'Test this route')}
            </button>
            <span className="role-picker-hint">{l('仅点击测试才会调用模型，可能产生费用。', 'Only testing calls a model and may incur charges.')}</span>
            <button type="button" className="role-picker-link" disabled={busy} data-copy-role onClick={() => {
              setRows(previous => previous.map(row => ({ ...selected, role: row.role }))); setFailure('');
            }}>{l('将此配置应用到全部角色', 'Apply this route to all roles')}</button>
          </div>
          {shownProbe?.result && <div role="status" className={shownProbe.result.ok ? 'role-picker-success' : 'role-picker-warning'}>
            {shownProbe.result.ok ? <>
              {l(`连接成功 · ${shownProbe.result.duration_ms} ms`, `Connected · ${shownProbe.result.duration_ms} ms`)}
              {shownProbe.result.actual_model && <span> · {shownProbe.result.actual_model}</span>}
            </> : <ErrorMessage error={shownProbe.result.error} code="model_probe_failed" />}
          </div>}
          {shownProbe?.error && <p role="alert" className="role-picker-warning">{shownProbe.error}</p>}
        </section>
        {!!config.data?.active_roles?.length && <p className="role-picker-warning">{l('团队正在运行：当前调用保持原配置。切换 CLI 或模型后，该角色下一轮将建立新会话，项目记录不变。', 'Work is running: current calls keep their settings. A CLI/model change starts a new role session next turn; project history is retained.')}</p>}
        {conflict && <p role="alert" className="role-picker-warning">{l('配置已在其他窗口变化。请重新载入后再保存。', 'Settings changed in another window. Reload before saving.')}
          <button type="button" onClick={() => { setBase(null); setFailure(''); }} className="role-picker-link">{l('重新载入（放弃草稿）', 'Reload (discard draft)')}</button>
        </p>}
        {onOpenSettings && <button type="button" className="role-picker-link" onClick={() => { onClose(); onOpenSettings(); }}>{l('CLI 安装与连接设置', 'CLI installation & connection settings')}</button>}
        <div className="role-picker-footer">
          <div><strong>{changes.length ? l(`${changes.length} 个角色有待保存的修改`, `${changes.length} role(s) changed`) : l('四个角色分别保存，互不干扰', 'Four independent role configurations')}</strong>
            <p className="role-picker-hint">{l('这里只修改当前项目；新项目继承最近创建项目的配置。', 'Applies to this project. New projects inherit the most recently created project’s settings.')}</p>
            {failure && <p role="alert" className="role-picker-warning">{failure}</p>}
          </div>
          <button type="button" className="model-picker-use" disabled={busy || !changes.length || !!conflict || changes.some(row => !Object.hasOwn(available, row.backend))} onClick={() => void save()} data-save-role-config>
            {busy ? l('保存中…', 'Saving…') : l('保存角色配置', 'Save role settings')}
          </button>
        </div>
      </>}
    </div>
  </Modal>;
}
