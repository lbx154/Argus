import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { useInfiniteQuery, useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, ArrowLeft, ArrowRight, Check, Database, Download, ExternalLink, FileJson, History, Menu, Moon, RefreshCw, Search, ShieldCheck, Sun, Users, X } from 'lucide-react';
import { WorkspaceHeader, WorkspaceShell, WorkspaceSidePanel } from '../components/WorkspaceShell';
import { Wordmark } from '../components/Wordmark';
import { Button, Chip, RawDisclosure, Spinner, StatusDot } from '../components/primitives';
import { CopyButton } from '../components/CopyButton';
import { MarkdownContent } from '../components/MarkdownContent';
import { Modal } from '../components/Modal';
import { theme } from '../lib/theme';
import { useWorkbenchTheme } from '../useWorkbenchTheme';
import { adminAPI, AdminAPIError } from './api';
import { episodeRole, mergeObservationPages, normalizeRole, parseTaskLink, projectKey, projectName, taskName } from './model';
import type { CollaborationProject, ObservedEpisode, Purpose, RoleSummary } from './types';
import { ObservationViewer } from './ObservationViewer';
import { automaticProjectSelection, openMatchingUserProject } from './navigation';
import { dateLabel, roleName, stateLabel, useAdminText } from './copy';
import './admin-data.css';

const ROLES = ['manager', 'planner', 'engineer', 'reviewer'] as const;
const TENANTS = Array.from({ length: 11 }, (_, index) => `trial-${String(index + 1).padStart(2, '0')}`);
const retry = (count: number, error: Error) => count < 1 && !(error instanceof AdminAPIError && [401, 403].includes(error.status));

export function saveDownload(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = name; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

function useDebounced(value: string) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => { const timer = window.setTimeout(() => setDebounced(value), 250); return () => window.clearTimeout(timer); }, [value]);
  return debounced;
}

export default function AdminDataApp() {
  const { locale, text, t, setLocale } = useAdminText();
  const { themeMode, cycleTheme } = useWorkbenchTheme();
  const queryClient = useQueryClient();
  const [entry] = useState(() => parseTaskLink(window.location.search));
  const [respectEntry, setRespectEntry] = useState(!!entry.sid);
  const [tenant, setTenant] = useState(entry.tenant || '');
  const [search, setSearch] = useState('');
  const [purpose, setPurpose] = useState<Purpose>('internal_training');
  const [offset, setOffset] = useState(0);
  const [selectedKey, setSelectedKey] = useState(entry.tenant && entry.sid ? projectKey({ tenant_id: entry.tenant, sid: entry.sid }) : '');
  const [taskId, setTaskId] = useState(entry.taskId || '');
  const [activeRole, setActiveRole] = useState<string | null>(null);
  const [episodeId, setEpisodeId] = useState<number | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [modal, setModal] = useState<'export' | 'audit' | 'diagnostics' | 'workspace' | null>(null);
  const [selection, setSelection] = useState<Map<string, CollaborationProject>>(() => new Map());
  const [exportNotice, setExportNotice] = useState('');
  const [openingWorkspace, setOpeningWorkspace] = useState(false);
  const [workspaceAccount, setWorkspaceAccount] = useState<string | null>(null);
  const query = useDebounced(search);
  const identity = useQuery({ queryKey: ['admin-data', 'identity'], queryFn: ({ signal }) => adminAPI.identity({ signal }), staleTime: 30_000, retry: false });
  const authenticated = identity.data?.role === 'admin';
  const readonly = identity.data?.readonly !== false;
  // The server's query filters SIDs only. Name/task search uses all paginated
  // summaries and the authorized project directory, never a misleading SID query.
  const searchAllPages = !!query || respectEntry;
  const overview = useQuery({ queryKey: ['admin-data', 'overview', purpose, tenant, searchAllPages ? 'all-pages' : offset], queryFn: async ({ signal }) => {
    const first = await adminAPI.overview({ purpose, tenant, offset: searchAllPages ? 0 : offset, signal });
    const pages = [first], seen = new Set<number>([first.offset]);
    while (searchAllPages && pages[pages.length - 1].has_more_projects) {
      const next = pages[pages.length - 1].next_offset;
      if (next === null || seen.has(next)) throw new Error('Invalid project pagination');
      seen.add(next); pages.push(await adminAPI.overview({ purpose, tenant, offset: next, signal }));
    }
    return pages;
  }, enabled: authenticated, retry, refetchInterval: searchAllPages ? false : 20_000 });
  const overviewData = useMemo(() => {
    const pages = overview.data;
    if (!pages?.length) return undefined;
    const last = pages[pages.length - 1];
    return { ...pages[0], projects: pages.flatMap(page => page.projects), tasks: pages.flatMap(page => page.tasks),
      has_more_projects: last.has_more_projects, next_offset: last.next_offset,
      completeness: { ...pages[0].completeness, page_truncated: pages.some(page => page.completeness.page_truncated) } };
  }, [overview.data]);
  const collector = useQuery({ queryKey: ['admin-data', 'collector'], queryFn: ({ signal }) => adminAPI.collector({ signal }), enabled: authenticated, retry, refetchInterval: 15_000 });
  const audit = useQuery({ queryKey: ['admin-data', 'audit'], queryFn: ({ signal }) => adminAPI.audit({ signal }), enabled: authenticated && modal === 'audit', retry });
  const baseProjects = overviewData?.projects ?? [];
  const directoryTenants = useMemo(() => [...new Set(baseProjects.map(project => project.tenant_id))].sort(), [baseProjects]);
  const directories = useQueries({ queries: directoryTenants.map(account => ({ queryKey: ['admin-data', 'directory', account], queryFn: ({ signal }: { signal: AbortSignal }) => adminAPI.projectDirectory(account, { signal }), enabled: authenticated, staleTime: 60_000, retry })) });
  const displayNames = new Map<string, string>();
  directories.forEach((directory, index) => {
    for (const project of directory.data?.projects ?? []) {
      if (project.title) displayNames.set(projectKey({ tenant_id: directoryTenants[index], sid: project.id }), project.title);
    }
  });
  const displayName = (project: CollaborationProject) => displayNames.get(projectKey(project)) || projectName(project);
  const searchTerm = query.trim().toLocaleLowerCase(locale);
  const projects = baseProjects.filter(project => !searchTerm || [displayName(project), project.title, project.tenant_id, project.sid,
    ...(overviewData?.tasks ?? []).filter(task => projectKey(task) === projectKey(project)).flatMap(task => [taskName(task), task.task_id || '']),
  ].some(value => value.toLocaleLowerCase(locale).includes(searchTerm)));
  const currentProject = projects.find(project => projectKey(project) === selectedKey) ?? null;
  const tasks = (overviewData?.tasks ?? []).filter(task => currentProject && projectKey(task) === projectKey(currentProject));
  const actualTasks = tasks.filter(task => task.task_id);
  const currentTask = tasks.find(task => task.task_id === taskId) ?? null;

  useEffect(() => {
    if (!overviewData) return;
    const next = automaticProjectSelection(projects, selectedKey, entry, respectEntry);
    if (!next) return;
    setSelectedKey(next.key);
    setEpisodeId(null);
    setActiveRole(null);
    setTaskId(next.taskId);
  }, [overviewData, selectedKey, entry, respectEntry, query, directories.map(directory => directory.dataUpdatedAt).join(',')]);

  const scopeKey = [purpose, currentProject?.tenant_id, currentProject?.sid, taskId || null] as const;
  const detail = useQuery({ queryKey: ['admin-data', 'detail', ...scopeKey], queryFn: ({ signal }) => adminAPI.detail({ purpose, tenant: currentProject!.tenant_id, sid: currentProject!.sid, taskId: taskId || null, signal }), enabled: authenticated && !!currentProject?.eligible, retry });
  const resolvedTask = currentTask ?? (taskId && detail.data?.task_id === taskId ? detail.data : null);
  const scopeReady = !taskId || !!resolvedTask;
  const observations = useInfiniteQuery({ queryKey: ['admin-data', 'observations', ...scopeKey], queryFn: ({ pageParam, signal }) => adminAPI.observations({ purpose, tenant: currentProject!.tenant_id, sid: currentProject!.sid, taskId: taskId || null, cursor: pageParam, limit: 200, signal }), initialPageParam: null as string | null, getNextPageParam: page => page.pagination.has_more ? page.pagination.next_cursor : undefined, enabled: authenticated && !!currentProject?.eligible && scopeReady, retry });
  const episodes = useMemo(() => mergeObservationPages(observations.data?.pages ?? []), [observations.data]);
  const visibleEpisodes = episodes.filter(episode => activeRole === null || episodeRole(episode) === activeRole);
  const selectedEpisode = visibleEpisodes.find(episode => episode.episode_id === episodeId) ?? visibleEpisodes[0] ?? null;
  const roleSummaries = new Map<string, RoleSummary>();
  for (const task of taskId ? resolvedTask ? [resolvedTask] : [] : tasks) {
    for (const role of task.roles) {
      const key = normalizeRole(role.role), previous = roleSummaries.get(key);
      roleSummaries.set(key, { ...role, role: key, observations: (previous?.observations || 0) + role.observations, episodes: (previous?.episodes || 0) + role.episodes, tool_pairs: (previous?.tool_pairs || 0) + role.tool_pairs });
    }
  }
  const loadedEventCount = episodes.reduce((sum, episode) => sum + episode.events.length, 0);
  const selectedProjects = selection.size ? [...selection.values()] : currentProject ? [currentProject] : [];
  const exporter = useMutation({ mutationFn: () => {
    if (readonly || !selectedProjects.length || selectedProjects.length > 20) throw new Error(text('请选择最多 20 个可导出的项目。', 'Select up to 20 eligible projects.'));
    return adminAPI.exportObservations({ purpose, projects: selectedProjects });
  }, onSuccess: value => { saveDownload(value.blob, value.filename); setExportNotice(text('全部保留过程已下载。', 'All retained process data downloaded.')); void queryClient.invalidateQueries({ queryKey: ['admin-data', 'audit'] }); } });

  const updateLink = (project: CollaborationProject, nextTask = '') => {
    const url = new URL(window.location.href);
    url.searchParams.set('tenant', project.tenant_id); url.searchParams.set('sid', project.sid);
    if (nextTask) url.searchParams.set('task_id', nextTask); else url.searchParams.delete('task_id');
    url.searchParams.delete('task'); window.history.replaceState(window.history.state, '', url);
  };
  const chooseProject = (project: CollaborationProject) => {
    setRespectEntry(false); setSelectedKey(projectKey(project)); setTaskId(''); setActiveRole(null); setEpisodeId(null); setSidebarOpen(false); updateLink(project);
  };
  const changeScope = (value: string) => {
    setTaskId(value); setEpisodeId(null); setActiveRole(null); if (currentProject) updateLink(currentProject, value);
  };
  const toggleSelection = (project: CollaborationProject) => setSelection(previous => {
    const next = new Map(previous), key = projectKey(project);
    if (next.has(key)) next.delete(key); else if (next.size < 20) next.set(key, project);
    return next;
  });
  const refresh = () => { void queryClient.invalidateQueries({ queryKey: ['admin-data'] }); };
  const downloadJSON = (episode: ObservedEpisode) => {
    if (readonly) return;
    saveDownload(new Blob([JSON.stringify({ scope: 'loaded_observations', loaded_event_count: episode.events.length, episode }, null, 2)], { type: 'application/json' }), `${episode.tenant_id}-${episode.sid}-process-${episode.episode_id}.json`);
  };
  const openUserWorkspace = async () => {
    if (!currentProject || openingWorkspace) return;
    setOpeningWorkspace(true);
    try {
      const result = await openMatchingUserProject(currentProject, () => adminAPI.workspaceIdentity(), url => window.location.assign(url));
      setWorkspaceAccount(result.identity.role === 'trial' ? result.identity.key_id : null);
      if (!result.matched) setModal('workspace');
    } catch {
      setWorkspaceAccount(null);
      setModal('workspace');
    } finally {
      setOpeningWorkspace(false);
    }
  };
  const errorMessage = (error: unknown) => error instanceof AdminAPIError && error.status === 401
    ? text('数据后台登录已过期，请重新登录。', 'Your administrator session expired. Sign in again.')
    : error instanceof Error ? error.message : text('暂时无法读取数据。', 'Data is temporarily unavailable.');
  const roleDescription: Record<string, string> = {
    manager: text('理解目标、协调任务', 'Understands goals and coordinates work'), planner: text('拆解任务、调整计划', 'Breaks down work and revises plans'),
    engineer: text('调用工具、执行任务', 'Uses tools and carries out tasks'), reviewer: text('检查证据、审查结果', 'Checks evidence and reviews results'),
  };

  return <WorkspaceShell className="admin-data-workspace h-full" style={{ '--sidebar-width': '292px' } as CSSProperties}>
    {sidebarOpen ? <button type="button" className="admin-data-scrim lg:hidden" aria-label={text('关闭项目列表', 'Close projects')} onClick={() => setSidebarOpen(false)} /> : null}
    <WorkspaceSidePanel mobileOpen={sidebarOpen} className="admin-data-sidebar" aria-label={text('数据项目', 'Data projects')}>
      <div className="admin-data-sidebar-brand"><Wordmark size={25} /><Chip>{text('数据', 'Data')}</Chip><button type="button" className="inline-flex admin-data-icon-button ml-auto lg:hidden" onClick={() => setSidebarOpen(false)} aria-label={t('common.close')}><X size={18} /></button></div>
      <div className="admin-data-sidebar-heading"><span>{text('数据工作台', 'Data workbench')}</span><span>{overviewData?.total_projects ?? '—'}</span></div>
      <div className="admin-data-sidebar-controls">
        <label className="admin-data-field"><span>{text('账号', 'Account')}</span><select aria-label={text('筛选账号', 'Filter account')} value={tenant} onChange={event => { setRespectEntry(false); setTenant(event.target.value); setOffset(0); setTaskId(''); }}><option value="">{text('全部 11 个账号', 'All 11 accounts')}</option>{TENANTS.map(account => <option key={account}>{account}</option>)}</select></label>
        <label className="admin-data-search"><Search size={15} /><input aria-label={text('搜索项目、任务或 SID', 'Search projects, tasks or SID')} placeholder={text('搜索项目或任务…', 'Search projects or tasks…')} value={search} onChange={event => { setRespectEntry(false); setSearch(event.target.value); setOffset(0); }} /></label>
      </div>
      <div className="admin-data-projects" aria-busy={overview.isFetching}>
        {overview.isPending && authenticated ? <div className="admin-data-empty"><Spinner /><p>{text('正在读取项目…', 'Loading projects…')}</p></div> : null}
        {overview.error ? <div className="admin-data-notice" role="alert">{errorMessage(overview.error)}<Button className="inline-flex items-center justify-center gap-2" onClick={() => void overview.refetch()}>{t('common.retry')}</Button></div> : null}
        {!overview.isPending && !projects.length && !overview.error ? <div className="admin-data-empty"><Database size={22} /><p>{text('没有符合筛选条件的项目。', 'No projects match these filters.')}</p></div> : null}
        {projects.map(project => {
          const selected = projectKey(project) === selectedKey;
          const projectTasks = (overviewData?.tasks ?? []).filter(task => projectKey(task) === projectKey(project));
          const records = projectTasks.reduce((sum, task) => sum + task.collection.retained_episodes, 0);
          return <div key={projectKey(project)} data-testid={`project-${project.tenant_id}-${project.sid}`} className={`admin-data-project ${selected ? 'active' : ''}`}>
            <button type="button" className="admin-data-project-open" aria-current={selected ? 'page' : undefined} onClick={() => chooseProject(project)}><span className="admin-data-project-title">{displayName(project)}</span><span className="admin-data-project-id">{project.tenant_id} · {project.sid}</span><span className="admin-data-project-meta">{project.eligible ? records ? `${records.toLocaleString(locale)} ${text('段保留过程', 'retained processes')}` : text('尚无保留过程', 'No process data retained') : text('当前用途不可读取', 'Unavailable for this purpose')}</span></button>
            <input type="checkbox" className="admin-data-project-check" aria-label={`${text('选择导出项目', 'Select project for export')} ${displayName(project)} ${project.tenant_id} ${project.sid}`} checked={selection.has(projectKey(project))} disabled={readonly || !project.eligible || (selection.size >= 20 && !selection.has(projectKey(project)))} onChange={() => toggleSelection(project)} />
          </div>;
        })}
      </div>
      <div className="admin-data-paging"><Button className="inline-flex items-center justify-center gap-2" disabled={!offset || overview.isFetching} onClick={() => setOffset(Math.max(0, offset - 20))} title={text('上一页项目', 'Previous project page')}><ArrowLeft size={14} /></Button><span>{text('第', 'Page')} {Math.floor(offset / 20) + 1} {text('页', '')}</span><Button className="inline-flex items-center justify-center gap-2" disabled={!overviewData?.has_more_projects || overview.isFetching} onClick={() => setOffset(overviewData?.next_offset ?? offset + 20)} title={text('下一页项目', 'Next project page')}><ArrowRight size={14} /></Button></div>
      <div className="admin-data-sidebar-footer"><button type="button" onClick={() => setModal('audit')}><History size={15} />{text('导出记录', 'Export history')}</button><button type="button" onClick={() => setModal('diagnostics')}><Activity size={15} />{text('采集诊断', 'Capture diagnostics')}</button><a href="/invite"><ExternalLink size={15} />{text('用户工作空间', 'User workspace')}</a></div>
    </WorkspaceSidePanel>
    <main className="admin-data-surface">
      <WorkspaceHeader>
        <button type="button" className="inline-flex admin-data-icon-button lg:hidden" onClick={() => setSidebarOpen(true)} aria-label={text('打开项目列表', 'Open projects')}><Menu size={18} /></button>
        <span className="admin-data-header-title">{text('数据工作台', 'Data workbench')}</span><Chip>{readonly ? text('只读管理员', 'Read-only administrator') : text('管理员', 'Administrator')}</Chip>
        <div className="ml-auto flex items-center gap-1 sm:gap-2"><button type="button" className="inline-flex admin-data-icon-button" onClick={() => setLocale(locale === 'zh-CN' ? 'en' : 'zh-CN')} aria-label={t('language.switchTo', { language: locale === 'zh-CN' ? 'English' : '中文' })}>{locale === 'zh-CN' ? 'EN' : '中'}</button><button type="button" className="inline-flex admin-data-icon-button" onClick={cycleTheme} aria-label={text('切换深浅色', 'Toggle light and dark theme')}>{themeMode === 'dark' ? <Sun size={16} /> : <Moon size={16} />}</button><Button className="inline-flex items-center justify-center gap-2" onClick={refresh} disabled={overview.isFetching || !authenticated} title={text('刷新数据', 'Refresh data')}>{overview.isFetching ? <Spinner /> : <RefreshCw size={14} />}</Button><Button className="inline-flex items-center justify-center gap-2" variant="primary" disabled={readonly || !currentProject?.eligible || exporter.isPending} onClick={() => { setExportNotice(''); setModal('export'); }}><Download size={14} /><span className="hidden sm:inline">{text('导出', 'Export')}</span></Button></div>
      </WorkspaceHeader>
      <div className="admin-data-content">
        {!authenticated ? <div className="admin-data-welcome"><Wordmark size={38} /><h1>{text('数据工作台', 'Data workbench')}</h1><p>{identity.error ? errorMessage(identity.error) : text('正在验证管理员身份…', 'Checking administrator access…')}</p>{identity.error ? <a className="brand-button brand-button-primary" href="/admin/login">{text('管理员登录', 'Administrator sign in')}</a> : <Spinner />}</div>
          : currentProject ? <>
            <div className="admin-data-project-heading"><div className="min-w-0"><p className="admin-data-eyebrow"><Database size={13} />{text('已授权的过程数据', 'Authorized process data')}</p><h1>{displayName(currentProject)}</h1><div className="admin-data-project-identity"><code>{currentProject.tenant_id} / {currentProject.sid}</code><Button className="inline-flex items-center justify-center gap-2" disabled={openingWorkspace} onClick={() => void openUserWorkspace()}>{openingWorkspace ? <Spinner /> : <ExternalLink size={13} />}{text('在用户端打开', 'Open in user workspace')}</Button><CopyButton text={`${window.location.origin}/admin/data?tenant=${encodeURIComponent(currentProject.tenant_id)}&sid=${encodeURIComponent(currentProject.sid)}`} label={text('复制后台链接', 'Copy data link')} copiedLabel={text('已复制', 'Copied')} /></div></div><span className="admin-data-collector-status"><StatusDot ok={collector.data?.state === 'running'} />{collector.error ? text('采集状态暂不可读', 'Capture status unavailable') : stateLabel(collector.data?.state, locale)}</span></div>
            <div className="admin-data-context-bar"><label className="admin-data-field flex-1"><span>{text('查看范围', 'View scope')}</span><select value={taskId} onChange={event => changeScope(event.target.value)} aria-label={text('选择任务或整个项目', 'Select a task or the entire project')}><option value="">{text('整个项目 · 包含任务创建前的过程', 'Entire project · includes pre-task processes')}</option>{taskId && !actualTasks.some(task => task.task_id === taskId) ? <option value={taskId}>{resolvedTask ? taskName(resolvedTask) : text('链接中的任务', 'Linked task')} · {taskId}</option> : null}{actualTasks.map(task => <option key={task.task_id} value={task.task_id!}>{taskName(task)} · {task.task_id}</option>)}</select></label><label className="admin-data-field"><span>{text('数据用途', 'Data purpose')}</span><select value={purpose} onChange={event => { setPurpose(event.target.value as Purpose); setSelection(new Map()); setOffset(0); }}><option value="internal_training">{text('内部训练', 'Internal training')}</option><option value="external_sharing">{text('对外共享', 'External sharing')}</option></select></label></div>
            {!currentProject.eligible ? <div className="admin-data-notice" role="status">{text('当前用途下没有可读取的授权记录。请选择其他项目或数据用途。', 'No authorized records are available for this purpose. Choose another project or purpose.')}<RawDisclosure><pre>{currentProject.reason}</pre></RawDisclosure></div> : <>
              {taskId && resolvedTask ? <RawDisclosure label={text('任务目标与来源', 'Task objective and source')}><div className="admin-data-prose"><MarkdownContent>{resolvedTask.objective || resolvedTask.request?.text || resolvedTask.title}</MarkdownContent></div><code>{resolvedTask.task_id}</code></RawDisclosure> : null}
              {scopeReady ? <><div className="admin-data-role-heading"><div><h2><Users size={17} />{text('多 Agent 过程', 'Multi-agent processes')}</h2><p>{text('查看各角色实际留下的记录。角色缺失、中断和质量验收分别标记。', 'Inspect the records each role actually produced. Missing roles, interruptions and review status are shown separately.')}</p></div><button type="button" className={`admin-data-text-button ${activeRole === null ? 'active' : ''}`} onClick={() => { setActiveRole(null); setEpisodeId(null); }}>{text('全部角色', 'All roles')}</button></div>
              <div className="admin-data-roles">{ROLES.map(role => {
                const summary = roleSummaries.get(role), retained = Math.max(summary?.episodes || 0, episodes.filter(episode => episodeRole(episode) === role).length);
                const loaded = episodes.filter(episode => episodeRole(episode) === role).length;
                return <button type="button" key={role} data-testid={`role-${role}`} className={`admin-data-role ${activeRole === role ? 'active' : ''}`} style={{ '--role-color': theme.role[role] } as CSSProperties} aria-pressed={activeRole === role} onClick={() => { setActiveRole(role); setEpisodeId(null); }}><span className="admin-data-role-label"><span className="admin-data-role-mark">{roleName(role, locale).slice(0, 1)}</span><strong>{roleName(role, locale)}</strong><span>{role}</span></span><span className="admin-data-role-description">{roleDescription[role]}</span><span className="admin-data-role-count">{retained ? `${retained.toLocaleString(locale)} ${text('段过程', 'processes')}` : summary?.observations ? text('仅有活动记录', 'Activity records only') : text('尚未采到记录', 'No records captured')}</span><span className="admin-data-role-footnote">{loaded ? `${text('正文已加载', 'Content loaded')} ${loaded.toLocaleString(locale)}` : text('按原始记录归属角色', 'Role comes from recorded metadata')}</span></button>;
              })}</div>
              {(roleSummaries.get('unknown')?.episodes || episodes.some(episode => episodeRole(episode) === 'unknown')) ? <button type="button" className="admin-data-unknown" onClick={() => { setActiveRole('unknown'); setEpisodeId(null); }}><FileJson size={14} />{text('另有角色未记录的过程，保留原样查看', 'Some processes have no recorded role. View them as captured.')} <ArrowRight size={13} /></button> : null}
              <div className="admin-data-record-heading"><div><h2>{activeRole ? roleName(activeRole, locale) : text('全部保留过程', 'All retained processes')}</h2><p>{episodes.length.toLocaleString(locale)} {text('段已加载', 'processes loaded')} · {loadedEventCount.toLocaleString(locale)} {text('条原始事件', 'raw events')} {observations.hasNextPage ? text('· 还有后续页', '· more pages available') : ''}</p></div>{visibleEpisodes.length ? <select className="admin-data-episode-select" aria-label={text('选择过程记录', 'Select a process')} value={selectedEpisode?.episode_id ?? ''} onChange={event => setEpisodeId(Number(event.target.value))}>{visibleEpisodes.map(episode => <option key={episode.episode_id} data-testid={`episode-${episode.episode_id}`} value={episode.episode_id}>#{episode.episode_id} · {roleName(episode.role, locale)} · {dateLabel(episode.started_at, locale)}</option>)}</select> : null}</div>
              {observations.error ? <div role="alert" className="admin-data-notice">{errorMessage(observations.error)}<Button className="inline-flex items-center justify-center gap-2" onClick={() => void observations.refetch()}>{t('common.retry')}</Button></div> : null}
              {selectedEpisode ? <ObservationViewer key={selectedEpisode.episode_id} episode={selectedEpisode} readonly={readonly} onDownload={downloadJSON} /> : <div className="admin-data-empty admin-data-record-empty">{observations.isPending ? <Spinner /> : <FileJson size={28} />}<p>{observations.isPending ? text('正在读取真实过程…', 'Loading recorded processes…') : activeRole && (roleSummaries.get(activeRole)?.episodes || 0) > 0 ? text('该角色有记录，当前页尚未加载其正文。', 'This role has recorded processes; their content is on a later page.') : text('当前范围没有保留的模型过程。项目活动不等同于完整模型轨迹。', 'No model processes are retained in this scope. Project activity does not establish a complete model trace.')}</p></div>}
              {observations.hasNextPage ? <div className="admin-data-load-more" data-testid="load-more-observations"><Button className="inline-flex items-center justify-center gap-2" disabled={observations.isFetchingNextPage} onClick={() => void observations.fetchNextPage()}>{observations.isFetchingNextPage ? <Spinner /> : <ArrowRight size={14} />}{text('加载后续原始记录', 'Load more original records')}</Button></div> : null}
              {detail.data?.segments.some(segment => segment.source_kind !== 'tool_episode') ? <RawDisclosure label={text('项目活动与任务来源', 'Project activity and task sources')}><div className="admin-data-activity">{detail.data.segments.filter(segment => segment.source_kind !== 'tool_episode').map(segment => <div key={segment.id}><span>{roleName(segment.role, locale)}</span><span>{segment.summary}</span><time>{dateLabel(segment.started_at, locale)}</time></div>)}</div><p className="admin-data-subtle">{text('活动顺序来自记录时间，不表示已证实的 Agent 交接。', 'Activity follows recorded timestamps; it does not establish an agent handoff.')}</p></RawDisclosure> : null}
              <p className="admin-data-bottom-note"><ShieldCheck size={14} />{text('过程已采集不代表任务成功，也不代表训练质量已验收。原始记录保留缺失和中断状态。', 'Captured processes do not establish task success or training quality. Missing data and interruptions remain visible.')}</p></> : <div className="admin-data-notice" role="status">{detail.isPending ? <><Spinner />{text('正在核对链接中的任务…', 'Checking the linked task…')}</> : <><span>{detail.error instanceof AdminAPIError && detail.error.status === 404 ? text('链接中的任务 ID 不存在或当前用途下不可读取。这不表示项目没有过程数据。', 'The linked task ID does not exist or is unavailable for this purpose. This does not mean the project has no process data.') : text('暂时无法核对该任务，请重试或查看整个项目。', 'This task could not be verified. Retry or view the entire project.')} <code>{taskId}</code></span><Button className="inline-flex items-center justify-center gap-2" onClick={() => changeScope('')}>{text('查看整个项目', 'View entire project')}</Button><Button className="inline-flex items-center justify-center gap-2" onClick={() => void detail.refetch()}>{t('common.retry')}</Button></>}</div>}
            </>}
          </> : <div className="admin-data-welcome"><Database size={32} /><h1>{respectEntry && entry.sid ? text('链接中的项目未找到', 'Linked project not found') : text('选择一个项目', 'Select a project')}</h1><p>{respectEntry && entry.sid ? text('当前账号与用途下找不到此 SID，没有替你打开其他项目。请检查链接或选择左侧项目。', 'This SID is not available for the selected account and purpose. Check the link or choose a project from the list.') : text('从左侧查看各账号的真实过程数据。', 'Choose a project to inspect its recorded processes.')}</p><Button className="inline-flex items-center justify-center gap-2" onClick={() => setSidebarOpen(true)}>{text('浏览项目', 'Browse projects')}</Button></div>}
      </div>
    </main>
    <Modal open={modal === 'workspace'} onClose={() => setModal(null)} label={text('打开用户工作空间', 'Open user workspace')}><div className="admin-data-modal-content"><h2>{text('需要对应的用户账号', 'Use the matching user account')}</h2><p>{text('这个项目属于', 'This project belongs to')} <strong>{currentProject?.tenant_id}</strong>{text('。', '. ')}{workspaceAccount ? <>{text('当前用户端账号是', 'Your current user account is')} <strong>{workspaceAccount}</strong>{text('。', '. ')}</> : text('你尚未登录用户工作空间。', 'You are not signed in to a user workspace.')}</p><p>{text('请在用户端使用对应邀请码登录，再打开这个项目。管理员登录不会切换用户账号。', 'Sign in to the user workspace with the matching invitation before opening this project. Administrator access does not switch your user account.')}</p><div className="admin-data-modal-actions"><Button className="inline-flex items-center justify-center gap-2" onClick={() => setModal(null)}>{t('common.cancel')}</Button><a className="brand-button brand-button-primary inline-flex items-center gap-2" href="/invite" target="_blank" rel="noopener">{text('打开用户登录入口', 'Open user sign in')}<ExternalLink size={14} /></a></div></div></Modal>
    <Modal open={modal === 'export'} onClose={() => setModal(null)} label={text('导出全部保留过程', 'Export all retained processes')}>
      <div className="admin-data-modal-content"><h2>{text('导出全部保留过程', 'Export all retained processes')}</h2><p>{text('ZIP 包含所选项目的全部已保留输入、输出和工具轨迹，包括未验收、未结束和失败的记录。', 'The ZIP includes all retained inputs, outputs and tool traces for these projects, including unreviewed, unfinished and failed records.')}</p><ul className="admin-data-export-projects">{selectedProjects.map(project => <li key={projectKey(project)}><Check size={14} /><div><strong>{displayName(project)}</strong><code>{project.tenant_id} / {project.sid}</code></div></li>)}</ul><p className="admin-data-subtle">{text('可在项目列表勾选多个项目；一次最多 20 个。', 'Use project checkboxes to select up to 20 projects per export.')}</p>{readonly ? <p role="status">{text('只读管理员不能导出数据。', 'Read-only administrators cannot export data.')}</p> : null}{exporter.error ? <p role="alert" className="admin-data-notice">{errorMessage(exporter.error)}</p> : null}{exportNotice ? <p role="status">{exportNotice}</p> : null}<div className="admin-data-modal-actions"><Button className="inline-flex items-center justify-center gap-2" onClick={() => setModal(null)}>{t('common.cancel')}</Button><Button className="inline-flex items-center justify-center gap-2" variant="primary" disabled={readonly || !selectedProjects.length || exporter.isPending} onClick={() => exporter.mutate()}>{exporter.isPending ? <Spinner /> : <Download size={14} />}{text('下载 ZIP', 'Download ZIP')}</Button></div></div>
    </Modal>
    <Modal open={modal === 'audit'} onClose={() => setModal(null)} label={text('导出记录', 'Export history')} width="max-w-3xl"><div className="admin-data-modal-content"><h2>{text('导出记录', 'Export history')}</h2><p>{text('服务器记录的导出范围、操作身份与结果。', 'Server-recorded export scope, operator and outcome.')}</p>{audit.isPending ? <Spinner /> : audit.error ? <p role="alert">{errorMessage(audit.error)}</p> : audit.data?.events.length ? <div className="admin-data-table-wrap"><table><thead><tr><th>{text('时间', 'Time')}</th><th>{text('操作', 'Action')}</th><th>{text('身份 / 用途', 'Actor / purpose')}</th><th>{text('结果', 'Outcome')}</th></tr></thead><tbody>{audit.data.events.map(event => <tr key={event.id}><td>{dateLabel(event.created_at, locale)}</td><td>{event.action}<small>{event.projects ?? '—'} {text('个项目', 'projects')}</small></td><td>{event.actor}<small>{event.purpose === 'external_sharing' ? text('对外共享', 'External sharing') : text('内部训练', 'Internal training')}</small></td><td>{event.outcome}</td></tr>)}</tbody></table></div> : <p>{text('尚无导出记录。', 'No export history yet.')}</p>}</div></Modal>
    <Modal open={modal === 'diagnostics'} onClose={() => setModal(null)} label={text('采集诊断', 'Capture diagnostics')} width="max-w-3xl"><div className="admin-data-modal-content"><h2>{text('采集诊断', 'Capture diagnostics')}</h2><p>{collector.error ? errorMessage(collector.error) : stateLabel(collector.data?.state, locale)}</p><div className="admin-data-diagnostics">{Object.entries(collector.data?.tool_capture?.tenants ?? {}).map(([account, capture]) => <div key={account}><strong>{account}</strong><span>{stateLabel(capture.state, locale)}</span><span>{capture.counts.events_received ?? 0} {text('条接收事件', 'events received')}</span>{capture.last_error_code ? <code>{capture.last_error_code}</code> : null}</div>)}</div><RawDisclosure label={text('完整诊断数据', 'Full diagnostic data')}><pre className="admin-data-code">{JSON.stringify({ collector: collector.data, project: currentProject, collection: detail.data?.collection, gaps: detail.data?.gaps, completeness: overviewData?.completeness }, null, 2)}</pre></RawDisclosure></div></Modal>
  </WorkspaceShell>;
}
