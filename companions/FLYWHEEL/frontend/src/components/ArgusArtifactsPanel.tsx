import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CheckCircle2, FileBox, LockKeyhole, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import {
  confirmArgusArtifactImport,
  discardArgusArtifactImport,
  loadArgusArtifactImport,
  loadArgusArtifactImports,
  loadArgusArtifacts,
  stageArgusArtifactImport,
} from '../api/client'
import type { ArgusArtifactImport, ArgusArtifactIndex, ArgusArtifactIndexItem, ArgusArtifactRole } from '../types'
import { useI18n } from '../lib/preferences'
import { Button, Notice, StatusPill } from './ui'

const ROLES: Array<{ value: ArgusArtifactRole; key: string }> = [
  { value: 'condition_snapshot', key: 'artifact.role.conditionSnapshot' },
  { value: 'prompt_contract', key: 'artifact.role.promptContract' },
  { value: 'trajectory', key: 'artifact.role.trajectory' },
  { value: 'experiment_spec', key: 'artifact.role.experimentSpec' },
  { value: 'experiment_result', key: 'artifact.role.experimentResult' },
  { value: 'paper', key: 'artifact.role.paper' },
  { value: 'outcome', key: 'artifact.role.outcome' },
  { value: 'review_certificate', key: 'artifact.role.reviewCertificate' },
  { value: 'integrity_report', key: 'artifact.role.integrityReport' },
  { value: 'reproducibility_manifest', key: 'artifact.role.reproducibilityManifest' },
]

const shortHash = (value?: string | null, unavailable = 'SHA unavailable') => value ? `${value.slice(0, 10)}…${value.slice(-6)}` : unavailable
const formatBytes = (value: number | null | undefined, unknownLabel: string) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return unknownLabel
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`
  return `${(value / 1024 ** 2).toFixed(1)} MiB`
}

function importTone(item: ArgusArtifactImport): 'good' | 'warn' | 'neutral' {
  if (item.state === 'confirmed' && item.sealed_in_head) return 'good'
  if (item.state === 'draft' || (item.state === 'confirmed' && !item.sealed_in_head)) return 'warn'
  return 'neutral'
}

export function ArgusArtifactsPanel({
  episodeId,
  disabled,
  onChanged,
  onRequestSeal,
  toast,
}: {
  episodeId: string
  disabled: boolean
  onChanged: () => Promise<void>
  onRequestSeal: () => void
  toast: (message: string) => void
}) {
  const { t } = useI18n()
  const [catalog, setCatalog] = useState<ArgusArtifactIndex | null>(null)
  const [imports, setImports] = useState<ArgusArtifactImport[]>([])
  const [importsReady, setImportsReady] = useState(false)
  const [roles, setRoles] = useState<Record<string, ArgusArtifactRole | ''>>({})
  const [activeDraft, setActiveDraft] = useState<ArgusArtifactImport | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [licenseBasis, setLicenseBasis] = useState('')
  const [redactionConfirmed, setRedactionConfirmed] = useState(false)
  const [manualRedactionConfirmed, setManualRedactionConfirmed] = useState(false)
  const [trainingConsent, setTrainingConsent] = useState(false)
  const [disposition, setDisposition] = useState<'as_is' | 'replace_text'>('as_is')
  const [replacementText, setReplacementText] = useState('')
  const idempotencyKeys = useRef(new Map<string, string>())

  const resetConfirmation = useCallback(() => {
    setLicenseBasis('')
    setRedactionConfirmed(false)
    setManualRedactionConfirmed(false)
    setTrainingConsent(false)
    setDisposition('as_is')
    setReplacementText('')
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    setImportsReady(false)
    setError('')
    const [catalogResult, importsResult] = await Promise.allSettled([
      loadArgusArtifacts(episodeId),
      loadArgusArtifactImports(episodeId),
    ])
    if (catalogResult.status === 'fulfilled') setCatalog(catalogResult.value)
    else setCatalog(null)
    if (importsResult.status === 'fulfilled') {
      setImports(importsResult.value)
      setImportsReady(true)
    } else {
      setImports([])
    }
    const failures = [catalogResult, importsResult]
      .filter((result): result is PromiseRejectedResult => result.status === 'rejected')
      .map((result) => result.reason instanceof Error ? result.reason.message : 'unknown error')
    setError([...new Set(failures)].join(' · '))
    setLoading(false)
  }, [episodeId])

  useEffect(() => {
    setActiveDraft(null)
    resetConfirmation()
    void refresh()
  }, [episodeId, refresh, resetConfirmation])

  const confirmedUnsealed = useMemo(
    () => imports.filter((item) => item.state === 'confirmed' && !item.sealed_in_head),
    [imports],
  )

  const activeVersions = useMemo(
    () => new Set(imports
      .filter((item) => item.state !== 'discarded')
      .map((item) => `${item.source_entry?.path ?? ''}:${item.source_entry_sha256.toLowerCase()}`)),
    [imports],
  )

  const panelLocked = disabled || loading || Boolean(busy)
  const roleLabel = (role: ArgusArtifactRole) => {
    const option = ROLES.find((item) => item.value === role)
    return option ? t(option.key) : role
  }
  const importLabel = (item: ArgusArtifactImport) => {
    if (item.state === 'draft') return t('artifact.state.staging')
    if (item.state === 'discarded') return t('artifact.state.discarded')
    return item.sealed_in_head ? t('artifact.state.sealed') : t('artifact.state.confirmedUnsealed')
  }

  const stage = async (item: ArgusArtifactIndexItem) => {
    if (disabled || loading || busy || !importsReady) return
    const role = roles[item.path]
    const expectedEntrySha = item.entry_sha256?.toLowerCase() ?? ''
    if (!role || !/^[0-9a-f]{64}$/.test(expectedEntrySha)) return
    const operationKey = `${episodeId}:${item.path}:${role}:${expectedEntrySha}`
    const idempotencyKey = idempotencyKeys.current.get(operationKey) ?? crypto.randomUUID()
    idempotencyKeys.current.set(operationKey, idempotencyKey)
    setBusy(`stage:${item.path}`)
    try {
      const draft = await stageArgusArtifactImport(episodeId, {
        artifact_path: item.path,
        role,
        expected_entry_sha256: expectedEntrySha,
        idempotency_key: idempotencyKey,
      })
      idempotencyKeys.current.delete(operationKey)
      setActiveDraft(draft)
      resetConfirmation()
      await refresh()
      toast(t('artifact.toast.staged'))
    } catch (cause) {
      toast(t('artifact.toast.stageFailed', { error: cause instanceof Error ? cause.message : 'unknown error' }))
    } finally { setBusy('') }
  }

  const inspectDraft = async (item: ArgusArtifactImport) => {
    if (disabled || loading || busy) return
    setBusy(`inspect:${item.id}`)
    try {
      const detail = await loadArgusArtifactImport(item.id)
      setActiveDraft(detail)
      resetConfirmation()
    } catch (cause) {
      toast(t('artifact.toast.previewFailed', { error: cause instanceof Error ? cause.message : 'unknown error' }))
    } finally { setBusy('') }
  }

  const confirm = async () => {
    if (disabled || loading || busy || !activeDraft || !redactionConfirmed || !licenseBasis.trim()) return
    if (activeDraft.manual_redaction_required && !manualRedactionConfirmed) return
    if (disposition === 'replace_text' && !replacementText.trim()) return
    setBusy(`confirm:${activeDraft.id}`)
    try {
      await confirmArgusArtifactImport(activeDraft.id, {
        expected_source_sha256: activeDraft.source_sha256,
        redaction_confirmed: true,
        manual_redaction_confirmed: manualRedactionConfirmed,
        training_consent: trainingConsent,
        license_basis: licenseBasis.trim(),
        disposition,
        replacement_text: disposition === 'replace_text' ? replacementText : undefined,
      })
      setActiveDraft(null)
      resetConfirmation()
      await refresh()
      await onChanged()
      toast(t('artifact.toast.confirmed'))
    } catch (cause) {
      toast(t('artifact.toast.confirmFailed', { error: cause instanceof Error ? cause.message : 'unknown error' }))
    } finally { setBusy('') }
  }

  const discard = async () => {
    if (disabled || loading || busy || !activeDraft) return
    setBusy(`discard:${activeDraft.id}`)
    try {
      await discardArgusArtifactImport(activeDraft.id, 'Human rejected the staged Argus artifact after bounded preview and rights review.')
      setActiveDraft(null)
      resetConfirmation()
      await refresh()
      await onChanged()
      toast(t('artifact.toast.discarded'))
    } catch (cause) {
      toast(t('artifact.toast.discardFailed', { error: cause instanceof Error ? cause.message : 'unknown error' }))
    } finally { setBusy('') }
  }

  return <section className="argus-artifacts-section">
    <div className="section-heading">
      <div><h3>{t('artifact.title')}</h3></div>
      <Button icon={<RefreshCw size={13} />} onClick={() => void refresh()} disabled={panelLocked}>{loading ? t('common.loading') : t('common.refresh')}</Button>
    </div>

    {error && <Notice tone="warn" title={t('artifact.partialUnavailable')}>{error}</Notice>}
    {confirmedUnsealed.length > 0 && <div className="artifact-reseal-callout">
      <LockKeyhole size={16} /><div><strong>{t('artifact.resealCount', { count: confirmedUnsealed.length })}</strong></div><Button kind="primary" onClick={onRequestSeal} disabled={panelLocked}>{t('artifact.action.reseal')}</Button>
    </div>}

    <div className="argus-artifact-grid">
      <div className="artifact-catalog">
        <header><strong>{t('artifact.catalog')}</strong><span>{catalog?.items.length ?? 0}</span></header>
        {catalog?.items.length ? catalog.items.map((item) => {
          const role = roles[item.path] ?? ''
          const versionKey = `${item.path}:${(item.entry_sha256 ?? '').toLowerCase()}`
          const included = activeVersions.has(versionKey)
          const stageable = importsReady && item.exists && /^[0-9a-f]{64}$/i.test(item.entry_sha256 ?? '') && !included
          return <article key={item.path}>
            <FileBox size={15} />
            <div><strong>{item.name || item.path.split('/').pop() || item.path}</strong><span title={item.path}>{item.path}</span><small>{item.kind} · {formatBytes(item.size, t('artifact.sizeUnknown'))} · <code>{shortHash(item.sha256 ?? item.entry_sha256, t('artifact.shaUnavailable'))}</code></small></div>
            <select aria-label={`${item.path} ${t('artifact.roleLabel')}`} value={role} onChange={(event) => setRoles((current) => ({ ...current, [item.path]: event.target.value as ArgusArtifactRole | '' }))} disabled={!stageable || panelLocked}>
              <option value="">{t('artifact.roleChoose')}</option>{ROLES.map((option) => <option value={option.value} key={option.value}>{t(option.key)}</option>)}
            </select>
            <Button onClick={() => void stage(item)} disabled={!stageable || !role || panelLocked}>{busy === `stage:${item.path}` ? t('artifact.action.staging') : included ? t('artifact.action.included') : t('artifact.action.stage')}</Button>
          </article>
        }) : <p className="artifact-empty">{loading ? t('common.loading') : t('artifact.catalogEmpty')}</p>}
      </div>

      <div className="artifact-import-ledger">
        <header><strong>{t('artifact.imports')}</strong><span>{imports.length}</span></header>
        {imports.length ? imports.map((item) => <article key={item.id}>
          <div><strong>{item.source_entry?.name || item.source_entry?.path || item.id.slice(0, 8)}</strong><span>{roleLabel(item.role)} · {formatBytes(item.source_byte_length, t('artifact.sizeUnknown'))}</span><small><code>{shortHash(item.source_sha256, t('artifact.shaUnavailable'))}</code> · {item.scan_state || t('artifact.scanPending')}</small></div>
          <StatusPill tone={importTone(item)}>{importLabel(item)}</StatusPill>
          {item.state === 'draft' && <Button onClick={() => void inspectDraft(item)} disabled={panelLocked}>{busy === `inspect:${item.id}` ? t('common.loading') : t('artifact.action.inspect')}</Button>}
        </article>) : <p className="artifact-empty">{importsReady ? t('artifact.importsEmpty') : t('artifact.importsUnavailable')}</p>}
      </div>
    </div>

    {activeDraft?.state === 'draft' && <div className="artifact-confirmation">
      <div className="artifact-preview-head"><div><span>STAGED · NOT SEALED</span><strong>{activeDraft.source_entry?.path}</strong></div><StatusPill tone="warn">{roleLabel(activeDraft.role)}</StatusPill></div>
      <div className="artifact-proof-row"><span>Source SHA-256</span><code>{activeDraft.source_sha256}</code><span>{formatBytes(activeDraft.source_byte_length, t('artifact.sizeUnknown'))}</span></div>
      {activeDraft.preview?.available ? <pre>{activeDraft.preview.text || t('artifact.preview.empty')}{activeDraft.preview.truncated ? `\n\n${t('artifact.preview.truncated')}` : ''}</pre> : <p className="artifact-preview-unavailable">{t('artifact.preview.unavailable')}</p>}
      <div className="artifact-confirm-fields">
        <label><span>{t('artifact.disposition.label')}</span><select value={disposition} onChange={(event) => setDisposition(event.target.value as 'as_is' | 'replace_text')}><option value="as_is">{t('artifact.disposition.asIs')}</option><option value="replace_text">{t('artifact.disposition.replaceText')}</option></select></label>
        <label><span>{t('artifact.license.label')}</span><input value={licenseBasis} onChange={(event) => setLicenseBasis(event.target.value)} placeholder={t('artifact.license.placeholder')} /></label>
        {disposition === 'replace_text' && <label className="wide"><span>{t('artifact.replacement.label')}</span><textarea rows={7} value={replacementText} onChange={(event) => setReplacementText(event.target.value)} /></label>}
      </div>
      <label className="confirm-check"><input type="checkbox" checked={redactionConfirmed} onChange={(event) => setRedactionConfirmed(event.target.checked)} /><span><CheckCircle2 size={12} /></span><p>{t('artifact.confirm.redaction')}</p></label>
      {activeDraft.manual_redaction_required && <label className="confirm-check"><input type="checkbox" checked={manualRedactionConfirmed} onChange={(event) => setManualRedactionConfirmed(event.target.checked)} /><span><ShieldCheck size={12} /></span><p>{t('artifact.confirm.manualRedaction')}</p></label>}
      <label className="confirm-check"><input type="checkbox" checked={trainingConsent} onChange={(event) => setTrainingConsent(event.target.checked)} /><span><CheckCircle2 size={12} /></span><p>{t('artifact.confirm.trainingConsent')}</p></label>
      <div className="artifact-confirm-actions"><Button kind="danger" icon={<Trash2 size={13} />} onClick={() => void discard()} disabled={panelLocked}>{busy.startsWith('discard:') ? t('artifact.action.discarding') : t('artifact.action.discard')}</Button><Button kind="primary" icon={<LockKeyhole size={13} />} onClick={() => void confirm()} disabled={panelLocked || !redactionConfirmed || !licenseBasis.trim() || (activeDraft.manual_redaction_required && !manualRedactionConfirmed) || (disposition === 'replace_text' && !replacementText.trim())}>{busy.startsWith('confirm:') ? t('artifact.action.confirming') : t('artifact.action.confirm')}</Button></div>
    </div>}
  </section>
}
