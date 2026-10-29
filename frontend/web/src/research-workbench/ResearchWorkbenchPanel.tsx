import { useCallback, useEffect, useState } from 'react';
import { useI18n } from '../i18n';
import { EmptyState, Spinner } from './components/Common';
import { IdePage } from './pages/IdePage';
import type { ActiveWorkbenchPageProps } from './pages/pageTypes';
import { RuntimeControlPage } from './pages/RuntimeControlPage';
import { WORKBENCH_MODULES } from './modules';
import type { PageId, Snapshot } from './types';
import './styles.css';

/** The project's modules, on the snapshot and the record the page already follows. */
export function ResearchWorkbenchPanel({ snapshot, failed, runtimeRequest = 0, readOnly = false, onRestored, ...data }:
  Omit<ActiveWorkbenchPageProps, 'snapshot'> & {
    snapshot: Snapshot | undefined; failed: boolean; readOnly?: boolean; runtimeRequest?: number;
    onRestored: (sid: string) => void | Promise<void>;
  }) {
  const { locale } = useI18n();
  const [page, setPage] = useState<PageId>(() => {
    const requested = new URLSearchParams(window.location.search).get('module');
    return WORKBENCH_MODULES.find((module) => module.id === requested)?.id ?? 'ide';
  });
  const [openedPages, setOpenedPages] = useState(() => new Set<PageId>([page]));
  const navigate = useCallback((next: PageId) => {
    setPage(next);
    setOpenedPages((current) => current.has(next) ? current : new Set([...current, next]));
  }, []);
  useEffect(() => {
    if (runtimeRequest) navigate('runtime');
  }, [runtimeRequest, navigate]);

  return (
    <section className="integrated-workbench flex min-h-0 flex-1 flex-col bg-transparent text-ink">
      <nav className="workbench-module-tabs shrink-0 border-b border-line/60 px-3 py-2" aria-label={locale === 'zh-CN' ? '工作台模块' : 'Workbench modules'}>
        <div className="flex flex-wrap gap-1">
          {WORKBENCH_MODULES.map(({ id, zh, en, icon: Icon }) => (
            <button
              key={id}
              type="button"
              className="workbench-module-tab"
              data-module={id}
              data-selected={page === id}
              aria-pressed={page === id}
              onClick={() => navigate(id)}
            >
              <Icon size={14} />
              <span>{locale === 'zh-CN' ? zh : en}</span>
            </button>
          ))}
        </div>
      </nav>
      {!snapshot ? failed ? (
        <EmptyState title={locale === 'zh-CN' ? '工作台读取失败' : 'Workbench unavailable'} />
      ) : (
        <div className="boot-state"><Spinner label={locale === 'zh-CN' ? '正在载入工作台' : 'Loading workbench'} /></div>
      ) : WORKBENCH_MODULES.filter(({ id }) => openedPages.has(id)).map(({ id }) => {
        const props = { ...data, snapshot, active: data.active && page === id };
        return (
          <div key={`${data.sid}:${id}`} className={`ros-content workspace-view-surface min-h-0 flex-1 overflow-x-hidden overflow-y-auto ${page === id ? '' : 'hidden'}`} aria-hidden={page !== id}>
            {id === 'runtime' ? <RuntimeControlPage {...props} readOnly={readOnly} onRestored={onRestored} /> : <IdePage {...props} />}
          </div>
        );
      })}
    </section>
  );
}
