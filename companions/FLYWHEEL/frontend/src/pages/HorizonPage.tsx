import { useMemo, useState } from 'react'
import { AlertTriangle, ArrowRight, Bell, CalendarDays, Check, ChevronDown, Clock3, FlaskConical, Play, Radio, Sparkles, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useApp } from '../App'
import type { Conference } from '../types'
import { Button, Notice, PageHeader, StatusPill } from '../components/ui'
import { useI18n } from '../lib/preferences'

const START = new Date('2026-08-22T00:00:00+08:00').getTime()
const END = new Date('2027-08-22T00:00:00+08:00').getTime()
const NOW = Date.now()
const score = (value: number) => value >= 0 ? String(value) : '—'
const position = (date: string) => date ? Math.max(1, Math.min(99, (new Date(`${date}T00:00:00+08:00`).getTime() - START) / (END - START) * 100)) : 98
const OFFSET_DATETIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/
const basePreflight = [
  ['compute_inventory_and_capacity_verified', '已真实探测或核验所选算力、容量与可用时段；seed 中的算力文字不是当前库存。'],
  ['data_access_and_license_reviewed', '已核验公开数据/任务的访问、许可证、版本与隐私边界。'],
  ['non_compute_prerequisites_reviewed', '已核验设备/testbed、参与者、专家协作与其他非算力前置条件；缺失时将阻断或延期。'],
] as const
const domainPreflight: Record<string, ReadonlyArray<readonly [string, string]>> = {
  HI: [['human_subjects_and_ethics_path_reviewed', '已确定不涉及真人研究，或已记录参与者招募、知情同意与 IRB/伦理路径。']],
  SC: [['dual_use_and_disclosure_path_reviewed', '已审查双重用途、隔离测试环境与负责任披露路径。']],
  CT: [['proof_expertise_and_checker_plan_reviewed', '已核验理论证明所需专家能力、证明检查器或独立 proof review 路径。']],
}

function StartCampaignModal({ conference, onClose }: { conference: Conference; onClose: () => void }) {
  const { data, act, mode } = useApp()
  const navigate = useNavigate()
  const [ideaId, setIdeaId] = useState(conference.ideas[0]?.id)
  const launchConnections = data.connections.filter((item) => item.state === 'connected' && (mode === 'demo' || item.backendReady === true))
  const [connectionId, setConnectionId] = useState(launchConnections[0]?.id ?? '')
  const resourcePools = data.resources.pools.filter((pool) => pool.enabled && pool.type !== 'unconfigured')
  const [resourceId, setResourceId] = useState(resourcePools[0]?.id ?? '')
  const [gpuBudget, setGpuBudget] = useState(160)
  const [apiBudget, setApiBudget] = useState(30)
  const [wallClockDeadline, setWallClockDeadline] = useState(conference.deadline ? `${conference.deadline}T18:00:00+08:00` : '')
  const [checked, setChecked] = useState(false)
  const [preflight, setPreflight] = useState<Record<string, boolean>>({})
  const idea = conference.ideas.find((item) => item.id === ideaId)
  const preflightItems = [...basePreflight, ...(domainPreflight[conference.area] ?? [])]
  const preflightReady = preflightItems.every(([key]) => preflight[key] === true)
  const wallClockValid = OFFSET_DATETIME.test(wallClockDeadline) && !Number.isNaN(new Date(wallClockDeadline).getTime()) && (!conference.deadline || wallClockDeadline.slice(0, 10) <= conference.deadline)
  const start = async () => {
    const started = await act('campaigns', { venue_key: conference.venueKey ?? conference.id, idea_id: ideaId && /^\d+$/.test(ideaId) ? Number(ideaId) : ideaId, deadline_id: conference.deadlineId, connection_id: connectionId || undefined, resource_id: resourceId || undefined, config: { mode: 'bounded', gpu_hours: gpuBudget, api_budget: `USD hard cap: ${apiBudget}`, max_parallel_jobs: 1, wall_clock_deadline: wallClockDeadline, preflight_attestations: Object.fromEntries(preflightItems.map(([key]) => [key, preflight[key] === true])) } })
    if (started) { onClose(); navigate('/campaigns') }
  }
  return <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="start-title" onMouseDown={(e) => e.currentTarget === e.target && onClose()}>
    <div className="start-modal">
      <div className="modal-head"><div><span className="eyebrow">START A BOUNDED PORTFOLIO SCREEN</span><h2 id="start-title">为 {conference.acronym} 启动 Argus 筛选</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭"><X size={17} /></button></div>
      <Notice tone={conference.kind === 'official' ? 'good' : 'warn'} title={conference.kind === 'official' ? '官方截止时间' : '预测截止区间'}>{conference.kind === 'official' ? `${conference.deadline} · ${conference.track}` : `${conference.deadline} — ${conference.deadlineEnd}。正式 CFP 发布后，调度器会重新计算里程碑。`}</Notice>
      <Notice tone="info" title="Seed baseline · not a personalized final idea">这 5 条是会议/领域起始基线。真正的 ideation 必须结合团队能力、资源、时间、数据权限、风险与目标重新调研和排序；不会把固定 catalog 冒充最终方案。</Notice>
      <label className="field-label">选择 seed baseline hypothesis</label>
      <div className="idea-choices">{conference.ideas.map((item) => <button key={item.id} className={item.id === ideaId ? 'selected' : ''} onClick={() => setIdeaId(item.id)}><span><i />{item.title}</span><small>{item.field} · Novelty {score(item.novelty)}</small></button>)}</div>
      {idea && <div className="campaign-contract"><div><span>研究问题</span><p>{idea.thesis}</p></div><div className="contract-grid"><div><span>选题阶段算力假设（非当前库存）</span><strong>{idea.compute || "—"}</strong></div><div><span>主要风险</span><strong>{idea.risk || "—"}</strong></div><div><span>运行模式</span><strong>Bounded · evidence gates</strong></div><div><span>Argus 版本</span><strong>连接上的 stable SHA</strong></div></div></div>}
      <div className="launch-config"><label><span>Argus connection</span><select value={connectionId} onChange={(e) => setConnectionId(e.target.value)}><option value="">Select a ready runtime</option>{launchConnections.map((item) => <option value={item.id} key={item.id}>{item.name} · {item.version}</option>)}</select></label><label><span>Resource pool</span><select value={resourceId} onChange={(e) => setResourceId(e.target.value)}><option value="">Select a configured pool</option>{resourcePools.map((item) => <option value={item.id} key={item.id}>{item.label} · {item.type}</option>)}</select></label><label><span>GPU budget (hours)</span><input type="number" min="1" max="5000" value={gpuBudget} onChange={(e) => setGpuBudget(Number(e.target.value))} /></label><label><span>API budget (USD)</span><input type="number" min="0" max="10000" value={apiBudget} onChange={(e) => setApiBudget(Number(e.target.value))} /></label><div className="execution-backend-readout"><span>Execution backend</span><strong>Target Argus connection default</strong><small>当前 WebAPI 不支持在创建 daemon 时切换 backend。Pi、Copilot、Codex 或 Claude 必须在目标 Argus 实例中配置；Flywheel 只记录运行 snapshot 实际报告的 backend。</small></div><label className="wall-clock-field"><span>Wall-clock deadline · explicit UTC offset</span><input type="text" required spellCheck={false} aria-invalid={!wallClockValid} value={wallClockDeadline} onChange={(e) => setWallClockDeadline(e.target.value)} placeholder="2027-01-15T18:00:00+08:00" /><small>{conference.rolling ? 'Rolling venue 需要你填写内部保守截止时间。必须带 Z 或 ±HH:MM；不接受无时区 datetime。' : `${conference.kind === 'forecast' ? `预测会议按区间最早端 ${conference.deadline} 约束` : `不得晚于已登记日期 ${conference.deadline}`}。必须带 Z 或 ±HH:MM；不接受无时区 datetime。`}</small></label></div>
      <div className="preflight-list"><span>EXECUTION PREFLIGHT · HUMAN ATTESTATIONS</span>{preflightItems.map(([key, label]) => <label key={key}><input type="checkbox" checked={preflight[key] === true} onChange={(event) => setPreflight((current) => ({ ...current, [key]: event.target.checked }))} /><span><Check size={11} /></span><p>{label}</p></label>)}</div>
      <label className="confirm-check"><input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} /><span><Check size={12} /></span><p>我批准启动有界 Portfolio 筛选，并理解它只负责候选证伪与选优；胜出后仍须人工冻结确认性合同。负结果和 <code>NO_WINNER</code> 都是合法终态，Viewer 分数不代表录用保证。</p></label>
      <div className="modal-actions"><Button onClick={onClose}>取消</Button><Button kind="primary" disabled={!checked || !preflightReady || !wallClockValid || !ideaId || (mode === 'live' && (!launchConnections.some((item) => item.id === connectionId) || !resourceId))} onClick={start} icon={<Play size={15} />}>{mode === 'live' ? '创建并启动 Portfolio 筛选' : '在 Demo 中模拟筛选'}</Button></div>
    </div>
  </div>
}

export function HorizonPage() {
  const { data } = useApp()
  const { t } = useI18n()
  const navigate = useNavigate()
  const [view, setView] = useState<'horizon' | 'calendar'>('horizon')
  const [selectedId, setSelectedId] = useState(data.conferences[1]?.id ?? data.conferences[0]?.id)
  const selected = data.conferences.find((conference) => conference.id === selectedId) ?? data.conferences[0]
  const nextConference = useMemo(() => [...data.conferences].filter((item) => !item.rolling && new Date(`${item.deadline}T23:59:59+08:00`).getTime() >= NOW).sort((a, b) => a.reminderDays - b.reminderDays)[0], [data.conferences])
  const months = ['AUG', 'SEP', 'OCT', 'NOV', 'DEC', 'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG']
  const ordered = useMemo(() => [...data.conferences].sort((a, b) => (a.rolling ? 1 : b.rolling ? -1 : a.deadline.localeCompare(b.deadline))), [data.conferences])
  const activeCampaigns = data.campaigns.filter((campaign) => campaign.status === 'running' || campaign.status === 'attention')
  return <>
    <PageHeader eyebrow="RESEARCH OPERATING PICTURE" title="The evidence horizon" actions={<><div className="segmented" role="group" aria-label="切换视图"><button className={view === 'horizon' ? 'active' : ''} onClick={() => setView('horizon')}><Radio size={14} />{t('view.horizon')}</button><button className={view === 'calendar' ? 'active' : ''} onClick={() => setView('calendar')}><CalendarDays size={14} />{t('view.calendar')}</button></div><Button icon={<Bell size={15} />} onClick={() => navigate('/resources')}>{t('action.reminderRules')}</Button></>} />
    {nextConference ? <div className="priority-strip"><div><span className="priority-signal"><i /><i /><i /></span><div><strong>{nextConference.acronym}</strong><p>D−{nextConference.reminderDays} · {nextConference.ideas.length ? `${nextConference.ideas.length} 个候选` : '未同步'}</p></div></div><Button kind="ghost" onClick={() => setSelectedId(nextConference.id)}>查看</Button></div> : <EmptyHorizon />}
    {view === 'horizon' ? <section className="horizon-instrument" aria-label="会议与证据时间线">
      <div className="horizon-top"><div><strong>22 AUG 2026 — 22 AUG 2027</strong></div><div className="horizon-legend"><span><i className="official-dot" />Official</span><span><i className="forecast-band" />Forecast range</span><span><i className="today-line-sample" />Today</span></div></div>
      <div className="horizon-scroll"><div className="horizon-canvas">
        <div className="month-ruler"><div className="venue-gutter" />{months.map((month, i) => <span key={`${month}-${i}`}>{month}</span>)}</div>
        <div className="today-marker" style={{ left: `calc(190px + (100% - 190px) * ${(NOW - START) / (END - START)})` }}><span>TODAY</span></div>
        {ordered.map((conference, index) => {
          const left = position(conference.deadline)
          const width = conference.deadlineEnd ? Math.max(1.8, position(conference.deadlineEnd) - left) : 0
          const active = conference.id === selectedId
          return <button key={conference.id} className={`horizon-row ${active ? 'selected' : ''}`} onClick={() => setSelectedId(conference.id)} aria-pressed={active}>
            <div className="venue-label"><strong>{conference.acronym}</strong><span>{conference.rolling ? 'Rolling venue' : conference.track}</span></div>
            <div className="venue-track"><span className="evidence-trail" style={{ width: `${left}%`, '--trail-color': conference.color } as React.CSSProperties}><i /><i /><i /></span>{conference.kind === 'forecast' && <span className="deadline-range" style={{ left: `${left}%`, width: `${width}%`, '--venue-color': conference.color } as React.CSSProperties} />}<span className={`deadline-pin ${conference.kind}`} style={{ left: `${left}%`, '--venue-color': conference.color } as React.CSSProperties}><i /><em>{conference.rolling ? 'ROLLING' : conference.kind === 'official' ? 'OFFICIAL' : 'FORECAST'}</em></span></div>
          </button>
        })}
      </div></div>
    </section> : <CalendarView conferences={ordered} onSelect={(conference) => { setSelectedId(conference.id); setView('horizon') }} />}
    {selected && <section className="conference-focus">
      <div className="conference-intro"><div className="conference-wordmark"><span>{selected.acronym.slice(0, 2)}</span></div><div><div className="conference-meta"><StatusPill tone={selected.kind === 'official' ? 'good' : 'warn'}>{selected.rolling ? 'Rolling venue' : selected.kind === 'official' ? 'Official date' : 'Forecast window'}</StatusPill><span>{selected.area} · {selected.track}</span></div><h2>{selected.acronym} <em>/{selected.rolling ? 'rolling' : new Date(selected.deadline).getFullYear()}</em></h2><p>{selected.name}</p></div></div>
      <div className="deadline-readout"><span>FULL PAPER</span><strong>{selected.rolling ? 'ROLLING' : selected.deadline.replaceAll('-', '.')}</strong>{selected.deadlineEnd && <small>— {selected.deadlineEnd.replaceAll('-', '.')}</small>}<p><Clock3 size={14} /> {selected.rolling ? 'No dated reminder registered' : `Reminder in ${selected.reminderDays} days`}</p></div>
      <div className="focus-ideas"><div className="section-line"><span>SEED BASELINE IDEAS</span><small>{selected.ideas.length} candidates</small></div>{selected.ideas.slice(0, 3).map((idea, index) => <article key={idea.id} className="idea-preview"><span className="idea-rank">0{index + 1}</span><div><h3>{idea.title}</h3><p>{idea.thesis}</p><div className="idea-foot"><span>{idea.field}</span><span>Novelty <strong>{score(idea.novelty)}</strong></span><span>Feasibility <strong>{score(idea.feasibility)}</strong></span></div></div></article>)}</div>
      <div className="focus-action"><Button kind="primary" icon={<Sparkles size={15} />} onClick={() => navigate(`/context?venue=${encodeURIComponent(selected.id)}&deadline=${encodeURIComponent(String(selected.deadlineId ?? ''))}`)}>{t('action.teamPlan')}</Button></div>
    </section>}
    {activeCampaigns.length > 0 && <section className="running-rail"><div className="section-line"><span>RUNNING & ATTENTION</span><small>{data.campaigns.filter((c) => c.status === 'running').length} active · {data.campaigns.filter((c) => c.status === 'attention').length} needs attention</small></div>{activeCampaigns.map((campaign) => <div className="running-row" key={campaign.id}><span className={`campaign-state ${campaign.status}`} /><div><strong>{campaign.title}</strong><small>{campaign.venue} · {campaign.phase}</small></div><div className="progress-line"><i style={{ width: `${campaign.progress}%` }} /></div><span>{campaign.tasksDone}/{campaign.tasksTotal} tasks</span><span>{campaign.gpuHours.toFixed(0)} GPU·h</span><Button kind="ghost" onClick={() => navigate(`/campaigns/${campaign.id}`)}>Open <ArrowRight size={13} /></Button></div>)}</section>}
  </>
}

function EmptyHorizon() { return <div className="priority-strip"><div><span className="priority-signal"><i /><i /><i /></span><div><strong>尚无即将到来的会议</strong></div></div></div> }

function CalendarView({ conferences, onSelect }: { conferences: Conference[]; onSelect: (conference: Conference) => void }) {
  const groups = useMemo(() => Object.entries(conferences.reduce<Record<string, Conference[]>>((acc, conference) => { const month = conference.rolling ? 'rolling' : conference.deadline.slice(0, 7); (acc[month] ??= []).push(conference); return acc }, {})), [conferences])
  return <section className="calendar-list"><div className="calendar-head"><div><span>DATE</span><span>VENUE / TRACK</span><span>STATUS</span><span>REMINDER</span></div></div>{groups.map(([month, items]) => <div className="calendar-month" key={month}><div className="month-stamp"><strong>{month === 'rolling' ? 'ROLL' : new Date(`${month}-02`).toLocaleString('en', { month: 'short' }).toUpperCase()}</strong><span>{month === 'rolling' ? 'ING' : month.slice(0, 4)}</span></div><div>{items.map((conference) => <button key={conference.id} onClick={() => onSelect(conference)}><time>{conference.rolling ? '∞' : conference.deadline.slice(8, 10)}</time><div><strong>{conference.acronym}</strong><span>{conference.track} · {conference.area}</span></div><StatusPill tone={conference.kind === 'official' ? 'good' : 'warn'}>{conference.rolling ? 'rolling' : conference.kind}</StatusPill><span><Bell size={13} /> {conference.rolling ? 'manual' : `D−${conference.reminderDays}`}</span><ChevronDown size={14} /></button>)}</div></div>)}</section>
}
