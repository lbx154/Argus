import type { Snapshot } from '../api';
import { useI18n } from '../i18n';
import './SessionWorkdir.css';

/** Presentation only: never discovers directories or opens the operations panel. */
export function SessionWorkdir({ session }: { session: Pick<Snapshot['session'], 'workdir' | 'cwd'> }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const label = zh ? '项目目录' : 'Session directory';
  const path = session.workdir || session.cwd || '';
  const value = path || (zh ? '待确认' : 'Pending confirmation');
  return <details className="session-workdir" data-session-workdir>
    <summary aria-label={label}>
      <span>{label}</span>
    </summary>
    <div className="session-workdir__detail">
      <strong>{label}</strong>
      <code>{value}</code>
    </div>
  </details>;
}
