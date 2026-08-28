import type { ResourceStatus } from '../../../core/src/resourceStatus.generated';
import { formatResourceTtl } from '../../../core/src/resourceStatus';
import { useI18n } from '../i18n';
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
  const { t } = useI18n();
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
          <div className="mt-4 grid gap-4 md:grid-cols-2">
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
        </>
      ) : null}
    </section>
  );
}
