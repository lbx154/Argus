import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, LoaderCircle, XCircle } from 'lucide-react'
import { useI18n } from '../lib/preferences'

const pageIdentityKeys: Record<string, string> = {
  'RESEARCH OPERATING PICTURE': 'page.overview.eyebrow',
  'The evidence horizon': 'page.overview.title',
  'CONDITIONED RESEARCH': 'page.context.eyebrow',
  'Context Studio': 'page.context.title',
  'LIVING NOVELTY MAP': 'page.ideas.eyebrow',
  'Idea Radar': 'page.ideas.title',
  'ACTIVE RESEARCH': 'page.campaigns.eyebrow',
  'Campaigns': 'page.campaigns.title',
  'INDEPENDENT REVIEW PROCESS': 'page.review.eyebrow',
  'Argus Viewer': 'page.review.title',
  'HUMAN AUTHORITY': 'page.approvals.eyebrow',
  'Approval inbox': 'page.approvals.title',
  'POST-SUBMISSION EVIDENCE': 'page.outcomes.eyebrow',
  'Outcomes & Rebuttal': 'page.outcomes.title',
  'RESEARCH DATA FLYWHEEL': 'page.dataVault.eyebrow',
  '不可变研究数据仓': 'page.dataVault.title',
  'ARGUS RUNTIMES': 'page.connections.eyebrow',
  'Connections': 'page.connections.title',
  'OPERATOR CONTROL': 'page.settings.eyebrow',
  'Resources & settings': 'page.settings.title',
}

export function PageHeader({ eyebrow, title, actions }: { eyebrow: string; title: string; actions?: ReactNode }) {
  const { t } = useI18n()
  const localize = (value: string) => pageIdentityKeys[value] ? t(pageIdentityKeys[value]) : value
  return <header className="page-header">
    <div><div className="eyebrow">{localize(eyebrow)}</div><h1>{localize(title)}</h1></div>
    {actions && <div className="page-actions">{actions}</div>}
  </header>
}

export function StatusPill({ tone = 'neutral', children }: { tone?: 'good' | 'warn' | 'bad' | 'iris' | 'neutral'; children: ReactNode }) {
  return <span className={`status-pill ${tone}`}><i />{children}</span>
}

export function Button({ kind = 'secondary', icon, children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { kind?: 'primary' | 'secondary' | 'danger' | 'ghost'; icon?: ReactNode }) {
  return <button className={`button ${kind}`} {...props}>{icon}{children}</button>
}

export function EmptyState({ title, detail, action }: { title: string; detail?: string; action?: ReactNode }) {
  return <div className="empty-state"><div className="empty-orbit" /><h3>{title}</h3>{detail && <p>{detail}</p>}{action}</div>
}

export function LoadingState({ label = '正在读取研究活动…' }: { label?: string }) {
  const { t } = useI18n()
  return <div className="loading-state"><LoaderCircle size={18} />{label === '正在读取研究活动…' ? t('common.loading') : label}</div>
}

export function ErrorState({ detail, retry }: { detail: string; retry: () => void }) {
  const { t } = useI18n()
  return <div className="error-state"><XCircle size={20} /><div><strong>{t('common.error')}</strong><p>{detail}</p></div><Button onClick={retry}>{t('common.retry')}</Button></div>
}

export function Notice({ tone, title, children }: { tone: 'info' | 'warn' | 'good'; title: string; children: ReactNode }) {
  const Icon = tone === 'warn' ? AlertTriangle : CheckCircle2
  return <div className={`notice ${tone}`}><Icon size={17} /><div><strong>{title}</strong><p>{children}</p></div></div>
}

export function ScoreRing({ value, max = 10, label, large = false }: { value: number; max?: number; label: string; large?: boolean }) {
  const pct = Math.min(100, Math.max(0, value / max * 100))
  return <div className={`score-ring ${large ? 'large' : ''}`} style={{ '--score': `${pct * 3.6}deg` } as React.CSSProperties}>
    <div><strong>{value.toFixed(max === 10 ? 1 : 0)}</strong><span>{label}</span></div>
  </div>
}
