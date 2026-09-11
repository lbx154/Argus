import { usePluginText } from '../lib/pluginText';
import { useI18n } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Boxes, Diamond, ArrowUpRight, X, Download, Loader2, RefreshCw } from 'lucide-react';
import { authHeaders } from '../api';
import { PluginEnvironment, type PluginHealth, type PluginSetup } from './PluginEnvironment';

type Plugin = {
  id: string; name: string; description: string; version: string; url: string; command?: string;
  installed: boolean; enabled: boolean; supported: boolean; reason: string; installed_version?: string;
  rights_notice?: string; rights_notice_zh?: string;
  update_available: boolean; backends: Record<string, string>;
  operation?: { status?: string; progress?: string; error?: string };
  health?: PluginHealth; setup?: PluginSetup;
  platform?: string; machine?: string;
};

export function PluginLauncher({ compact = false }: { compact?: boolean }) {
  const tr = usePluginText();
  const { locale } = useI18n();
  const [open, setOpen] = useState(false);
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const close = useRef<HTMLButtonElement>(null);
  async function refresh(signal?: AbortSignal) {
    const response = await fetch('/api/plugins', { headers: authHeaders(), signal });
    if (!response.ok) throw new Error(tr("无法读取插件列表"));
    setPlugins((await response.json()).plugins);
  }
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    close.current?.focus(); setError(''); setLoading(true);
    refresh(controller.signal).catch(e => { if (!controller.signal.aborted) setError(e.message); }).finally(() => setLoading(false));
    const timer = window.setInterval(() => void refresh(controller.signal).catch(() => {}), 1500);
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', key);
    return () => { controller.abort(); window.clearInterval(timer); window.removeEventListener('keydown', key); };
  }, [open]);
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
    <button type="button" onClick={() => setOpen(true)} title={tr("插件")} aria-label={tr("插件")}
      className={`mx-2 my-1 flex h-9 shrink-0 items-center rounded-md text-sm text-ink-dim transition-colors hover:bg-bg hover:text-ink ${compact ? 'justify-center' : 'gap-2 px-3'}`}>
      <Boxes size={17} strokeWidth={1.5} />{tr(!compact && <span>{tr("插件")}</span>)}
    </button>
    {open && createPortal(<div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/20 p-5 backdrop-blur-sm" onClick={() => setOpen(false)}>
      <section role="dialog" aria-modal="true" aria-labelledby="plugin-title" onClick={e => e.stopPropagation()}
        className="max-h-[85vh] w-full max-w-xl overflow-y-auto rounded-2xl border border-line bg-panel p-6 text-ink shadow-xl">
        <div className="flex items-center justify-between"><h2 id="plugin-title" className="text-lg font-semibold">{tr("插件")}</h2>
          <button ref={close} type="button" aria-label={tr("关闭插件列表")} className="icon-control p-1.5" onClick={() => setOpen(false)}><X size={18}/></button></div>
        <p className="mb-6 mt-2 text-sm text-ink-faint">{tr("按需安装研究工具，沿用 Argus 的模型与执行后端。")}</p>
        {tr(error && <p role="alert" className="mb-4 text-sm text-ink-dim">{tr(error)}</p>)}
        {tr(loading && <p className="text-sm text-ink-faint">{tr("正在读取插件…")}</p>)}
        {tr(!loading && !plugins.length && <p className="text-sm text-ink-faint">{tr("暂无可用插件")}</p>)}
        {tr(plugins.map(plugin => {
          const running = plugin.operation?.status === 'running' || pending === plugin.id;
          const disabled = running || !plugin.supported;
          const control = 'inline-flex items-center justify-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm transition-colors hover:bg-bg disabled:cursor-not-allowed disabled:opacity-45';
          return <article key={plugin.id} className="rounded-xl border border-line/70 p-4" data-testid={`plugin-${plugin.id}`}>
            <div className="flex items-start gap-3"><Diamond size={24} strokeWidth={1.25} className="mt-0.5 shrink-0 text-blue"/>
              <div className="min-w-0 flex-1"><div className="flex items-baseline gap-2"><h3 className="font-medium">{tr(plugin.name)}</h3><span className="text-xs text-ink-faint">{tr(plugin.installed_version || plugin.version)}</span></div>
                <p className="mt-1 text-sm leading-relaxed text-ink-faint">{tr(plugin.description)}</p></div>
            </div>
            <div className="mt-4 text-xs text-ink-faint">{tr(plugin.installed ? plugin.enabled ? tr("已启用") : tr("已停用") : tr("未安装"))}{tr(" · 当前后端 ")}{tr(Array.from(new Set(Object.values(plugin.backends))).join(' / '))}</div>
            {tr(!plugin.supported && <p className="mt-3 text-sm text-ink-dim">{tr(plugin.reason)}</p>)}
            {tr(plugin.operation?.status === 'running' && <p role="status" className="mt-3 flex items-center gap-2 text-sm text-ink-dim"><Loader2 size={14} className="animate-spin"/>{tr(plugin.operation.progress)}</p>)}
            {tr(plugin.operation?.status === 'failed' && <p role="status" className="mt-3 break-words text-sm text-ink-dim">{tr(plugin.operation.error)}</p>)}
            <div className="mt-4 flex flex-wrap items-center gap-2">
              {tr(!plugin.installed && <button className={control} disabled={disabled} onClick={() => void act(plugin, 'install')}><Download size={14}/>{tr("安装")}</button>)}
              {tr(plugin.installed && plugin.enabled && <button className={control} disabled={disabled} onClick={() => void act(plugin, 'launch')}>{tr("打开工作台")}<ArrowUpRight size={14}/></button>)}
              {tr(plugin.installed && !plugin.enabled && <button className={control} disabled={disabled} onClick={() => void act(plugin, 'enable')}>{tr("启用")}</button>)}
              {tr(plugin.update_available && <button className={control} disabled={disabled} onClick={() => void act(plugin, 'update')}><RefreshCw size={14}/>{tr("更新至 ")}{tr(plugin.version)}</button>)}
              {tr(plugin.installed && plugin.enabled && <button className={control} disabled={running} onClick={() => void act(plugin, 'disable')}>{tr("停用")}</button>)}
              {tr(plugin.installed && <button className={control} disabled={running} onClick={() => void act(plugin, 'uninstall')}>{tr("卸载")}</button>)}
            </div>
            <p className="mt-4 text-xs leading-relaxed text-ink-faint">{tr(plugin.installed ? tr(`原生会话输入 ${plugin.command || ''} 可启用后台工具。卸载保留会话、研究数据和科学软件。`) : tr("首次安装自动配置独立 Python、DIALS、Systre / Java 和 PLATON 学术免费组件。SHELX 稍后输入授权信息即可安装。"))}</p>
            {tr(plugin.rights_notice && <p className="mt-3 text-[10px] leading-relaxed text-ink-faint" data-testid="plugin-rights-notice">{tr(locale === 'zh-CN' ? plugin.rights_notice_zh || plugin.rights_notice : plugin.rights_notice)}</p>)}
            {tr(plugin.installed && plugin.setup && <PluginEnvironment health={plugin.health} setup={plugin.setup} running={running} platform={plugin.platform} machine={plugin.machine} act={(action, payload) => act(plugin, action, payload)}/>)}
          </article>;
        }))}
      </section>
    </div>, document.body)}
  </>;
}
