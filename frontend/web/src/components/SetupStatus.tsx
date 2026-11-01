import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { QRCodeSVG } from 'qrcode.react';
import { api, mobilePairingUrl, type ConfigSnapshot } from '../api';
import { useI18n } from '../i18n';
import { backendLabel } from '../lib/backend';
import { requestFailureText } from '../lib/requestFailure';

export function SetupStatus({ open, config }: { open: boolean; config?: ConfigSnapshot }) {
  const { locale, t } = useI18n();
  const zh = locale === 'zh-CN';
  const probe = useQuery({ queryKey: ['setup-status'], queryFn: ({ signal }) => api.setup(signal), enabled: open, retry: false });
  const [selected, setSelected] = useState('');
  const data = probe.data;
  const origin = data?.mobile_origins.includes(selected) ? selected : data?.mobile_origins[0];
  const url = origin ? mobilePairingUrl(origin) : '';
  return <section className="settings-section mt-4" data-setup-status>
    <h3 className="mb-3 text-sm font-semibold">{zh ? '后端与沙箱' : 'Backends and sandbox'}</h3>
    {probe.isPending && <p className="text-xs text-ink-faint">{zh ? '正在探测…' : 'Checking…'}</p>}
    {probe.isError && <p role="alert" className="text-xs text-err">{requestFailureText(probe.error, t).text}</p>}
    {data && <>
      <dl className="grid gap-3 sm:grid-cols-2">
        {data.backends.filter(row => row.installed).map(row => <div key={row.backend} className="min-w-0 rounded border border-line p-3">
          <dt className="text-xs font-semibold">{backendLabel(row.backend, config)}</dt>
          <dd className="mt-1 break-all font-mono text-xs text-ink-faint">{row.version || (zh ? '版本未知' : 'Version unknown')}</dd>
          {row.detail && <dd className="mt-1 break-words text-xs text-ink-faint">{row.detail}</dd>}
        </div>)}
      </dl>
      {!data.backends.some(row => row.installed) && <p className="text-xs">{zh ? '未发现 Agent CLI，请安装或在桌面设置中指定路径。' : 'No Agent CLI found. Install one or select its path in desktop settings.'}</p>}
      <p className="mt-3 text-xs" data-sandbox-status>{data.sandbox.bubblewrap
        ? zh ? 'Bubblewrap：运行探测成功，角色调用使用系统沙箱。' : 'Bubblewrap: launch probe passed; agent calls use OS isolation.'
        : zh ? 'Bubblewrap：未启用（未安装、运行探测失败或系统不支持）。角色调用使用后端自身权限设置，不代表系统隔离。' : 'Bubblewrap: inactive (missing, launch probe failed, or unsupported OS). Agent calls use backend permission settings; OS isolation is not confirmed.'}
        {' '}({data.sandbox.platform})</p>
      {url && <div className="mt-5 border-t border-line pt-4" data-mobile-pairing>
        <h3 className="text-sm font-semibold">{zh ? '在手机上打开' : 'Open on your phone'}</h3>
        <p className="my-2 text-xs text-ink-faint">{zh ? '手机连接同一网络后扫码。地址含访问令牌，仅分享给你信任的人。' : 'Connect your phone to the same network and scan. This address includes your access token; share it with people you trust.'}</p>
        {data.mobile_origins.length > 1 && <select aria-label={zh ? '主机地址' : 'Host address'} value={origin} onChange={event => setSelected(event.target.value)} className="mb-3 max-w-full rounded border border-line bg-bg p-2 text-xs">
          {data.mobile_origins.map(address => <option key={address} value={address}>{address}</option>)}
        </select>}
        <QRCodeSVG value={url} size={192} marginSize={4} title={zh ? '在手机上打开 Argus' : 'Open Argus on your phone'} className="max-w-full" />
        <a href={url} className="mt-2 block break-all text-xs text-blue" data-mobile-url>{url}</a>
      </div>}
    </>}
  </section>;
}
