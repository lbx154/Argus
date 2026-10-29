import type { ResourceStatus } from '../../../core/src/resourceStatus.generated';
import { useI18n } from '../i18n';
import { Cpu } from 'lucide-react';
import './resourceStatus.css';
import {
  acceleratorLabel,
  enforcementLabel,
  statusLabel,
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
                    {statusLabel(accelerator.status, t)} · {accelerator.kind === 'cpu' ? (zh ? `${accelerator.device_count} 核` : `${accelerator.device_count} cores`) : t('resource.devices', { count: accelerator.device_count })}
                  </span>
                </div>
                {accelerator.kind === 'cpu' && accelerator.load_average !== undefined
                  && accelerator.used_memory_gib !== undefined && accelerator.total_memory_gib !== undefined
                  ? <p className="mt-2 text-xs text-ink-faint">{t('resource.cpuUsage', {
                    load: accelerator.load_average.toFixed(2), used: accelerator.used_memory_gib.toFixed(1), total: accelerator.total_memory_gib.toFixed(1),
                  })}</p> : null}
                {accelerator.detail ? <p className="mt-2 text-xs text-ink-faint">{accelerator.detail}</p> : null}
              </div>
            ))}
          </div>
          <div className="gpu-device-grid">
            {status.accelerators.flatMap(accelerator => (accelerator.devices ?? []).map(device => {
              const state = device.utilization_percent > 0 ? 'computing' : device.used_memory_mib > 0 ? 'allocated' : 'quiet';
              const label = state === 'computing' ? zh ? '正在计算' : 'Computing'
                : state === 'allocated' ? zh ? '计算负载 0% · 有显存占用' : '0% compute load · memory in use'
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
              </article>;
            }))}
          </div>
          {!!status.accelerators.some(accelerator => accelerator.kind !== 'cpu' && accelerator.device_count > 0 && !accelerator.devices) && <p className="mt-3 text-xs text-ink-faint">{zh ? '当前服务尚未提供单卡详情，不能判断各卡使用情况。' : 'This server has no per-device details; individual usage is unknown.'}</p>}
          <p className="mt-3 text-xs text-ink-faint">{zh ? '编号与主机一致；负载和显存含其他程序。' : 'Numbers match host GPU indices; load and memory include other programs.'}</p>
        </>
      ) : null}
    </section>
  );
}
