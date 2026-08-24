import { useCallback, useEffect, useMemo, useState } from 'react'
import { Archive, CheckCircle2, Database, Fingerprint, GitCommitHorizontal, LockKeyhole, RefreshCw, ShieldCheck, Upload, X } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { confirmReviewImport, createDatasetSnapshot, createOpenReviewImport, createReviewImport, discardReviewImport, loadDatasetSnapshots, loadEpisode, loadEpisodes, previewDatasetSnapshot, sealEpisode, verifyEpisode } from '../api/client'
import { Button, EmptyState, ErrorState, LoadingState, Notice, PageHeader, StatusPill } from '../components/ui'
import { ArgusArtifactsPanel } from '../components/ArgusArtifactsPanel'
import type { DatasetSnapshot, EpisodeVerification, ResearchEpisode } from '../types'
import { useApp } from '../App'

const shortHash = (value?: string | null) => value ? `${value.slice(0, 10)}…${value.slice(-6)}` : 'not sealed'
const when = (value?: string) => value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—'

function integrityTone(value: string): 'good' | 'warn' | 'bad' | 'neutral' {
  if (value === 'pass' || value === 'verified') return 'good'
  if (value === 'fail' || value === 'quarantined') return 'bad'
  if (value === 'warn' || value === 'checking') return 'warn'
  return 'neutral'
}

export function DataVaultPage() {
  const { toast } = useApp()
  const [searchParams] = useSearchParams()
  const requestedEpisode = searchParams.get('episode') ?? ''
  const [episodes, setEpisodes] = useState<ResearchEpisode[]>([])
  const [snapshots, setSnapshots] = useState<DatasetSnapshot[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [selected, setSelected] = useState<ResearchEpisode | null>(null)
  const [verification, setVerification] = useState<EpisodeVerification | null>(null)
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null)
  const [datasetName, setDatasetName] = useState('ARGUS / FLYWHEEL research episodes')
  const [datasetLicense, setDatasetLicense] = useState('')
  const [loading, setLoading] = useState(true)
  const [detailEpoch, setDetailEpoch] = useState(0)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [sealOpen, setSealOpen] = useState(false)
  const [sealReason, setSealReason] = useState('Human checkpoint: preserve the current research state and provenance.')
  const [terminalState, setTerminalState] = useState('')
  const [reviewOpen, setReviewOpen] = useState(false)
  const [reviewKind, setReviewKind] = useState<'paste' | 'json' | 'pdf' | 'openreview'>('paste')
  const [reviewRaw, setReviewRaw] = useState('')
  const [reviewRef, setReviewRef] = useState('')
  const [reviewFile, setReviewFile] = useState<File | null>(null)
  const [reviewDraft, setReviewDraft] = useState<Record<string, unknown> | null>(null)
  const [reviewLicense, setReviewLicense] = useState('')
  const [reviewRedacted, setReviewRedacted] = useState(false)
  const [reviewTraining, setReviewTraining] = useState(false)

  const refresh = useCallback(async () => {
    setError('')
    try {
      const [nextEpisodes, nextSnapshots] = await Promise.all([
        loadEpisodes(),
        loadDatasetSnapshots().catch(() => []),
      ])
      setEpisodes(nextEpisodes)
      setSnapshots(nextSnapshots)
      setDetailEpoch((value) => value + 1)
      setSelectedId((current) => requestedEpisode && nextEpisodes.some((item) => item.id === requestedEpisode)
        ? requestedEpisode
        : current && nextEpisodes.some((item) => item.id === current) ? current : nextEpisodes[0]?.id ?? '')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Data Vault could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [requestedEpisode])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    if (!selectedId) { setSelected(null); return }
    let active = true
    setVerification(null)
    loadEpisode(selectedId).then((detail) => {
      if (!active) return
      setSelected(detail)
      setEpisodes((current) => current.map((item) => item.id === detail.id ? detail : item))
    }).catch((cause) => {
      if (active) setError(cause instanceof Error ? cause.message : 'Episode could not be loaded.')
    })
    return () => { active = false }
  }, [selectedId, detailEpoch])

  const totals = useMemo(() => ({
    revisions: episodes.reduce((sum, item) => sum + item.head_revision, 0),
    eligible: episodes.filter((item) => item.data_eligible === true).length,
    verified: episodes.filter((item) => item.data_eligible !== null).length,
    pendingVerification: episodes.filter((item) => item.data_eligible === null).length,
  }), [episodes])

  const verify = async () => {
    if (!selected) return
    setBusy('verify')
    try {
      const result = await verifyEpisode(selected.id)
      setVerification(result)
      toast(result.valid ? 'Episode 哈希链与对象引用验证通过。' : '验证发现问题；本 Episode 不会进入数据集快照。')
    } catch (cause) {
      toast(`验证失败：${cause instanceof Error ? cause.message : 'unknown error'}`)
    } finally { setBusy('') }
  }

  const seal = async () => {
    if (!selected || !sealReason.trim()) return
    setBusy('seal')
    try {
      await sealEpisode(selected.id, sealReason.trim(), terminalState)
      setSealOpen(false)
      await refresh()
      setSelected(await loadEpisode(selected.id))
      toast('已创建新的不可变 Episode revision；旧版本未被覆盖。')
    } catch (cause) {
      toast(`封存失败：${cause instanceof Error ? cause.message : 'unknown error'}`)
    } finally { setBusy('') }
  }

  const prepareSnapshot = async () => {
    setBusy('preview')
    try { setPreview(await previewDatasetSnapshot()) }
    catch (cause) { toast(`预检失败：${cause instanceof Error ? cause.message : 'unknown error'}`) }
    finally { setBusy('') }
  }

  const createSnapshot = async () => {
    setBusy('dataset')
    try {
      if (!preview) return
      const snapshot = await createDatasetSnapshot(preview, datasetName.trim(), datasetLicense.trim())
      setPreview(null)
      await refresh()
      toast(`数据集快照 ${snapshot.id.slice(0, 8)} 已封存；不会自动训练或上传。`)
    } catch (cause) { toast(`封存失败：${cause instanceof Error ? cause.message : 'unknown error'}`) }
    finally { setBusy('') }
  }

  const fileBase64 = (file: File) => new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('PDF 读取失败'))
    reader.onload = () => {
      const result = String(reader.result ?? '')
      resolve(result.includes(',') ? result.slice(result.indexOf(',') + 1) : result)
    }
    reader.readAsDataURL(file)
  })

  const stageReview = async () => {
    if (!selected) return
    setBusy('review-stage')
    try {
      let draft: Record<string, unknown>
      if (reviewKind === 'openreview') {
        if (!reviewRef.trim()) throw new Error('请输入 OpenReview forum / note id')
        draft = await createOpenReviewImport(selected.id, reviewRef.trim())
      } else if (reviewKind === 'pdf') {
        if (!reviewFile) throw new Error('请选择 PDF 文件')
        if (reviewFile.size > 10 * 1024 * 1024) throw new Error('PDF 不能超过 10 MiB')
        draft = await createReviewImport(selected.id, {
          source_kind: 'pdf',
          source_ref: reviewRef.trim() || reviewFile.name,
          payload: { filename: reviewFile.name, mime_type: reviewFile.type || 'application/pdf', content_base64: await fileBase64(reviewFile) },
        })
      } else if (reviewKind === 'json') {
        if (!reviewRaw.trim()) throw new Error('请粘贴 JSON')
        draft = await createReviewImport(selected.id, { source_kind: 'json', source_ref: reviewRef.trim() || undefined, payload: JSON.parse(reviewRaw) })
      } else {
        if (!reviewRaw.trim()) throw new Error('请粘贴已脱敏的评审文本')
        draft = await createReviewImport(selected.id, { source_kind: 'paste', source_ref: reviewRef.trim() || undefined, raw_text: reviewRaw.trim() })
      }
      setReviewDraft(draft)
      toast('评审已进入 staging；尚未写入不可变对象库。')
    } catch (cause) { toast(`导入预检失败：${cause instanceof Error ? cause.message : 'unknown error'}`) }
    finally { setBusy('') }
  }

  const confirmReview = async () => {
    if (!selected || !reviewDraft || !reviewRedacted || !reviewLicense.trim()) return
    setBusy('review-confirm')
    try {
      await confirmReviewImport(String(reviewDraft.id), {
        parsed: Array.isArray(reviewDraft.parsed)
          ? reviewDraft.parsed
          : reviewDraft.parsed && typeof reviewDraft.parsed === 'object'
            ? reviewDraft.parsed as Record<string, unknown>
            : undefined,
        redaction_confirmed: true,
        training_consent: reviewTraining,
        license_basis: reviewLicense.trim(),
      })
      setReviewOpen(false); setReviewDraft(null); setReviewRaw(''); setReviewRef(''); setReviewFile(null); setReviewRedacted(false); setReviewTraining(false); setReviewLicense('')
      setSelected(await loadEpisode(selected.id)); await refresh()
      toast('外部评审已由人工确认并按原始字节 SHA-256 封存；下一次 Episode revision 会引用它。')
    } catch (cause) { toast(`评审确认失败：${cause instanceof Error ? cause.message : 'unknown error'}`) }
    finally { setBusy('') }
  }

  const discardReview = async (batchId: string) => {
    if (!selected) return
    setBusy('review-discard')
    try {
      await discardReviewImport(batchId, 'Human returned to edit; preserve staging audit but exclude this batch from sealing.')
      setReviewDraft(null); setReviewRedacted(false); setReviewTraining(false); setReviewLicense('')
      setSelected(await loadEpisode(selected.id)); await refresh()
      toast('旧 staging batch 已标记为 discarded；可修改后重新预检，不会永久阻塞 Episode。')
    } catch (cause) { toast(`放弃 staging 失败：${cause instanceof Error ? cause.message : 'unknown error'}`) }
    finally { setBusy('') }
  }

  if (loading) return <LoadingState label="正在验证 Research Episode 索引…" />
  if (error && episodes.length === 0) return <ErrorState detail={error} retry={() => { setLoading(true); void refresh() }} />

  return <div className="data-vault-page">
    <PageHeader
      eyebrow="RESEARCH DATA FLYWHEEL"
      title="不可变研究数据仓"
      actions={<>
        <Button icon={<RefreshCw size={14} />} onClick={() => void refresh()} disabled={Boolean(busy)}>刷新</Button>
        <Button kind="primary" icon={<Archive size={15} />} onClick={prepareSnapshot} disabled={Boolean(busy) || episodes.length === 0}>{busy === 'preview' ? '正在预检…' : '预检数据集快照'}</Button>
      </>}
    />

    <div className="vault-metrics">
      <article><Database size={18} /><div><strong>{episodes.length}</strong><span>Research Episodes</span></div></article>
      <article><GitCommitHorizontal size={18} /><div><strong>{totals.revisions}</strong><span>不可变 revisions</span></div></article>
      <article><ShieldCheck size={18} /><div><strong>{totals.eligible}/{totals.verified}</strong><span>资格通过 / 已验证</span></div></article>
      <article className={totals.pendingVerification ? 'attention' : ''}><Fingerprint size={18} /><div><strong>{totals.pendingVerification}</strong><span>资格待验证</span></div></article>
      <article><Archive size={18} /><div><strong>{snapshots.length}</strong><span>Dataset snapshots</span></div></article>
    </div>

    {preview && <section className="snapshot-preview">
      <div><h2>创建数据集快照</h2></div>
      <pre>{JSON.stringify(preview, null, 2)}</pre>
      <div className="snapshot-fields"><label><span>Snapshot name</span><input value={datasetName} onChange={(event) => setDatasetName(event.target.value)} /></label><label><span>汇总许可证依据 · 必填</span><input value={datasetLicense} onChange={(event) => setDatasetLicense(event.target.value)} placeholder="例如：仅包含团队拥有且获准用于研究训练的脱敏记录" /></label></div>
      <div className="snapshot-actions"><Button onClick={() => setPreview(null)}>取消</Button><Button kind="primary" icon={<LockKeyhole size={14} />} onClick={createSnapshot} disabled={busy === 'dataset' || !datasetName.trim() || !datasetLicense.trim() || !preview.selection_sha256}>{busy === 'dataset' ? '正在封存…' : '确认创建不可变快照'}</Button></div>
    </section>}

    {episodes.length === 0 ? <EmptyState
      title="还没有 Research Episode"
      action={<Link className="button primary" to="/context">从团队条件开始</Link>}
    /> : <div className="vault-layout">
      <aside className="episode-index">
        <div className="episode-index-head"><span>EPISODE INDEX</span><small>{episodes.length} records</small></div>
        {episodes.map((episode) => <button key={episode.id} className={selectedId === episode.id ? 'active' : ''} onClick={() => setSelectedId(episode.id)}>
          <div className="episode-state"><i className={`integrity-${episode.integrity_state}`} /><span>{episode.phase}</span></div>
          <strong>{episode.title}</strong>
          <small>{episode.venue_name ?? episode.venue_key ?? 'Venue pending'} · r{episode.head_revision}</small>
          <div><StatusPill tone={integrityTone(episode.integrity_state)}>{episode.integrity_state}</StatusPill>{episode.data_eligible === true ? <StatusPill tone="good">data-ready</StatusPill> : episode.data_eligible === null ? <StatusPill tone="neutral">待验证</StatusPill> : <StatusPill tone="warn">data-blocked</StatusPill>}</div>
        </button>)}
      </aside>

      <section className="episode-detail">
        {!selected ? <LoadingState /> : <>
          <div className="episode-title-row">
            <div><span>RESEARCH EPISODE · {selected.id.slice(0, 8)}</span><h2>{selected.title}</h2><p>{selected.team_name ?? selected.team_profile_id ?? 'Team pending'} · {selected.venue_name ?? selected.venue_key ?? 'Venue pending'}</p></div>
            <div><Button icon={<Upload size={14} />} onClick={() => setReviewOpen(true)} disabled={Boolean(busy)}>导入外部评审</Button><Button icon={<ShieldCheck size={14} />} onClick={verify} disabled={Boolean(busy)}>{busy === 'verify' ? '正在验证…' : '验证哈希链'}</Button><Button kind="primary" icon={<LockKeyhole size={14} />} onClick={() => setSealOpen(true)} disabled={Boolean(busy)}>封存新版本</Button></div>
          </div>

          <div className="episode-state-grid">
            <div><span>Phase</span><strong>{selected.phase}</strong></div>
            <div><span>Execution</span><strong>{selected.execution_state}</strong></div>
            <div><span>Human gate</span><strong>{selected.human_gate_state}</strong></div>
            <div><span>Integrity</span><strong>{selected.integrity_state}</strong></div>
            <div><span>Terminal state</span><strong>{selected.terminal_state ?? 'open'}</strong></div>
          </div>

          {verification && <Notice tone={verification.valid ? 'good' : 'warn'} title={verification.valid ? '完整性验证通过' : 'Episode 仍被阻断'}>
            {verification.checks.map((check) => `${check.passed ? '✓' : '×'} ${check.name}`).join(' · ')}
          </Notice>}

          {selected.review_imports?.some((item) => !['confirmed', 'discarded'].includes(item.state)) && <div className="pending-review-list">
            <Notice tone="warn" title="待处理评审">确认或放弃后才能封存。</Notice>
            {selected.review_imports.filter((item) => !['confirmed', 'discarded'].includes(item.state)).map((item) => <div key={item.id}>
              <div><strong>{item.source_kind}</strong><small>{item.source_ref || item.id.slice(0, 8)}</small></div>
              <Button onClick={() => { setReviewDraft({ ...item, parsed: item.parsed ?? {} }); setReviewOpen(true) }}>继续确认</Button>
              <Button kind="danger" onClick={() => void discardReview(item.id)} disabled={busy === 'review-discard'}>放弃 staging</Button>
            </div>)}
          </div>}

          <ArgusArtifactsPanel
            key={selected.id}
            episodeId={selected.id}
            disabled={Boolean(busy)}
            onChanged={refresh}
            onRequestSeal={() => setSealOpen(true)}
            toast={toast}
          />

          <div className="revision-section">
            <div className="section-heading"><div><h3>Episode revisions</h3></div><StatusPill tone="iris">head r{selected.head_revision}</StatusPill></div>
            {selected.revisions?.length ? <div className="revision-list">{[...selected.revisions].reverse().map((revision) => <article key={revision.id}>
              <div className="revision-marker"><i /><span>r{revision.revision}</span></div>
              <div><strong>{revision.trigger_type}</strong><p><code>{shortHash(revision.manifest_sha256)}</code></p><small>{revision.sealed_by} · {when(revision.sealed_at)}</small></div>
              <CheckCircle2 size={16} />
            </article>)}</div> : <EmptyState title="尚未封存 revision" />}
          </div>

          <div className="episode-links">
            <div className="section-heading"><div><h3>引用对象</h3></div></div>
            {selected.links?.length ? selected.links.map((link) => <div key={`${link.entity_type}-${link.entity_id}-${link.relation}`}><span>{link.entity_type}</span><strong>{link.relation}</strong><code>{link.entity_id}</code></div>) : <p>暂无引用</p>}
          </div>
        </>}
      </section>
    </div>}

    {sealOpen && selected && <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="seal-title">
      <div className="vault-seal-dialog">
        <div className="dialog-head"><div><h2 id="seal-title">封存 Episode r{selected.head_revision + 1}</h2></div><button onClick={() => setSealOpen(false)} aria-label="关闭"><X size={18} /></button></div>
        <Notice tone="info" title="新增版本，不覆盖历史">数据库密钥、未授权全文与隐私标识将被拒绝。</Notice>
        <label><span>封存原因 · 必填</span><textarea rows={4} value={sealReason} onChange={(event) => setSealReason(event.target.value)} /></label>
        <label><span>合法终态 · 可选</span><select value={terminalState} onChange={(event) => setTerminalState(event.target.value)}><option value="">保持进行中</option><option value="NO_WINNER">NO_WINNER</option><option value="NOVELTY_COLLISION">NOVELTY_COLLISION</option><option value="RESOURCE_INFEASIBLE">RESOURCE_INFEASIBLE</option><option value="NEGATIVE_RESULT">NEGATIVE_RESULT</option><option value="INCONCLUSIVE">INCONCLUSIVE</option><option value="KILLED">KILLED</option><option value="DEFERRED">DEFERRED</option><option value="POLICY_BLOCKED">POLICY_BLOCKED</option><option value="SUBMISSION_READY_FOR_HUMAN_REVIEW">SUBMISSION_READY_FOR_HUMAN_REVIEW</option><option value="ACCEPTED">ACCEPTED</option><option value="REJECTED">REJECTED</option><option value="WITHDRAWN">WITHDRAWN</option></select></label>
        <div className="dialog-actions"><Button onClick={() => setSealOpen(false)}>取消</Button><Button kind="primary" icon={<LockKeyhole size={14} />} onClick={seal} disabled={!sealReason.trim() || busy === 'seal'}>{busy === 'seal' ? '正在封存…' : '创建不可变 revision'}</Button></div>
      </div>
    </div>}
    {reviewOpen && selected && <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="review-import-title">
      <div className="vault-seal-dialog review-import-dialog">
        <div className="dialog-head"><div><h2 id="review-import-title">导入外部评审</h2></div><button onClick={() => setReviewOpen(false)} aria-label="关闭"><X size={18} /></button></div>
        {!reviewDraft ? <>
          <div className="review-kind-tabs">{(['paste', 'json', 'pdf', 'openreview'] as const).map((kind) => <button key={kind} className={reviewKind === kind ? 'active' : ''} onClick={() => setReviewKind(kind)}>{kind === 'paste' ? '粘贴文本' : kind === 'json' ? 'JSON' : kind === 'pdf' ? 'PDF 文件' : 'OpenReview API'}</button>)}</div>
          {reviewKind === 'pdf' ? <label><span>PDF file · max 10 MiB</span><input type="file" accept="application/pdf,.pdf" onChange={(event) => setReviewFile(event.target.files?.[0] ?? null)} /></label> : reviewKind === 'openreview' ? <label><span>OpenReview forum / note id</span><input value={reviewRef} onChange={(event) => setReviewRef(event.target.value)} placeholder="例如 zzz...（不是任意 URL）" /></label> : <label><span>{reviewKind === 'json' ? 'OpenReview / reviewer JSON' : '已脱敏评审文本'}</span><textarea rows={9} value={reviewRaw} onChange={(event) => setReviewRaw(event.target.value)} spellCheck={false} /></label>}
          {reviewKind !== 'openreview' && <label><span>Source reference · optional</span><input value={reviewRef} onChange={(event) => setReviewRef(event.target.value)} placeholder="submission round / local record id / public URL" /></label>}
          <div className="dialog-actions"><Button onClick={() => setReviewOpen(false)}>取消</Button><Button kind="primary" icon={<Upload size={14} />} onClick={stageReview} disabled={busy === 'review-stage'}>{busy === 'review-stage' ? '正在预检…' : 'Stage & preview'}</Button></div>
        </> : <>
          <div className="review-draft-preview"><span>STAGED · NOT SEALED</span><pre>{JSON.stringify(reviewDraft.parsed ?? reviewDraft, null, 2)}</pre></div>
          <label><span>许可证 / 使用权依据 · 必填</span><input value={reviewLicense} onChange={(event) => setReviewLicense(event.target.value)} placeholder="例如：团队自有评审；或公开 OpenReview 内容仅按许可用于研究分析" /></label>
          <label className="confirm-check"><input type="checkbox" checked={reviewRedacted} onChange={(event) => setReviewRedacted(event.target.checked)} /><span><CheckCircle2 size={12} /></span><p>已核对匿名化、隐私与解析结果，可以封存。</p></label>
          <label className="confirm-check"><input type="checkbox" checked={reviewTraining} onChange={(event) => setReviewTraining(event.target.checked)} /><span><CheckCircle2 size={12} /></span><p>允许进入训练快照（默认关闭）。</p></label>
          <div className="dialog-actions"><Button onClick={() => void discardReview(String(reviewDraft.id))} disabled={busy === 'review-discard'}>{busy === 'review-discard' ? '正在放弃…' : '放弃并返回修改'}</Button><Button kind="primary" icon={<LockKeyhole size={14} />} onClick={confirmReview} disabled={!reviewRedacted || !reviewLicense.trim() || busy === 'review-confirm'}>{busy === 'review-confirm' ? '正在封存…' : '确认评审并写入对象库'}</Button></div>
        </>}
      </div>
    </div>}
  </div>
}
