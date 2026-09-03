import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { AlertCircle, Antenna, Bell, Boxes, CheckSquare2, Command, Database, FlaskConical, GitBranch, Languages, LayoutDashboard, Menu, MessageSquareReply, MoonStar, PanelLeftClose, PanelLeftOpen, Radar, Search, Settings2, UsersRound, X, type LucideIcon } from 'lucide-react'
import { apiWebSocketUrl, loadDashboard, performAction, type DataMode } from './api/client'
import type { DashboardData } from './types'
import { ErrorState, LoadingState, StatusPill } from './components/ui'
import { HorizonPage } from './pages/HorizonPage'
import { CampaignDetailPage, CampaignsPage } from './pages/CampaignPages'
import { ApprovalPage, ConnectionsPage, IdeaRadarPage, OutcomesPage, ResourcesPage } from './pages/OtherPages'
import { ContextStudioPage } from './pages/ContextStudioPage'
import { DataVaultPage } from './pages/DataVaultPage'
import { useI18n, usePreferences } from './lib/preferences'

type AppContextValue = {
  data: DashboardData
  mode: DataMode
  toast: (message: string) => void
  act: (path: string, payload?: Record<string, unknown>) => Promise<boolean>
  refresh: () => Promise<void>
}

const AppContext = createContext<AppContextValue | null>(null)
export const useApp = () => {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('useApp must be used within AppContext')
  return ctx
}

type NavItem = { to: string; icon: LucideIcon; labelKey: string; purposeKey: string; end?: boolean }
type NavGroup = { labelKey: string; items: NavItem[] }

const navGroups: NavGroup[] = [
  { labelKey: 'nav.group.plan', items: [
    { to: '/', icon: LayoutDashboard, labelKey: 'nav.overview', purposeKey: 'nav.purpose.overview', end: true },
    { to: '/context', icon: UsersRound, labelKey: 'nav.context', purposeKey: 'nav.purpose.context' },
    { to: '/ideas', icon: Radar, labelKey: 'nav.ideas', purposeKey: 'nav.purpose.ideas' },
  ] },
  { labelKey: 'nav.group.run', items: [
    { to: '/campaigns', icon: FlaskConical, labelKey: 'nav.campaigns', purposeKey: 'nav.purpose.campaigns' },
  ] },
  { labelKey: 'nav.group.decide', items: [
    { to: '/data-vault', icon: Database, labelKey: 'nav.dataVault', purposeKey: 'nav.purpose.dataVault' },
    { to: '/outcomes', icon: MessageSquareReply, labelKey: 'nav.outcomes', purposeKey: 'nav.purpose.outcomes' },
  ] },
  { labelKey: 'nav.group.system', items: [
    { to: '/approvals', icon: CheckSquare2, labelKey: 'nav.approvals', purposeKey: 'nav.purpose.approvals' },
    { to: '/connections', icon: Antenna, labelKey: 'nav.connections', purposeKey: 'nav.purpose.connections' },
    { to: '/resources', icon: Boxes, labelKey: 'nav.settings', purposeKey: 'nav.purpose.settings' },
  ] },
]

const allNavItems = navGroups.flatMap((group) => group.items)

function ArgusMark({ size = 22 }: { size?: number }) {
  return (
    <svg viewBox="0 0 512 512" role="img" aria-label="Argus" style={{ width: size, height: size }}>
      <path d="M352 112q0-30 30-30h28q30 0 30 30v320h-88v-52q-46 62-129 62Q66 442 66 266T228 88q80 0 124 56v-32ZM140 266q46-80 102-80t110 80q-54 80-110 80t-102-80Z" fill="var(--brand-body)" fillRule="evenodd" />
      <path d="M140 266q46-80 102-80t110 80q-54 80-110 80t-102-80Z" fill="var(--brand-eye)" />
      <circle cx="244" cy="266" r="42" fill="var(--brand-pupil)" />
      <circle cx="262" cy="248" r="12" fill="var(--brand-highlight)" />
    </svg>
  )
}

function Shell({ children, mode }: { children: ReactNode; mode: DataMode }) {
  const { data } = useApp()
  const { t } = useI18n()
  const { locale, setLocale, theme, setTheme } = usePreferences()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [railCollapsed, setRailCollapsed] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const location = useLocation()
  const navigate = useNavigate()
  const activeNav = [...allNavItems]
    .sort((a, b) => b.to.length - a.to.length)
    .find((item) => item.end ? location.pathname === item.to : location.pathname.startsWith(item.to)) ?? allNavItems[0]
  const researchStep = location.pathname.startsWith('/context') ? 'context'
    : location.pathname.startsWith('/ideas') ? 'ideas'
      : location.pathname.startsWith('/campaigns') ? 'run'
        : location.pathname.startsWith('/data-vault') || location.pathname.startsWith('/outcomes') ? 'learn'
          : location.pathname === '/' ? 'target' : ''
  const searchResults = useMemo(() => {
    const query = searchQuery.trim().toLocaleLowerCase(locale)
    const results: Array<{ id: string; to: string; icon: LucideIcon; title: string; meta: string }> = [
      ...allNavItems.map((item) => ({ id: `page-${item.to}`, to: item.to, icon: item.icon, title: t(item.labelKey), meta: t(item.purposeKey) })),
      ...data.campaigns.map((campaign) => ({ id: `campaign-${campaign.id}`, to: `/campaigns/${campaign.id}`, icon: FlaskConical, title: campaign.title, meta: `${campaign.venue} · ${campaign.status} · ${campaign.phase}` })),
      ...data.conferences.map((conference) => ({ id: `venue-${conference.id}`, to: `/context?venue=${encodeURIComponent(conference.id)}`, icon: Radar, title: `${conference.acronym} · ${conference.name}`, meta: `${conference.deadline} · ${conference.area} · ${conference.kind}` })),
      ...data.conferences.flatMap((conference) => conference.ideas.map((idea) => ({ id: `idea-${idea.id}`, to: `/ideas?idea=${encodeURIComponent(idea.id)}`, icon: GitBranch, title: idea.title, meta: `${conference.acronym} · ${idea.field}` }))),
    ]
    if (!query) return results.slice(0, 7)
    return results.filter((result) => `${result.title} ${result.meta}`.toLocaleLowerCase(locale).includes(query)).slice(0, 10)
  }, [data, locale, searchQuery, t])
  useEffect(() => setMobileOpen(false), [location.pathname])
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault(); setSearchOpen((v) => !v)
      }
      if (event.key === 'Escape') { setSearchOpen(false); setSearchQuery('') }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  const closeSearch = () => { setSearchOpen(false); setSearchQuery('') }
  const argusReady = mode === 'live' && data.connections.some((connection) => connection.state === 'connected' && connection.backendReady === true)
  const stages = [
    { key: 'context', label: t('spine.context'), to: '/context' },
    { key: 'target', label: t('spine.target'), to: '/' },
    { key: 'ideas', label: t('spine.ideas'), to: '/ideas' },
    { key: 'run', label: t('spine.run'), to: '/campaigns' },
    { key: 'learn', label: t('spine.learn'), to: '/data-vault' },
  ]
  return <div className={`app-shell ${railCollapsed ? 'rail-collapsed' : ''}`}>
    <aside className={`sidebar ${mobileOpen ? 'open' : ''}`}>
      <div className="brand"><div className="brand-mark"><ArgusMark /></div><div className="brand-lockup"><span className="brand-family">ARGUS</span><strong className="brand-product">FLYWHEEL</strong><small>{t('brand.descriptor')}</small></div><button className="rail-toggle" onClick={() => setRailCollapsed((value) => !value)} aria-label={railCollapsed ? t('shell.expandNav') : t('shell.collapseNav')}>{railCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}</button><button className="mobile-close" onClick={() => setMobileOpen(false)} aria-label={t('shell.closeNavigation')}><X size={18} /></button></div>
      <button className="command-trigger" onClick={() => setSearchOpen(true)}><Search size={15} /><span>{t('shell.search')}</span><kbd>Ctrl K</kbd></button>
      <nav aria-label={t('shell.primaryNav')}>{navGroups.map((group) => <div className="nav-group" key={group.labelKey}><span className="nav-group-label">{t(group.labelKey)}</span>{group.items.map(({ to, icon: Icon, labelKey, end }) => <NavLink key={to} to={to} end={end} title={railCollapsed ? t(labelKey) : undefined}><Icon size={17} /><span>{t(labelKey)}</span>{to === '/approvals' && data.approvals.length > 0 && <em>{data.approvals.length}</em>}</NavLink>)}</div>)}</nav>
      <div className="sidebar-spacer" />
      <div className="system-mini">
        <div><span className="pulse-dot" /><strong>{data.campaigns.filter((item) => item.status === 'running').length} {t('shell.activeCampaigns')}</strong></div>
        <p>{data.resources.gpus.filter((gpu) => gpu.enabled).length} {t('shell.gpuConfigured')}</p>
        <div className="capacity"><i style={{ width: `${Math.min(100, data.resources.gpus.filter((gpu) => gpu.enabled).length * 25)}%` }} /></div>
      </div>
      <NavLink className="sidebar-foot" to="/resources"><div className="avatar">OP</div><div><strong>{t('shell.operator')}</strong><span>{t('shell.workspace')}</span></div><Settings2 size={16} /></NavLink>
    </aside>
    {mobileOpen && <button className="sidebar-scrim" onClick={() => setMobileOpen(false)} aria-label={t('shell.closeNavigation')} />}
    <main>
      <div className="topbar"><button className="mobile-menu" onClick={() => setMobileOpen(true)} aria-label={t('shell.openNav')}><Menu size={18} /></button><div className="breadcrumbs"><span>ARGUS</span><i>/</i><div><strong>{t(activeNav.labelKey)}</strong></div></div><div className="topbar-actions"><StatusPill tone={argusReady ? 'good' : 'warn'}>{mode === 'demo' ? t('shell.demoData') : argusReady ? 'ARGUS READY' : 'ARGUS NOT READY'}</StatusPill><label className="preference-control"><Languages size={15} /><span className="sr-only">{t('shell.language')}</span><select aria-label={t('shell.language')} value={locale} onChange={(event) => setLocale(event.target.value as 'zh-CN' | 'en-US')}><option value="zh-CN">中文</option><option value="en-US">EN</option></select></label><label className="preference-control"><MoonStar size={15} /><span className="sr-only">{t('shell.theme')}</span><select aria-label={t('shell.theme')} value={theme} onChange={(event) => setTheme(event.target.value as 'dark' | 'light' | 'system')}><option value="dark">{t('shell.dark')}</option><option value="light">{t('shell.light')}</option><option value="system">{t('shell.system')}</option></select></label><button className="icon-button" aria-label={t('shell.notifications')} onClick={() => navigate('/approvals')}><Bell size={16} />{data.approvals.length > 0 && <i />}</button><button className="icon-button" aria-label={t('shell.command')} onClick={() => setSearchOpen(true)}><Command size={16} /></button></div></div>
      {mode === 'demo' && <div className="demo-banner"><AlertCircle size={15} /><strong>{t('shell.demoTitle')}</strong><span>{t('shell.demoDetail')}</span></div>}
      <div className={`page ${mode === 'demo' ? 'with-demo-banner' : ''}`}><div className="research-spine" aria-label="Research lifecycle">{stages.map((stage, index) => <NavLink key={stage.key} to={stage.to} aria-current={researchStep === stage.key ? 'step' : undefined} className={`spine-step ${researchStep === stage.key ? 'active' : ''}`}><i>{String(index + 1).padStart(2, '0')}</i><span>{stage.key}</span><strong>{stage.label}</strong></NavLink>)}</div>{children}</div>
    </main>
    {searchOpen && <div className="modal-layer" role="dialog" aria-modal="true" aria-label={t('shell.search')} onMouseDown={(event) => event.currentTarget === event.target && closeSearch()}><div className="command-palette"><div className="command-input"><Search size={18} /><input autoFocus role="combobox" aria-expanded="true" aria-controls="global-search-results" value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder={t('shell.searchPlaceholder')} /><kbd>Esc</kbd></div><div className="command-results" id="global-search-results"><span>{searchQuery ? t('shell.searchResults') : t('shell.quickGo')}</span>{searchResults.length ? searchResults.map((result) => { const Icon = result.icon; return <NavLink key={result.id} to={result.to} onClick={closeSearch}><Icon size={16} /><div><strong>{result.title}</strong><small>{result.meta}</small></div><kbd>↵</kbd></NavLink> }) : <div className="command-empty"><Search size={20} /><p>{t('common.noResults')}</p></div>}</div></div></div>}
  </div>
}

export default function App() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [mode, setMode] = useState<DataMode>('live')
  const [error, setError] = useState<string | null>(null)
  const [toastText, setToastText] = useState<string | null>(null)
  const deliveredReminders = useRef(new Set<string>())
  const refresh = useCallback(async () => {
    const result = await loadDashboard()
    setData(result.data)
    setMode(result.mode)
  }, [])
  const reload = useCallback(() => {
    setError(null)
    setData(null)
    refresh().catch((e: Error) => setError(e.message))
  }, [refresh])
  useEffect(reload, [reload])
  useEffect(() => {
    if (mode !== 'live' || !data) return
    let disposed = false
    let refreshTimer = 0
    const refreshFromServer = () => loadDashboard().then((result) => { if (!disposed && result.mode === 'live') setData(result.data) }).catch(() => undefined)
    const socket = new WebSocket(apiWebSocketUrl('/ws'))
    socket.onmessage = (message) => {
      try {
        const payload = JSON.parse(message.data) as { type?: string; event?: Record<string, unknown> }
        const event = payload.event
        if (payload.type === 'event' && event?.event_type === 'reminder.fired' && 'Notification' in window && Notification.permission === 'granted') {
          const eventId = String(event.id ?? '')
          if (eventId && !deliveredReminders.current.has(eventId)) {
            deliveredReminders.current.add(eventId)
            let eventPayload: Record<string, unknown> = event.payload && typeof event.payload === 'object' ? event.payload as Record<string, unknown> : {}
            if (!Object.keys(eventPayload).length && typeof event.payload_json === 'string') { try { eventPayload = JSON.parse(event.payload_json) as Record<string, unknown> } catch { eventPayload = {} } }
            const title = typeof eventPayload.title === 'string' ? eventPayload.title : 'Argus Flywheel reminder'
            const body = typeof eventPayload.message === 'string' ? eventPayload.message : '页面打开期间收到一条会议或研究提醒。'
            new Notification(title, { body, tag: `argus-flywheel-reminder-${eventId}` })
          }
        }
        if (payload.type === 'event') { window.clearTimeout(refreshTimer); refreshTimer = window.setTimeout(refreshFromServer, 350) }
      } catch { /* retain the last valid dashboard when a realtime payload is malformed */ }
    }
    const polling = window.setInterval(refreshFromServer, 30_000)
    return () => { disposed = true; window.clearTimeout(refreshTimer); window.clearInterval(polling); socket.close() }
  }, [mode])
  const toast = (message: string) => { setToastText(message); window.setTimeout(() => setToastText(null), 3600) }
  const act = async (path: string, payload: Record<string, unknown> = {}) => { try { const result = await performAction(path, payload, mode); toast(result.message ?? (result.ok ? '操作已提交。' : result.simulated ? 'Demo 只读预览，服务器未发生变化。' : '操作未完成。')); return result.ok } catch (error) { toast(`操作失败：${error instanceof Error ? error.message : 'unknown error'}`); return false } }
  const value = useMemo(() => data ? { data, mode, toast, act, refresh } : null, [data, mode, refresh])
  if (error) return <div className="standalone-state"><ErrorState detail={error} retry={reload} /></div>
  if (!value) return <div className="standalone-state"><div className="loading-brand"><div className="brand-mark"><ArgusMark /></div><strong>ARGUS / FLYWHEEL</strong></div><LoadingState /></div>
  return <AppContext.Provider value={value}><Shell mode={mode}><Routes>
    <Route path="/" element={<HorizonPage />} />
    <Route path="/campaigns" element={<CampaignsPage />} />
    <Route path="/campaigns/:campaignId" element={<CampaignDetailPage />} />
    <Route path="/ideas" element={<IdeaRadarPage />} />
    <Route path="/context" element={<ContextStudioPage />} />
    <Route path="/viewer" element={<Navigate to="/campaigns" replace />} />
    <Route path="/data-vault" element={<DataVaultPage />} />
    <Route path="/approvals" element={<ApprovalPage />} />
    <Route path="/outcomes" element={<OutcomesPage />} />
    <Route path="/connections" element={<ConnectionsPage />} />
    <Route path="/resources" element={<ResourcesPage />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes></Shell>{toastText && <div className="toast" role="status" aria-live="polite"><CheckSquare2 size={16} />{toastText}</div>}</AppContext.Provider>
}
