import { useI18n } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Boxes, Diamond, ArrowUpRight, X, Ellipsis } from 'lucide-react';
import { authHeaders } from '../api';

export interface Plugin {
  id: string;
  name: string;
  description: string;
  version: string;
  url: string;
  reason: string;
}

const control = 'inline-flex items-center justify-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm transition-colors hover:bg-bg disabled:cursor-not-allowed disabled:opacity-45';
const entry = (compact: boolean) => `flex h-9 min-w-0 flex-1 items-center rounded-md text-sm text-ink-dim transition-colors hover:bg-bg hover:text-ink ${compact ? 'justify-center' : 'gap-2 px-3'}`;

/** Plugins are deployed by their owner; the cockpit lists them and opens their URL. */
export function PluginEntries({ plugins, compact = false, pending, onLaunch, onManage }: {
  plugins: Plugin[]; compact?: boolean; pending: string | null;
  onLaunch: (plugin: Plugin) => void; onManage: () => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  return <>
    {plugins.filter(plugin => plugin.url && !plugin.reason).map(plugin => <div key={plugin.id} className="mx-2 my-1 flex shrink-0 items-center" data-testid={`plugin-entry-${plugin.id}`}>
      <button type="button" disabled={pending === plugin.id} onClick={() => onLaunch(plugin)} title={plugin.name} aria-label={plugin.name}
        className={`${entry(compact)} disabled:opacity-60`}>
        <Diamond size={17} strokeWidth={1.5} className="shrink-0 text-blue"/>
        {!compact && <span className="truncate">{plugin.name}</span>}
        {!compact && <ArrowUpRight size={13} className="shrink-0 text-ink-faint"/>}
      </button>
      {!compact && <button type="button" onClick={onManage} aria-label={zh ? `查看 ${plugin.name}` : `View ${plugin.name}`} title={plugin.name}
        className="icon-control mr-1 shrink-0 p-1.5 text-ink-faint hover:text-ink"><Ellipsis size={15}/></button>}
    </div>)}
    {(!plugins.length || plugins.some(plugin => plugin.reason || !plugin.url)) && <div className="mx-2 my-1 flex shrink-0 items-center">
      <button type="button" onClick={onManage} title={zh ? '插件' : 'Plugins'} aria-label={zh ? '插件' : 'Plugins'} className={entry(compact)}>
        <Boxes size={16} strokeWidth={1.5} className="shrink-0" />{!compact && <span>{zh ? '插件' : 'Plugins'}</span>}
      </button>
    </div>}
  </>;
}

export function PluginCard({ plugin, running, onLaunch }: { plugin: Plugin; running: boolean; onLaunch: () => void }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  return <article className="rounded-xl border border-line/70 p-4" data-testid={`plugin-${plugin.id}`}>
    <div className="flex items-start gap-3"><Diamond size={24} strokeWidth={1.25} className="mt-0.5 shrink-0 text-blue"/>
      <div className="min-w-0 flex-1"><div className="flex items-baseline gap-2"><h3 className="font-medium">{plugin.name}</h3><span className="text-xs text-ink-faint">{plugin.version}</span></div>
        <p className="mt-1 text-sm leading-relaxed text-ink-faint">{plugin.description}</p></div>
    </div>
    {plugin.reason ? <p className="mt-3 text-xs leading-5 text-ink-dim">{plugin.reason}</p>
      : <button className={`${control} mt-3`} disabled={running || !plugin.url} onClick={onLaunch}>{zh ? '打开工作台' : 'Open workbench'}<ArrowUpRight size={14}/></button>}
  </article>;
}

export function PluginLauncher({ compact = false }: { compact?: boolean }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const [open, setOpen] = useState(false);
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const close = useRef<HTMLButtonElement>(null);
  async function refresh(signal?: AbortSignal) {
    const response = await fetch('/api/plugins', { headers: authHeaders(), signal });
    if (!response.ok) throw new Error(zh ? '无法读取插件列表' : 'Could not load plugins');
    const value = await response.json();
    if (!signal?.aborted) setPlugins(value.plugins);
  }
  useEffect(() => {
    const controller = new AbortController();
    if (open) { close.current?.focus(); setLoading(true); }
    void refresh(controller.signal).catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    if (open) window.addEventListener('keydown', key);
    return () => { controller.abort(); window.removeEventListener('keydown', key); };
  }, [open]);
  async function launch(plugin: Plugin) {
    setPending(plugin.id); setError('');
    try {
      const response = await fetch(`/api/plugins/${encodeURIComponent(plugin.id)}/launch`, { method: 'POST', headers: authHeaders() });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || (zh ? '插件操作未完成' : 'Could not open plugin'));
      window.location.assign(result.url);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); setOpen(true); }
    finally { setPending(null); }
  }
  return <>
    <PluginEntries plugins={plugins} compact={compact} pending={pending} onLaunch={plugin => void launch(plugin)} onManage={() => setOpen(true)} />
    {open && createPortal(<div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/20 p-5 backdrop-blur-sm" onClick={() => setOpen(false)}>
      <section role="dialog" aria-modal="true" aria-labelledby="plugin-title" onClick={e => e.stopPropagation()}
        className="max-h-[85vh] w-full max-w-xl overflow-y-auto rounded-2xl border border-line bg-panel p-6 text-ink shadow-xl">
        <div className="flex items-center justify-between"><h2 id="plugin-title" className="text-lg font-semibold">{zh ? '插件' : 'Plugins'}</h2>
          <button ref={close} type="button" aria-label={zh ? '关闭插件列表' : 'Close plugins'} className="icon-control p-1.5" onClick={() => setOpen(false)}><X size={18}/></button></div>
        <p className="mb-6 mt-2 text-sm text-ink-faint">{zh ? '打开已部署的研究工具。' : 'Open deployed research tools.'}</p>
        {error && <p role="alert" className="mb-4 text-sm text-ink-dim">{error}</p>}
        {loading && <p className="text-sm text-ink-faint">{zh ? '正在读取插件…' : 'Loading plugins…'}</p>}
        {!loading && !error && !plugins.length && <p className="text-sm text-ink-faint">{zh ? '暂无可用插件' : 'No plugins available'}</p>}
        {error && <button type="button" className={`${control} mb-3`} onClick={() => { setLoading(true); void refresh().then(() => setError('')).catch(e => setError(e instanceof Error ? e.message : String(e))).finally(() => setLoading(false)); }}>{zh ? '重新读取' : 'Retry loading'}</button>}
        {plugins.map(plugin => <PluginCard key={plugin.id} plugin={plugin} running={pending === plugin.id} onLaunch={() => void launch(plugin)} />)}
      </section>
    </div>, document.body)}
  </>;
}
