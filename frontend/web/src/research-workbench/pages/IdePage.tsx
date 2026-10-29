import { errorText } from '../../lib/format';
import { useQuery } from '@tanstack/react-query';
import { Check, CheckCircle2, ChevronDown, ChevronRight, Code2, Download, File, FileCode2, Files, Folder, GitBranch, LockKeyhole, RefreshCw, Search, Server, TerminalSquare, UserRound, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { PdfPreview } from '../../components/PdfPreview';
import { Badge, EmptyState } from '../components/Common';
import { ReadonlyDiffView } from '../components/ReadonlyDiffView';
import { agentRoleColor } from '../../lib/agentRoles';
import { roleLabel } from '../enumLabels';
import { eventDetail, eventRole, eventTitle, formatClock } from '../utils';
import { buildFileTree, searchEntries, visibleEntries, workspaceApi, type FileTreeNode } from '../workspaceApi';
import { downloadBlob } from '../../lib/downloadBlob';
import { useWorkbenchText } from '../useWorkbenchText';
import { useWorkspaceBlobUrl } from '../useWorkspaceBlobUrl';
import { useWorkspaceProfile } from '../useWorkspaceProfile';
import type { ActiveWorkbenchPageProps } from './pageTypes';

function FileNode({ node, depth, selected, expanded, onToggle, onSelect }: { node: FileTreeNode; depth: number; selected: string; expanded: Set<string>; onToggle: (path: string) => void; onSelect: (path: string) => void }) {
  const { text } = useWorkbenchText();
  const directory = node.type === 'directory';
  const open = expanded.has(node.path);
  return <div className="workspace-node"><button type="button" className={selected === node.path ? 'is-selected' : ''} style={{ paddingLeft: 7 + depth * 13 }} onClick={() => directory ? onToggle(node.path) : onSelect(node.path)}>{directory ? open ? <ChevronDown size={13} /> : <ChevronRight size={13} /> : <span className="node-spacer" />}{directory ? <Folder size={14} /> : <FileCode2 size={14} />}<span>{node.name}</span>{node.skipped ? <small>{text('未展开', 'restricted')}</small> : null}</button>{directory && open ? node.children.map((child) => <FileNode key={child.path} node={child} depth={depth + 1} selected={selected} expanded={expanded} onToggle={onToggle} onSelect={onSelect} />) : null}</div>;
}

function WorkspacePreview({ sid, workspaceId, path, active }: { sid: string; workspaceId: string; path: string; active: boolean }) {
  const { text } = useWorkbenchText();
  const extension = path.toLowerCase().slice(path.lastIndexOf('.'));
  const media = ['.pdf', '.png', '.jpg', '.jpeg', '.webp', '.svg'].includes(extension);
  const file = useQuery({ queryKey: ['workspace-file', sid, workspaceId, path], queryFn: ({ signal }) => workspaceApi.file(sid, workspaceId, path, signal), enabled: Boolean(active && path && workspaceId && !media) });
  const blob = useWorkspaceBlobUrl(media ? sid : '', media ? workspaceId : '', media ? path : '');
  if (!path) return <EmptyState icon={File} title={text('打开一个文件开始阅读', 'Open a file to start reading')} description={text('左侧文件树展示所选代码工作区。', 'The file tree shows the selected code workspace.')} />;
  if (media) {
    if (blob.error) return <EmptyState icon={LockKeyhole} title={text('无法预览', 'Preview unavailable')} description={blob.error} />;
    if (!blob.url) return <div className="editor-loading">{text('正在载入预览…', 'Loading preview…')}</div>;
    return extension === '.pdf' ? <PdfPreview src={blob.url} name={path.split('/').at(-1) || path} className="workspace-pdf" /> : <img className="workspace-image" src={blob.url} alt={path} />;
  }
  if (file.isLoading) return <div className="editor-loading">{text('正在打开', 'Opening')} {path}…</div>;
  if (file.isError) return <EmptyState icon={LockKeyhole} title={text('无法预览这个文件', 'Preview unavailable')} description={`${errorText(file.error)} · ${text('可以用上方的“下载”按钮保存到本地查看。', 'Use Download above to open it locally.')}`} />;
  const lines = (file.data?.content ?? '').split('\n');
  return <div className="vscode-code" tabIndex={0} aria-label={text('文件内容', 'File contents')}><div className="vscode-line-numbers">{lines.map((_, index) => <span key={index}>{index + 1}</span>)}</div><pre><code>{file.data?.content}</code></pre></div>;
}

export function IdePage(props: ActiveWorkbenchPageProps) {
  const { locale, text } = useWorkbenchText();
  const workspace = useWorkspaceProfile(props.sid, 'ide', props.active);
  const workspaceId = workspace.workspaceId;
  const root = workspace.active?.path || '';
  const [selected, setSelected] = useState('');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const initializedTree = useRef('');
  const editorRef = useRef<HTMLElement>(null);
  const selectFile = (path: string) => {
    setSelected(path);
    const editor = editorRef.current;
    const shell = editor?.closest('.vscode-shell');
    const scroller = editor?.closest('.ros-content');
    // Compact workspaces stack the tree above the editor. A file click should
    // reveal its contents, not leave the new document below the fold.
    if (editor && shell && scroller && getComputedStyle(shell).display === 'flex') {
      scroller.scrollTo({
        top: scroller.scrollTop + editor.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 12,
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
      });
    }
  };
  const [activityView, setActivityView] = useState<'files' | 'git'>('files');
  const [query, setQuery] = useState('');
  const [showInternal, setShowInternal] = useState(false);
  const [downloadError, setDownloadError] = useState('');
  const [downloading, setDownloading] = useState(false);
  const downloadSelected = async () => {
    if (!selected) return;
    setDownloading(true);
    setDownloadError('');
    try {
      downloadBlob(await workspaceApi.downloadBlob(props.sid, workspaceId, selected), selected.split('/').at(-1) || 'download');
    } catch (error) {
      setDownloadError(errorText(error));
    } finally {
      setDownloading(false);
    }
  };
  const [sourceTab, setSourceTab] = useState<'changes' | 'timeline' | 'repository'>('repository');
  const tree = useQuery({ queryKey: ['workspace-tree', props.sid, workspaceId], queryFn: ({ signal }) => workspaceApi.tree(props.sid, workspaceId, signal), enabled: Boolean(props.active && workspaceId) });
  const git = useQuery({ queryKey: ['workspace-git', props.sid, workspaceId], queryFn: ({ signal }) => workspaceApi.git(props.sid, workspaceId, signal), enabled: Boolean(props.active && workspaceId) });
  const shownEntries = useMemo(() => visibleEntries(tree.data?.entries ?? [], showInternal), [tree.data?.entries, showInternal]);
  const nodes = useMemo(() => buildFileTree(shownEntries), [shownEntries]);
  const matches = useMemo(() => searchEntries(shownEntries, query), [shownEntries, query]);
  const hiddenCount = (tree.data?.entries.length ?? 0) - shownEntries.length;
  useEffect(() => { setSelected(''); setExpanded(new Set()); initializedTree.current = ''; }, [workspaceId]);
  useEffect(() => {
    if (!workspaceId || !nodes.length || initializedTree.current === workspaceId) return;
    initializedTree.current = workspaceId;
    setExpanded(new Set(nodes.filter((node) => node.type === 'directory').slice(0, 5).map((node) => node.path)));
  }, [nodes, workspaceId]);
  const commandEvents = props.events.filter((event) => ['command_execution', 'tool_use', 'tool_result', 'file_change'].includes(String(event.kind ?? ''))).slice(-100);
  const changed = (git.data?.status ?? '').split('\n').filter(Boolean);
  const commits = (git.data?.log ?? '').split('\n').filter(Boolean).map((line) => { const [hash, date, author, ...subject] = line.split('\t'); return { hash, date, author, subject: subject.join('\t') }; });
  const readiness = git.data;

  return (
    <div className="ros-page ide-v3">
      <header className="ros-page-header"><div><h1>{text('代码工作区', 'Code workspace')}</h1></div><Badge tone="info"><LockKeyhole size={12} />{text('此视图只读', 'This view is read-only')}</Badge></header>
      <div className="ide-context-strip"><Server size={15} /><select aria-label={text('选择已批准工作区', 'Select approved workspace')} value={workspaceId} onChange={(event) => workspace.setWorkspaceId(event.target.value)}>{workspace.profiles.data?.profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.label}</option>)}</select><code>{root}</code>{tree.isError ? <Badge tone="danger">{text('连接失败', 'Connection failed')}</Badge> : tree.isFetching ? <Badge tone="live" dot>{text('同步中', 'Syncing')}</Badge> : <Badge tone="success"><CheckCircle2 size={11} />{text('已同步', 'Synced')}</Badge>}<small>{shownEntries.length} {text('项', 'entries')}{hiddenCount > 0 ? text(`（另有 ${hiddenCount} 项内部文件已隐藏）`, ` (${hiddenCount} internal hidden)`) : ''}</small><button type="button" onClick={() => { void tree.refetch(); void git.refetch(); }} aria-label={text('刷新工作区', 'Refresh workspace')}><RefreshCw size={14} /></button></div>

      <div className="vscode-shell">
        <nav className="vscode-activitybar"><button type="button" className={activityView === 'files' ? 'is-active' : ''} onClick={() => setActivityView('files')} title={text('文件', 'Explorer')} aria-label={text('文件', 'Explorer')}><Files size={21} /></button><button type="button" className={activityView === 'git' ? 'is-active' : ''} onClick={() => setActivityView('git')} title={text('版本管理', 'Source Control')} aria-label={text('版本管理', 'Source Control')}><GitBranch size={21} />{changed.length ? <i>{changed.length}</i> : null}</button></nav>
        <aside className="vscode-sidebar"><header><span>{activityView === 'git' ? text('版本管理', 'SOURCE CONTROL') : text('文件', 'EXPLORER')}</span><button type="button" onClick={() => void tree.refetch()} aria-label={text('刷新文件树', 'Refresh file tree')}><RefreshCw size={14} /></button></header>{activityView === 'files' ? <><div className="workspace-search"><Search size={13} /><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={text('搜索文件名', 'Search file names')} aria-label={text('搜索文件名', 'Search file names')} /></div><label className="workspace-internal-toggle"><input type="checkbox" checked={showInternal} onChange={(event) => setShowInternal(event.target.checked)} />{text('显示内部文件（.argus 等）', 'Show internal files (.argus etc.)')}</label><div className="vscode-root"><ChevronDown size={13} /><strong title={root}>{root.split(/[\\/]/).filter(Boolean).at(-1) || root}</strong></div><div className="workspace-tree">{tree.isError ? <EmptyState icon={Server} title={text('目录连接失败', 'Directory connection failed')} description={errorText(tree.error)} /> : query.trim() ? (matches.length ? matches.map((entry) => <div className="workspace-node" key={entry.path}><button type="button" className={selected === entry.path ? 'is-selected' : ''} style={{ paddingLeft: 7 }} onClick={() => selectFile(entry.path)} title={entry.path}><span className="node-spacer" /><FileCode2 size={14} /><span>{entry.path}</span></button></div>) : <p className="workspace-search-empty">{text('没有匹配的文件', 'No matching files')}</p>) : nodes.map((node) => <FileNode key={node.path} node={node} depth={0} selected={selected} expanded={expanded} onToggle={(path) => setExpanded((current) => { const next = new Set(current); if (next.has(path)) next.delete(path); else next.add(path); return next; })} onSelect={selectFile} />)}</div></> : <div className="vscode-changes">{changed.length ? changed.map((line) => <button type="button" key={line} onClick={() => { const raw = line.slice(3).trim(); const path = raw.includes(' -> ') ? raw.split(' -> ').at(-1)! : raw; if (!path.endsWith('/')) selectFile(path); }}><b>{line.slice(0, 2).trim() || '?'}</b><span>{line.slice(3)}</span></button>) : <p>{text('没有改动', 'No changes')}</p>}</div>}</aside>
        <main className="vscode-editor" ref={editorRef}><div className="vscode-tabs"><button type="button" className="is-active"><Code2 size={13} />{selected || text('欢迎', 'Welcome')}</button>{selected ? <button type="button" className="workspace-download" disabled={downloading} onClick={() => void downloadSelected()} title={text('下载这个文件', 'Download this file')}><Download size={13} />{downloading ? text('正在下载…', 'Downloading…') : text('下载', 'Download')}</button> : null}{downloadError ? <span className="workspace-download-error" role="alert">{downloadError}</span> : null}</div><div className="vscode-breadcrumbs">{selected ? selected.split('/').map((part, index) => <span key={`${part}-${index}`}>{part}{index < selected.split('/').length - 1 ? <ChevronRight size={11} /> : null}</span>) : <span>{root}</span>}</div><div className="vscode-editor-surface"><WorkspacePreview key={`${workspaceId}:${selected}`} sid={props.sid} workspaceId={workspaceId} path={selected} active={props.active} /></div></main>
        <aside className="vscode-source-control"><header><div><span>{text('版本管理', 'SOURCE CONTROL')}</span><strong>{readiness?.branch || text('没有仓库', 'No repository')}</strong></div><Badge tone={changed.length ? 'warn' : 'success'}>{text(`${changed.length} 处改动`, `${changed.length} changes`)}</Badge></header><div className="vscode-sc-tabs"><button type="button" className={sourceTab === 'changes' ? 'is-active' : ''} onClick={() => setSourceTab('changes')}>{text('改动', 'Changes')}</button><button type="button" className={sourceTab === 'timeline' ? 'is-active' : ''} onClick={() => setSourceTab('timeline')}>{text('提交记录', 'Timeline')}</button><button type="button" className={sourceTab === 'repository' ? 'is-active' : ''} onClick={() => setSourceTab('repository')}>{text('仓库', 'Repository')}</button></div><div className="vscode-git-content">{!readiness?.available ? <EmptyState icon={GitBranch} title={text('这不是 Git 仓库', 'Not a Git repository')} /> : sourceTab === 'changes' ? <><pre className="vscode-status">{readiness.status || text('工作区没有改动', 'Working tree clean')}</pre><ReadonlyDiffView diff={readiness.diff} truncated={readiness.truncated} /></> : sourceTab === 'timeline' ? <div className="vscode-commits">{commits.map((commit) => <article key={commit.hash}><GitBranch size={13} /><div><strong>{commit.subject}</strong><small>{commit.author} · {commit.date?.slice(0, 10)}</small></div></article>)}</div> : <div className="repository-readiness"><h3>{text('仓库设置', 'Repository settings')}</h3><dl><div><dt><GitBranch size={13} />{text('远程仓库', 'Remote')}</dt><dd>{readiness.remotes.length ? readiness.remotes.map((remote) => `${remote.name}: ${remote.fetch}`).join('\n') : text('未设置', 'Not configured')}</dd><i className={readiness.remotes.length ? 'ok' : 'missing'}>{readiness.remotes.length ? <Check size={12} /> : <X size={12} />}</i></div><div><dt><GitBranch size={13} />{text('上游分支', 'Upstream')}</dt><dd>{readiness.upstream ? text(`${readiness.upstream} · 领先 ${readiness.ahead}，落后 ${readiness.behind}`, `${readiness.upstream} · ahead ${readiness.ahead}, behind ${readiness.behind}`) : text('未设置', 'Not configured')}</dd><i className={readiness.upstream ? 'ok' : 'missing'}>{readiness.upstream ? <Check size={12} /> : <X size={12} />}</i></div><div><dt><UserRound size={13} />{text('提交身份', 'Commit identity')}</dt><dd>{readiness.identity.name && readiness.identity.email ? `${readiness.identity.name} <${readiness.identity.email}>` : text('未设置', 'Not configured')}</dd><i className={readiness.identity.valid ? 'ok' : 'missing'}>{readiness.identity.valid ? <Check size={12} /> : <X size={12} />}</i></div></dl><p>{text('这里不显示任何凭据。', 'No credentials are shown here.')}</p></div>}</div></aside>
        <section className="vscode-terminal"><header><strong>{text('Argus 活动', 'ARGUS ACTIVITY')}</strong><span><TerminalSquare size={13} />{text('只读', 'read-only')}</span></header><div>{commandEvents.length ? commandEvents.map((event, index) => <article key={`${event.ts}-${index}`}><time>{formatClock(event.ts, locale)}</time><b className="terminal-role" style={{ color: agentRoleColor(eventRole(event)) }}>{roleLabel(eventRole(event), text)}</b><span>›</span><code>{eventDetail(event, 800) || eventTitle(event)}</code></article>) : <p>{text('$ 等待 Argus 活动', '$ waiting for Argus activity')}</p>}</div></section>
        <footer className="vscode-statusbar"><span><GitBranch size={12} />{readiness?.branch || text('没有分支', 'no branch')}</span><span>{tree.isError ? text('工作区出错', 'Workspace error') : tree.isFetching ? text('正在同步', 'Workspace syncing') : tree.data?.truncated ? text('文件太多，只显示了一部分', 'Tree truncated') : text('已同步', 'Workspace synced')}</span><span>UTF-8</span><span>{selected.split('.').at(-1)?.toUpperCase() || text('纯文本', 'Plain Text')}</span></footer>
      </div>
    </div>
  );
}
