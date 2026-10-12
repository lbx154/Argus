import type { ResourceStatus } from '../../../core/src/resourceStatus.generated';
import { formatResourceTtl } from '../../../core/src/resourceStatus';
import { useI18n } from '../i18n';
import { Cpu } from 'lucide-react';
import './resourceStatus.css';
import {
  acceleratorLabel,
  enforcementLabel,
  statusLabel,
  yieldDecisionLabel,
} from '../lib/enumLabels';

const probeTone = {
  available: 'bg-ok/10 text-ok',
  absent: 'bg-bg text-ink-faint',
  inaccessible: 'bg-warn/10 text-warn',
  degraded: 'bg-warn/10 text-warn',
} as const;

export function ResourceStatusView({
  status,
  error,
}: {
  status: ResourceStatus | null;
  error: string;
}) {
  const { t, locale } = useI18n();
  const zh = locale === 'zh-CN';
  return (
    <section className="rounded-lg border border-line bg-panel p-4 lg:col-span-2">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-dim">{t('resource.title')}</h3>
        {status ? (
          <span className={`rounded px-2 py-1 text-[10px] font-semibold uppercase ${status.enforcement === 'strict' ? 'bg-ok/10 text-ok' : 'bg-warn/10 text-warn'}`}>
            {enforcementLabel(status.enforcement, t)}
          </span>
        ) : null}
      </div>
      {error ? <p className="mt-3 text-xs text-err">{error}</p> : null}
      {!status && !error ? <p className="mt-3 text-xs text-ink-faint">{t('resource.loading')}</p> : null}
      {status ? (
        <>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {status.accelerators.map((accelerator) => (
              <div key={accelerator.kind} className="rounded border border-line bg-bg p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-ink">{acceleratorLabel(accelerator.kind, t)}</span>
                  <span className={`rounded px-2 py-0.5 text-[10px] font-medium ${probeTone[accelerator.status]}`}>
                    {statusLabel(accelerator.status, t)} · {t('resource.devices', { count: accelerator.device_count })}
                  </span>
                </div>
                {accelerator.detail ? <p className="mt-2 text-xs text-ink-faint">{accelerator.detail}</p> : null}
              </div>
            ))}
          </div>
          <div className="gpu-device-grid">
            {status.accelerators.flatMap(accelerator => (accelerator.devices ?? []).map(device => {
              const state = device.utilization_percent > 0 ? 'computing' : device.reserved ? 'reserved' : device.used_memory_mib > 0 ? 'allocated' : 'quiet';
              const label = state === 'computing' ? zh ? '正在计算' : 'Computing'
                : state === 'reserved' ? zh ? '已预留' : 'Reserved'
                : state === 'allocated' ? zh ? '仅显存占用' : 'Memory occupied'
                : zh ? '暂无活动' : 'No activity';
              const memoryPercent = device.total_memory_mib > 0 ? Math.min(100, device.used_memory_mib / device.total_memory_mib * 100) : 0;
              return <article key={`${accelerator.kind}:${device.index}`} className="gpu-device-card" data-gpu-state={state} aria-label={`GPU ${device.index} · ${device.name} · ${label}`}>
                <div className="gpu-device-header">
                  <span className="gpu-device-icon" aria-hidden="true"><Cpu size={26} strokeWidth={1.5} /></span>
                  <div><span className="gpu-device-number">{acceleratorLabel(accelerator.kind, t)} · GPU {device.index}</span><h4>{device.name}</h4></div>
                  <span className="gpu-device-status"><i aria-hidden="true" />{label}</span>
                </div>
                <div className="gpu-device-meter">
                  <div><span>{zh ? '计算负载' : 'Compute load'}</span><strong>{device.utilization_percent}%</strong></div>
                  <div role="meter" aria-label={zh ? '计算负载' : 'Compute load'} aria-valuemin={0} aria-valuemax={100} aria-valuenow={device.utilization_percent}><i style={{ width: `${device.utilization_percent}%` }} /></div>
                </div>
                <div className="gpu-device-meter">
                  <div><span>{zh ? '显存' : 'GPU memory'}</span><strong>{device.used_memory_mib < 1024 ? `${device.used_memory_mib} MiB` : `${(device.used_memory_mib / 1024).toFixed(1)} GiB`} / {(device.total_memory_mib / 1024).toFixed(1)} GiB</strong></div>
                  <div role="meter" aria-label={zh ? '显存占用' : 'GPU memory usage'} aria-valuemin={0} aria-valuemax={device.total_memory_mib || 1} aria-valuenow={device.used_memory_mib}><i style={{ width: `${memoryPercent}%` }} /></div>
                </div>
                {device.reserved && state !== 'reserved' && <p className="gpu-device-note">{zh ? '已由 Argus 预留' : 'Reserved by Argus'}</p>}
              </article>;
            }))}
          </div>
          {!!status.accelerators.some(accelerator => accelerator.device_count > 0 && !accelerator.devices) && <p className="mt-3 text-xs text-ink-faint">{zh ? '当前服务尚未提供单卡详情，不能判断各卡使用情况。' : 'This server has no per-device details; individual usage is unknown.'}</p>}
          <p className="mt-3 text-xs text-ink-faint">{zh ? '编号与主机一致。负载和显存包含其他程序，少量显存可能来自驱动；“暂无活动”是采样结果，不保证可独占使用。' : 'Numbers match host GPU indices. Load and memory include other programs; small memory use can come from drivers. “No activity” is a sample, not a guarantee of exclusive availability.'}</p>
          <details className="mt-4">
            <summary className="cursor-pointer text-xs text-ink-dim">{zh ? `Argus 资源预约 ${status.holders.length} · 等待 ${status.queue.length}` : `Argus reservations ${status.holders.length} · Waiting ${status.queue.length}`}</summary>
          <div className="mt-3 grid gap-4 md:grid-cols-2">
            <div>
              <h4 className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t('resource.inUse', { count: status.holders.length })}</h4>
              <div className="mt-2 space-y-2">
                {status.holders.length === 0 ? <p className="text-xs text-ink-faint">{t('resource.none')}</p> : status.holders.map((holder, index) => (
                  <div key={`${holder.project}:${holder.task_id}:${index}`} className="rounded border border-line bg-bg p-3 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-ink">{t('resource.devices', { count: holder.device_count })}</span>
                      <span className="shrink-0 font-mono text-ink-faint">{t('resource.timeLeft', { ttl: formatResourceTtl(holder.ttl_seconds) })}</span>
                    </div>
                    <p className="mt-1 text-ink-dim">{holder.intent || t('resource.noIntent')}</p>
                    {holder.yield_requests.map((request, requestIndex) => (
                      <div key={requestIndex} className="mt-2 border-l-2 border-warn/50 pl-2 text-ink-faint">
                        <div>{t('resource.yieldRequest', { reason: request.reason })}</div>
                        {request.response ? <div>{yieldDecisionLabel(request.response.decision, t)} · {request.response.reason}</div> : null}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </div>
            <div>
              <h4 className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t('resource.queue', { count: status.queue.length })}</h4>
              <div className="mt-2 space-y-2">
                {status.queue.length === 0 ? <p className="text-xs text-ink-faint">{t('resource.none')}</p> : status.queue.map((request) => (
                  <div key={request.position} className="rounded border border-line bg-bg p-3 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-ink">{t('resource.queuePosition', { position: request.position })}</span>
                      <span className="shrink-0 font-mono text-ink-faint">{t('resource.timeLeft', { ttl: formatResourceTtl(request.ttl_seconds) })}</span>
                    </div>
                    <p className="mt-1 text-ink-dim">{request.intent || t('resource.noIntent')}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
          </details>
        </>
      ) : null}
    </section>
  );
}
