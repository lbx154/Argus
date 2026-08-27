import {
  BookOpen,
  Code2,
  FileText,
  FlaskConical,
  FolderKanban,
  Inbox,
  Megaphone,
  MessagesSquare,
  Radar,
  ShieldCheck,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { useI18n } from '../i18n';
import { EmptyState, Spinner } from './components/Common';
import { CopilotPage } from './pages/CopilotPage';
import { CounterexamplePage } from './pages/CounterexamplePage';
import { ExperimentsPage } from './pages/ExperimentsPage';
import { IdePage } from './pages/IdePage';
import { InboxPage } from './pages/InboxPage';
import { LiteraturePage } from './pages/LiteraturePage';
import type { WorkspacePageProps } from './pages/pageTypes';
import { PaperPage } from './pages/PaperPage';
import { ProjectOverviewPage } from './pages/ProjectOverviewPage';
import { ReviewerPage } from './pages/ReviewerPage';
import { ReleasePage } from './pages/ReleasePage';
import type { PageId } from './types';
import { useArgusData, useProjects } from './useArgusData';
import './styles.css';

const MODULES = [
  ['overview', '项目概览', 'Project overview', FolderKanban, false],
  ['counterexamples', '反例实验室', 'Counterexample Lab', Radar, false],
  ['experiments', '运行进程', 'Execution', FlaskConical, false],
  ['copilot', 'Argus Copilot', 'Argus Copilot', MessagesSquare, false],
  ['literature', '文献中心', 'Literature', BookOpen, true],
  ['inbox', '科研收信箱', 'Research inbox', Inbox, true],
  ['ide', 'AI IDE', 'AI IDE', Code2, false],
  ['paper', '论文工作区', 'Paper', FileText, true],
  ['reviewer', '模拟审稿', 'Reviewer', ShieldCheck, true],
  ['release', '成果发布', 'Release', Megaphone, true],
] as const satisfies ReadonlyArray<readonly [PageId, string, string, typeof FolderKanban, boolean]>;

export function ResearchWorkbenchPanel({ sid, active }: { sid: string; active: boolean }) {
  const { locale } = useI18n();
  const [page, setPage] = useState<PageId>(() => (
    new URLSearchParams(window.location.search).get('module') === 'counterexamples'
      ? 'counterexamples'
      : 'overview'
  ));
  const projectsQ = useProjects(active);
  const projects = projectsQ.data?.projects ?? [];
  const project = useMemo(() => projects.find((item) => item.id === sid) ?? null, [projects, sid]);
  const data = useArgusData(sid, active);
  const research = data.snapshot.data?.mission_view?.routing.vertical === 'research';
  const modules = useMemo(() => {
    const visible = research ? MODULES : MODULES.filter((module) => !module[4]);
    return data.counterexamples.data?.total
      ? visible
      : visible.filter(([id]) => id !== 'counterexamples');
  }, [data.counterexamples.data?.total, research]);
  const activePage = modules.some(([id]) => id === page) ? page : 'overview';
  const controlError = data.controls.start.error || data.controls.stop.error;
  const pageProps: WorkspacePageProps | null = project && data.snapshot.data ? {
    sid,
    project,
    snapshot: data.snapshot.data,
    status: data.status.data,
    events: data.events,
    transcript: data.transcript.data ?? [],
    artifacts: data.artifacts.data ?? [],
    counterexamples: data.counterexamples.data,
    gitDiff: data.gitDiff.data,
    journal: data.journal.data ?? [],
    connected: data.connected,
    snapshotUpdatedAt: data.snapshot.dataUpdatedAt,
    refresh: data.refresh,
    controls: {
      start: async () => { try { return await data.controls.start.mutateAsync(); } catch { return null; } },
      stop: async (drain) => { try { return await data.controls.stop.mutateAsync(drain); } catch { return null; } },
      busy: data.controls.start.isPending || data.controls.stop.isPending,
      error: controlError instanceof Error ? controlError.message : '',
    },
    navigate: setPage,
  } : null;

  const content = (() => {
    if (!pageProps) return null;
    if (activePage === 'overview') return <ProjectOverviewPage {...pageProps} />;
    if (activePage === 'counterexamples') return <CounterexamplePage {...pageProps} />;
    if (activePage === 'experiments') return <ExperimentsPage {...pageProps} />;
    if (activePage === 'copilot') return <CopilotPage {...pageProps} />;
    if (activePage === 'literature') return <LiteraturePage {...pageProps} />;
    if (activePage === 'inbox') return <InboxPage {...pageProps} />;
    if (activePage === 'ide') return <IdePage {...pageProps} />;
    if (activePage === 'paper') return <PaperPage {...pageProps} />;
    if (activePage === 'reviewer') return <ReviewerPage {...pageProps} />;
    return <ReleasePage {...pageProps} />;
  })();

  return (
    <section className="integrated-workbench flex min-h-0 flex-1 flex-col bg-transparent text-ink">
      <nav className="workbench-module-tabs shrink-0 border-b border-line/60 px-3 py-2" aria-label={locale === 'zh-CN' ? '工作台模块' : 'Workbench modules'}>
        <div className="flex flex-wrap gap-1">
          {modules.map(([id, zh, en, Icon]) => (
            <button
              key={id}
              type="button"
              className="workbench-module-tab"
              data-selected={activePage === id}
              onClick={() => setPage(id)}
            >
              <Icon size={14} />
              <span>{locale === 'zh-CN' ? zh : en}</span>
            </button>
          ))}
        </div>
      </nav>
      <div className="ros-content min-h-0 flex-1 overflow-x-hidden overflow-y-auto">
        {projectsQ.isError || data.snapshot.isError && !data.snapshot.data ? (
          <EmptyState title={locale === 'zh-CN' ? '工作台读取失败' : 'Workbench unavailable'} description="Argus API did not return the selected project." />
        ) : !pageProps ? (
          <div className="boot-state"><Spinner label={locale === 'zh-CN' ? '正在载入工作台' : 'Loading workbench'} /></div>
        ) : content}
      </div>
    </section>
  );
}
