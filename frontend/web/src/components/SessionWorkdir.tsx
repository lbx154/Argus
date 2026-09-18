import type { Snapshot } from '../api';
import { useI18n } from '../i18n';
import './SessionWorkdir.css';

/** Presentation only: never discovers directories or opens the operations panel. */
export function SessionWorkdir({ session }: { session: Pick<Snapshot['session'], 'workdir' | 'cwd'> }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const label = zh ? '会话目录' : 'Session directory';
  const path = session.workdir || session.cwd || '';
  const value = path || (zh ? '待确认' : 'Pending confirmation');
  return <details className="session-workdir" data-session-workdir>
    <summary title={`${label}: ${value}`} aria-label={`${label}: ${value}`}>
      <span>{label}</span><code>{value}</code>
    </summary>
    <div className="session-workdir__detail">
      <strong>{label}</strong>
      <code>{value}</code>
      <p>{zh
        ? '来自已加载会话快照的工作目录；不是每个任务的实际 cwd，也不是安全沙箱边界。'
        : 'Working directory from the loaded session snapshot; not each task’s actual cwd or a security sandbox boundary.'}</p>
    </div>
  </details>;
}
