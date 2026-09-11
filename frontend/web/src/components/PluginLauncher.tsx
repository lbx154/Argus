import { usePluginText } from '../lib/pluginText';
import { useI18n } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Boxes, Diamond, ArrowUpRight, X, Download, Loader2, RefreshCw, Ellipsis } from 'lucide-react';
import { authHeaders } from '../api';
import { PluginEnvironment, type PluginHealth, type PluginSetup } from './PluginEnvironment';
import { pluginEntryState, preparingSentence, unavailableSentence, type PluginEntry } from '../lib/pluginLaunch';

export type Plugin = PluginEntry & {
  description: string; version: string; url: string; command?: string; reason: string; installed_version?: string;
  rights_notice?: string; rights_notice_zh?: string;
  update_available: boolean; backends: Record<string, string>;
  health?: PluginHealth; setup?: PluginSetup;
  platform?: string; machine?: string;
};

type Act = (action: string, payload?: Record<string, unknown>) => Promise<boolean>;
const control = 'inline-flex items-center justify-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm transition-colors hover:bg-bg disabled:cursor-not-allowed disabled:opacity-45';
const entry = (compact: boolean) => `flex h-9 min-w-0 flex-1 items-center rounded-md text-sm text-ink-dim transition-colors hover:bg-bg hover:text-ink ${compact ? 'justify-center' : 'gap-2 px-3'}`;

// The sidebar and landing entries. An installed workbench opens with one click;
// a workbench the service is still preparing shows a single sentence and the
// current step; the plugin center itself stays the manual path for self-hosted copies.
export function PluginEntries({ plugins, compact = false, pending, onLaunch, onManage }: {
  plugins: Plugin[]; compact?: boolean; pending: string | null;
  onLaunch: (plugin: Plugin) => void; onManage: () => void;
}) {
  const tr = usePluginText();
  const entries = plugins.filter(plugin => pluginEntryState(plugin) !== 'manual');
  const manual = !plugins.length || plugins.some(plugin => pluginEntryState(plugin) === 'manual');
  return <>
    {entries.map(plugin => {
      const state = pluginEntryState(plugin);
      const manage = !compact && <button type="button" onClick={onManage} aria-label={tr(`管理 ${plugin.name}`)} title={tr(`管理 ${plugin.name}`)}
        className="icon-control mr-1 shrink-0 p-1.5 text-ink-faint hover:text-ink"><Ellipsis size={15}/></button>;
      if (state === 'ready') {
        return <div key={plugin.id} className="mx-2 my-1 flex shrink-0 items-center" data-testid={`plugin-entry-${plugin.id}`}>
          <button type="button" disabled={pending === plugin.id} onClick={() => onLaunch(plugin)} title={plugin.name} aria-label={plugin.name}
            className={`${entry(compact)} disabled:opacity-60`}>
            <Diamond size={17} strokeWidth={1.5} className="shrink-0 text-blue"/>
            {!compact && <span className="truncate">{plugin.name}</span>}
            {!compact && <ArrowUpRight size={13} className="shrink-0 text-ink-faint"/>}
          </button>
          {manage}
        </div>;
      }
      const sentence = state === 'preparing' ? preparingSentence(plugin.name) : unavailableSentence(plugin.name);
      const detail = state === 'preparing' ? plugin.operation?.progress : plugin.operation?.error || plugin.reason;
      return <div key={plugin.id} className="mx-2 my-1 flex shrink-0 items-center" data-testid={`plugin-entry-${plugin.id}`}>
        <div role="status" title={tr(detail || sentence)} className={`flex min-w-0 flex-1 items-center text-sm text-ink-faint ${compact ? 'h-9 justify-center' : 'gap-2 px-3 py-1.5'}`}>
          {state === 'preparing' ? <Loader2 size={17} strokeWidth={1.5} className="shrink-0 animate-spin"/> : <Diamond size={17} strokeWidth={1.5} className="shrink-0"/>}
          {!compact && <div className="min-w-0 leading-snug"><p>{tr(sentence)}</p>{detail && <p className="mt-0.5 truncate text-xs">{tr(detail)}</p>}</div>}
        </div>
        {manage}
      </div>;
    })}
    {manual && <button type="button" onClick={onManage} title={tr("插件")} aria-label={tr("插件")}
      className={`mx-2 my-1 flex h-9 shrink-0 items-center rounded-md text-sm text-ink-dim transition-colors hover:bg-bg hover:text-ink ${compact ? 'justify-center' : 'gap-2 px-3'}`}>
      <Boxes size={17} strokeWidth={1.5} />{!compact && <span>{tr("插件")}</span>}
    </button>}
  </>;
}

// One plugin inside the plugin center. A plugin the service provides keeps its
// environment tools but offers no install, update, disable or uninstall control.
export function PluginCard({ plugin, running, locale, act }: { plugin: Plugin; running: boolean; locale: string; act: Act }) {
  const tr = usePluginText();
  const managed = Boolean(plugin.managed_by_host);
  const state = pluginEntryState(plugin);
  const disabled = running || !plugin.supported;
  return <article className="rounded-xl border border-line/70 p-4" data-testid={`plugin-${plugin.id}`}>
    <div className="flex items-start gap-3"><Diamond size={24} strokeWidth={1.25} className="mt-0.5 shrink-0 text-blue"/>
      <div className="min-w-0 flex-1"><div className="flex items-baseline gap-2"><h3 className="font-medium">{tr(plugin.name)}</h3><span className="text-xs text-ink-faint">{tr(plugin.installed_version || plugin.version)}</span></div>
        <p className="mt-1 text-sm leading-relaxed text-ink-faint">{tr(plugin.description)}</p></div>
    </div>
    <div className="mt-4 text-xs text-ink-faint">{tr(plugin.installed ? plugin.enabled ? tr("已启用") : tr("已停用") : tr("未安装"))}{tr(" · 当前后端 ")}{tr(Array.from(new Set(Object.values(plugin.backends))).join(' / '))}</div>
    {tr(!plugin.supported && <p className="mt-3 text-sm text-ink-dim">{tr(plugin.reason)}</p>)}
    {tr(managed && state === 'preparing' && <p className="mt-3 text-sm text-ink-dim">{tr(preparingSentence(plugin.name))}</p>)}
    {tr(plugin.operation?.status === 'running' && <p role="status" className="mt-3 flex items-center gap-2 text-sm text-ink-dim"><Loader2 size={14} className="animate-spin"/>{tr(plugin.operation.progress)}</p>)}
    {tr(plugin.operation?.status === 'failed' && <p role="status" className="mt-3 break-words text-sm text-ink-dim">{tr(plugin.operation.error)}</p>)}
    <div className="mt-4 flex flex-wrap items-center gap-2">
      {tr(!plugin.installed && !managed && <button className={control} disabled={disabled} onClick={() => void act('install')}><Download size={14}/>{tr("安装")}</button>)}
      {tr(plugin.installed && plugin.enabled && <button className={control} disabled={disabled} onClick={() => void act('launch')}>{tr("打开工作台")}<ArrowUpRight size={14}/></button>)}
      {tr(plugin.installed && !plugin.enabled && !managed && <button className={control} disabled={disabled} onClick={() => void act('enable')}>{tr("启用")}</button>)}
      {tr(plugin.update_available && !managed && <button className={control} disabled={disabled} onClick={() => void act('update')}><RefreshCw size={14}/>{tr("更新至 ")}{tr(plugin.version)}</button>)}
      {tr(plugin.installed && plugin.enabled && !managed && <button className={control} disabled={running} onClick={() => void act('disable')}>{tr("停用")}</button>)}
      {tr(plugin.installed && !managed && <button className={control} disabled={running} onClick={() => void act('uninstall')}>{tr("卸载")}</button>)}
    </div>
    {tr(plugin.installed && <p className="mt-4 text-xs leading-relaxed text-ink-faint">{tr(managed ? tr(`原生会话输入 ${plugin.command || ''} 可启用后台工具。`) : tr(`原生会话输入 ${plugin.command || ''} 可启用后台工具。卸载保留会话、研究数据和科学软件。`))}</p>)}
    {tr(!plugin.installed && !managed && <p className="mt-4 text-xs leading-relaxed text-ink-faint">{tr("首次安装自动配置独立 Python、DIALS、Systre / Java 和 PLATON 学术免费组件。SHELX 稍后输入授权信息即可安装。")}</p>)}
    {tr(plugin.rights_notice && <p className="mt-3 text-[10px] leading-relaxed text-ink-faint" data-testid="plugin-rights-notice">{tr(locale === 'zh-CN' ? plugin.rights_notice_zh || plugin.rights_notice : plugin.rights_notice)}</p>)}
    {tr(plugin.installed && plugin.setup && <PluginEnvironment health={plugin.health} setup={plugin.setup} running={running} platform={plugin.platform} machine={plugin.machine} act={act}/>)}
  </article>;
}

export function PluginLauncher({ compact = false }: { compact?: boolean }) {
  const tr = usePluginText();
  const { locale } = useI18n();
  const [open, setOpen] = useState(false);
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const close = useRef<HTMLButtonElement>(null);
  const preparing = plugins.some(plugin => pluginEntryState(plugin) === 'preparing');
  const provided = plugins.length > 0 && plugins.every(plugin => plugin.managed_by_host);
  async function refresh(signal?: AbortSignal) {
    const response = await fetch('/api/plugins', { headers: authHeaders(), signal });
    if (!response.ok) throw new Error(tr("无法读取插件列表"));
    setPlugins((await response.json()).plugins);
  }
  useEffect(() => {
    const controller = new AbortController();
    const load = () => refresh(controller.signal).catch(e => { if (!controller.signal.aborted && open) setError(e.message); });
    if (open) { close.current?.focus(); setError(''); setLoading(true); }
    void load().finally(() => { if (!controller.signal.aborted) setLoading(false); });
    // The list is read once on arrival, then followed while the plugin center is
    // open and, more slowly, while the service is still preparing a workbench.
    const timer = open || preparing ? window.setInterval(load, open ? 1500 : 4000) : 0;
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    if (open) window.addEventListener('keydown', key);
    return () => { controller.abort(); if (timer) window.clearInterval(timer); window.removeEventListener('keydown', key); };
  }, [open, preparing]);
  async function act(plugin: Plugin, action: string, payload?: Record<string, unknown>): Promise<boolean> {
    setPending(plugin.id); setError('');
    try {
      const response = await fetch(`/api/plugins/${plugin.id}/${action === 'launch' ? 'launch' : `manage/${action}`}`, { method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' }, body: payload ? JSON.stringify(payload) : undefined });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || tr("插件操作未完成"));
      if (action === 'launch') window.location.assign(result.url);
      else await refresh();
      return true;
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); return false; }
    finally { setPending(null); }
  }
  return <>
    <PluginEntries plugins={plugins} compact={compact} pending={pending} onLaunch={plugin => void act(plugin, 'launch')} onManage={() => setOpen(true)} />
    {open && createPortal(<div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/20 p-5 backdrop-blur-sm" onClick={() => setOpen(false)}>
      <section role="dialog" aria-modal="true" aria-labelledby="plugin-title" onClick={e => e.stopPropagation()}
        className="max-h-[85vh] w-full max-w-xl overflow-y-auto rounded-2xl border border-line bg-panel p-6 text-ink shadow-xl">
        <div className="flex items-center justify-between"><h2 id="plugin-title" className="text-lg font-semibold">{tr("插件")}</h2>
          <button ref={close} type="button" aria-label={tr("关闭插件列表")} className="icon-control p-1.5" onClick={() => setOpen(false)}><X size={18}/></button></div>
        <p className="mb-6 mt-2 text-sm text-ink-faint">{tr(provided ? tr("研究工具由服务方提供，沿用 Argus 的模型与执行后端。") : tr("按需安装研究工具，沿用 Argus 的模型与执行后端。"))}</p>
        {tr(error && <p role="alert" className="mb-4 text-sm text-ink-dim">{tr(error)}</p>)}
        {tr(loading && <p className="text-sm text-ink-faint">{tr("正在读取插件…")}</p>)}
        {tr(!loading && !plugins.length && <p className="text-sm text-ink-faint">{tr("暂无可用插件")}</p>)}
        {tr(plugins.map(plugin => <PluginCard key={plugin.id} plugin={plugin} locale={locale}
          running={plugin.operation?.status === 'running' || pending === plugin.id}
          act={(action, payload) => act(plugin, action, payload)}/>))}
      </section>
    </div>, document.body)}
  </>;
}
