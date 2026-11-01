import { useQuery } from '@tanstack/react-query';
import { api, type RoleConfigChoice } from '../api';
import { useI18n } from '../i18n';

/** Each card observes its own CLI/model, sharing discovery with the active editor. */
export function RolePrice({ sid, route, available }: { sid: string; route: RoleConfigChoice; available: boolean }) {
  const { locale } = useI18n(); const zh = locale === 'zh-CN';
  const query = useQuery({ queryKey: ['role-model-choices', sid, route.role, route.backend],
    queryFn: ({ signal }) => api.modelChoices(sid, signal, { role: route.role, backend: route.backend }),
    enabled: available, staleTime: 60_000, refetchInterval: 5 * 60_000, retry: false });
  const model = query.data?.backend === route.backend ? query.data.options.find(row => route.model ? row.model === route.model : row.default) : undefined;
  const quote = model?.pricing;
  const rates = quote?.rates;
  const source = quote?.source === 'cli-catalog' ? (zh ? 'CLI 声明价' : 'CLI rates') : (zh ? '公开参考价' : 'Public reference');
  const checked = quote?.checked_at ? new Date(quote.checked_at).toLocaleTimeString(locale) : '';
  const detail = rates ? `${source} · ${checked}${quote?.stale ? (zh ? ' · 已过期' : ' · stale') : ''}\n`
    + `${zh ? '缓存读/写' : 'Cache read/write'}: ${rates.cachedInput ?? '?'} / ${rates.cacheWrite ?? '?'}\n`
    + (quote?.reference ?? '') : '';
  return <span data-role-price={route.role} className="role-route-price" title={detail}>
    {!available ? (zh ? '原 CLI 不可用，请重新选择' : 'CLI unavailable; choose again')
      : query.isPending ? (zh ? '正在查询此角色费率…' : 'Loading this route’s rates…')
      : rates ? <><span>${rates.input} / ${rates.output} · {zh ? '输入/输出每百万' : 'in/out per 1M'}</span>
        <small>{source} · {checked}{quote?.stale && (zh ? ' · 已过期' : ' · stale')}</small></>
      : (zh ? '费率未知，不能完整估算' : 'Unknown rates; estimate incomplete')}
  </span>;
}
