import { usePluginText } from '../lib/pluginText';
import { useState } from 'react';
import { Check, CircleHelp, Download, KeyRound, RefreshCw, ShieldCheck, ChevronDown, ExternalLink } from 'lucide-react';

export type PluginHealth = {
  checked?: number; ready?: boolean; summary?: string;
  components?: { id: string; name: string; description: string; status: string; detail: string; path?: string; url: string; automatic: boolean; license_required: boolean }[];
};
export type PluginSetup = { actions: string[]; windows_runtime?: { action: string; name: string; url: string; notice: string }; license?: { action: string; name: string; url: string; platform_consent?: { platform: string; machines: string[]; text: string; url: string } } };
const control = 'inline-flex items-center justify-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm transition-colors hover:bg-bg disabled:cursor-not-allowed disabled:opacity-45';

export function PluginEnvironment({ health, setup, running, act, platform, machine }: {
  health?: PluginHealth; setup: PluginSetup; running: boolean;
  platform?: string; machine?: string;
  act: (action: string, payload?: Record<string, unknown>) => Promise<boolean>;
}) {
  const tr = usePluginText();
  const [expanded, setExpanded] = useState(false);
  const [credentials, setCredentials] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [manual, setManual] = useState<string | null>(null);
  const [path, setPath] = useState('');
  const [acceptPlatform, setAcceptPlatform] = useState(false);
  const [runtimeConsent, setRuntimeConsent] = useState(false);
  const [showRuntime, setShowRuntime] = useState(false);
  const runtime = setup.windows_runtime;
  const terms = setup.license?.platform_consent;
  const platformConsent = terms?.platform === platform && terms?.machines.includes(machine || '');
  const components = health?.components || [];
  const missing = components.filter(c => c.status !== 'ready');
  const needLicense = missing.some(c => c.license_required);
  const needRepair = missing.some(c => c.automatic);
  async function license(event: React.FormEvent) {
    event.preventDefault();
    const submitted = await act(setup.license!.action, { username, password, accept_platform_license: acceptPlatform });
    // Secrets stay in this form only until submitted/closed. No browser storage.
    setPassword('');
    if (submitted) { setCredentials(false); setUsername(''); setExpanded(true); }
  }
  return <div className="mt-5 border-t border-line/60 pt-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}
        className="inline-flex items-center gap-2 text-sm text-ink-dim hover:text-ink">
        <ShieldCheck size={16} strokeWidth={1.5}/>
        <span>{tr(health?.checked ? health.ready ? tr("科学环境已就绪") : tr(`科学环境 · ${missing.length} 项待配置`) : tr("科学环境"))}</span>
        <ChevronDown size={13} className={`transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}/>
      </button>
      <button type="button" className="inline-flex items-center gap-1.5 text-xs text-ink-faint hover:text-ink disabled:opacity-45"
        disabled={running} onClick={() => { setExpanded(true); void act('health'); }}><RefreshCw size={12}/>{tr("检查环境")}</button>
    </div>
    {tr((needLicense || needRepair || !health?.checked) && <p className="mt-2 text-xs leading-relaxed text-ink-faint">{tr(" 免费科学组件自动配置；SHELX 需要你在官网取得学术授权后输入下载凭据。 ")}</p>)}
    <div className="mt-3 flex flex-wrap gap-2">
      {tr((needRepair || !health?.checked) && <button className={control} disabled={running} onClick={() => { setExpanded(true); void act('repair'); }}><Download size={14}/>{tr("修复依赖")}</button>)}
      {tr(setup.license && <button className={control} disabled={running} onClick={() => { setCredentials(!credentials); setPassword(''); setUsername(''); }}><KeyRound size={14}/>{tr(needLicense ? tr("配置 SHELX") : tr("SHELX 授权安装"))}</button>)}
    </div>
    {runtime && <div className="mt-3">
      <button type="button" className={control} disabled={running} onClick={() => { setShowRuntime(!showRuntime); setRuntimeConsent(false); }}><Download size={14}/>{tr("准备 PLATON 官方环境")}</button>
      {showRuntime && <form className="mt-3 rounded-lg bg-bg/70 p-3" onSubmit={async e => {
        e.preventDefault();
        if (runtimeConsent && await act(runtime.action, { accept_software_license: true })) {
          setShowRuntime(false); setRuntimeConsent(false); setExpanded(true);
        }
      }}>
        <p className="text-xs leading-relaxed text-ink-dim">{tr(runtime.notice)} <a href={runtime.url} target="_blank" rel="noreferrer" className="text-blue">{tr("官方安装说明 ↗")}</a></p>
        <label className="mt-3 flex items-start gap-2 text-xs leading-relaxed text-ink-dim"><input type="checkbox" checked={runtimeConsent} onChange={e => setRuntimeConsent(e.target.checked)} className="mt-0.5"/>{tr("我确认用途符合 PLATON 官方许可；如用于商业用途，已另行取得授权。")}</label>
        <div className="mt-3 flex gap-3"><button type="submit" className={control} disabled={running || !runtimeConsent}>{tr("下载、验证并配置")}</button><button type="button" className="text-xs text-ink-faint" onClick={() => { setShowRuntime(false); setRuntimeConsent(false); }}>{tr("取消")}</button></div>
      </form>}
    </div>}
    {tr(credentials && setup.license && <form onSubmit={license} className="mt-4 rounded-lg bg-bg/70 p-3">
      <div className="flex items-center justify-between gap-2 text-sm"><span className="font-medium">{tr(setup.license.name)}{tr(" 学术授权")}</span>
        <a href={setup.license.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-blue">{tr("前往官网申请")}<ExternalLink size={11}/></a></div>
      <p className="mb-3 mt-1.5 text-xs leading-relaxed text-ink-faint">{tr("填写授权邮件中的 username 和 password，即可自动下载安装。凭据仅用于本次官方下载，不保存，也不发送给模型。")}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-ink-dim">{tr("用户名")}<input aria-label={tr("SHELX 用户名")} value={username} onChange={e => setUsername(e.target.value)} required maxLength={200} autoComplete="off" autoCapitalize="none" spellCheck={false} className="mt-1.5 w-full rounded-md border border-line bg-panel px-2.5 py-2 text-sm text-ink outline-none focus:border-blue/60"/></label>
        <label className="text-xs text-ink-dim">{tr("密码")}<input aria-label={tr("SHELX 密码")} type="password" value={password} onChange={e => setPassword(e.target.value)} required maxLength={500} autoComplete="new-password" className="mt-1.5 w-full rounded-md border border-line bg-panel px-2.5 py-2 text-sm text-ink outline-none focus:border-blue/60"/></label>
      </div>
      {tr(platformConsent && terms && <label className="mt-3 flex items-start gap-2 text-xs leading-relaxed text-ink-faint"><input type="checkbox" checked={acceptPlatform} onChange={e => setAcceptPlatform(e.target.checked)} className="mt-0.5"/><span>{tr(terms.text)} <a href={terms.url} target="_blank" rel="noreferrer" className="text-blue">{tr("查看许可 ↗")}</a></span></label>)}
      <div className="mt-3 flex gap-2"><button type="submit" className={control} disabled={running || !username.trim() || !password.trim()}><Download size={14}/>{tr("下载并安装 SHELX")}</button>
        <button type="button" className="px-2 text-xs text-ink-faint" onClick={() => { setCredentials(false); setUsername(''); setPassword(''); }}>{tr("取消")}</button></div>
    </form>)}
    {expanded && <div className="mt-3" aria-label={tr("科学软件健康检查")}>
      {tr(!components.length && <p className="py-2 text-xs text-ink-faint">{tr("点击“检查环境”可验证科学内核与外部程序。")}</p>)}
      {components.map(component => <div key={component.id} className="border-b border-line/40 py-2.5 last:border-0">
        <div className="flex items-start gap-2.5">
          {tr(component.status === 'ready' ? <Check size={15} strokeWidth={1.7} className="mt-0.5 shrink-0 text-blue/75"/> : <CircleHelp size={15} strokeWidth={1.5} className="mt-0.5 shrink-0 text-ink-faint"/>)}
          <div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-3 text-sm"><span>{tr(component.name)}</span><span className="shrink-0 text-xs text-ink-faint">{tr(component.status === 'ready' ? tr("可用") : component.license_required ? tr("待授权安装") : tr("待修复"))}</span></div>
            <p className="mt-1 text-xs leading-relaxed text-ink-faint">{tr(component.description)}</p>
            <details className="mt-1.5 text-xs text-ink-faint"><summary className="cursor-pointer hover:text-ink-dim">{tr(component.status === 'ready' ? tr("查看检查详情") : tr("查看原因与安装方式"))}</summary>
              <p className="mt-2 whitespace-pre-wrap break-words leading-relaxed">{tr(component.detail)}</p>
              {component.path && <p className="mt-1 break-all leading-relaxed">{component.path}</p>}
              <div className="mt-2 flex flex-wrap gap-3"><a href={component.url} target="_blank" rel="noreferrer" className="text-blue">{tr("官方安装说明 ↗")}</a>
                {tr(component.id !== 'python' && setup.actions.includes('configure') && <button type="button" disabled={running} onClick={() => { setManual(component.id); setPath(''); }} className="text-blue">{tr("使用已有安装")}</button>)}</div>
            </details>
          </div>
        </div>
      </div>)}
      {tr(manual && <form onSubmit={async e => { e.preventDefault(); if (await act('configure', { paths: { [manual]: path } })) { setManual(null); setPath(''); } }} className="mt-3 rounded-lg bg-bg/70 p-3">
        <label className="text-xs text-ink-dim">{tr(manual === 'dials' ? tr("DIALS 环境目录") : manual === 'systre' ? tr("Systre JAR 路径（需要已有 Java）") : tr(`${manual.toUpperCase()} 可执行文件路径`))}<input required value={path} onChange={e => setPath(e.target.value)} className="mt-2 w-full rounded-md border border-line bg-panel px-2 py-2 text-sm text-ink"/></label>
        <p className="mt-1.5 text-xs text-ink-faint">{tr("填写运行 Argus 的电脑上的路径，验证成功后生效。")}</p>
        <div className="mt-3 flex gap-3"><button className={control} disabled={running}>{tr("验证并使用")}</button><button type="button" className="text-xs text-ink-faint" onClick={() => setManual(null)}>{tr("取消")}</button></div>
      </form>)}
      {tr(health?.checked && <p className="mt-2 text-[11px] text-ink-faint">{tr("最近检查 ")}{tr(new Date(health.checked * 1000).toLocaleString())}{tr(" · 检查不调用模型")}</p>)}
    </div>}
  </div>;
}
