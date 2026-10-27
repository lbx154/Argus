import { useCallback, useEffect, useMemo, useState } from 'react';
import { useI18n } from '../i18n';
import { EmptyState, Spinner } from './components/Common';
import { ExperimentsPage } from './pages/ExperimentsPage';
import { IdePage } from './pages/IdePage';
import { TimelinePage } from './timeline/TimelinePage';
import type { ActiveWorkbenchPageProps } from './pages/pageTypes';
import { RuntimeControlPage } from './pages/RuntimeControlPage';
import { WORKBENCH_MODULES } from './modules';
import type { PageId } from './types';
import { useArgusData, useProjects } from './useArgusData';
import './styles.css';

export function ResearchWorkbenchPanel({ sid, active, readOnly = false, runtimeRequest = 0, onRestored, onCompose }: {
  sid: string; active: boolean; readOnly?: boolean; runtimeRequest?: number;
  onRestored: (sid: string) => void | Promise<void>;
  onCompose: () => void;
}) {
  const { locale } = useI18n();
  const [page, setPage] = useState<PageId>(() => {
    const requested = new URLSearchParams(window.location.search).get('module');
    return WORKBENCH_MODULES.find((module) => module.id === requested)?.id ?? 'experiments';
  });
  const [openedPages, setOpenedPages] = useState(() => new Set<PageId>([page]));
  const navigate = useCallback((next: PageId) => {
    setPage(next);
    setOpenedPages((current) => current.has(next) ? current : new Set([...current, next]));
  }, []);
  useEffect(() => {
    if (runtimeRequest) navigate('runtime');
  }, [runtimeRequest, navigate]);
  const projectsQ = useProjects(active);
  const projects = projectsQ.data?.projects ?? [];
  const project = useMemo(() => projects.find((item) => item.id === sid) ?? null, [projects, sid]);
  const data = useArgusData(sid, active);
  const activePage = page;
  const controlError = data.controls.start.error || data.controls.stop.error;
  const pageProps: ActiveWorkbenchPageProps | null = project && data.snapshot.data ? {
    sid,
    active,
    project,
    snapshot: data.snapshot.data,
    status: data.status.data,
    events: data.events,
    connected: data.connected,
    snapshotUpdatedAt: data.snapshot.dataUpdatedAt,
    refresh: data.refresh,
    controls: {
      start: async () => { try { return await data.controls.start.mutateAsync(); } catch { return null; } },
      stop: async (drain) => { try { return await data.controls.stop.mutateAsync(drain); } catch { return null; } },
      busy: data.controls.start.isPending || data.controls.stop.isPending,
      error: controlError instanceof Error ? controlError.message : '',
    },
    navigate,
  } : null;

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
              data-selected={activePage === id}
              aria-pressed={activePage === id}
              onClick={() => navigate(id)}
            >
              <Icon size={14} />
              <span>{locale === 'zh-CN' ? zh : en}</span>
            </button>
          ))}
        </div>
      </nav>
      {projectsQ.isError && !project || data.snapshot.isError && !data.snapshot.data ? (
        <EmptyState title={locale === 'zh-CN' ? '工作台读取失败' : 'Workbench unavailable'} />
      ) : !pageProps ? (
        <div className="boot-state"><Spinner label={locale === 'zh-CN' ? '正在载入工作台' : 'Loading workbench'} /></div>
      ) : WORKBENCH_MODULES.filter(({ id }) => openedPages.has(id)).map(({ id }) => (
        <div key={`${sid}:${id}`} className={`ros-content workspace-view-surface min-h-0 flex-1 overflow-x-hidden overflow-y-auto ${activePage === id ? '' : 'hidden'}`} aria-hidden={activePage !== id}>
          {id === 'runtime' ? <RuntimeControlPage {...pageProps} active={active && activePage === id} readOnly={readOnly} onRestored={onRestored} onCompose={onCompose} />
            : id === 'timeline' ? <TimelinePage sid={sid} />
            : id === 'experiments' ? <ExperimentsPage {...pageProps} active={active && activePage === id} readOnly={readOnly} onCompose={onCompose} />
            : <IdePage {...pageProps} active={active && activePage === id} />}
        </div>
      ))}
    </section>
  );
}
