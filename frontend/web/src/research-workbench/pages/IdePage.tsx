import { useQuery } from '@tanstack/react-query';
import { Check, CheckCircle2, ChevronDown, ChevronRight, Code2, File, FileCode2, Files, Folder, GitBranch, Github, LockKeyhole, RefreshCw, Server, TerminalSquare, UserRound, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Badge, EmptyState } from '../components/Common';
import { eventDetail, eventRole, eventTitle, formatClock } from '../utils';
import { buildFileTree, workspaceApi, type FileTreeNode } from '../workspaceApi';
import { useWorkbenchText } from '../useWorkbenchText';
import { useWorkspaceBlobUrl } from '../useWorkspaceBlobUrl';
import { useWorkspaceProfile } from '../useWorkspaceProfile';
import type { WorkspacePageProps } from './pageTypes';

function FileNode({ node, depth, selected, expanded, onToggle, onSelect }: { node: FileTreeNode; depth: number; selected: string; expanded: Set<string>; onToggle: (path: string) => void; onSelect: (path: string) => void }) {
  const directory = node.type === 'directory';
  const open = expanded.has(node.path);
  return <div className="workspace-node"><button type="button" className={selected === node.path ? 'is-selected' : ''} style={{ paddingLeft: 7 + depth * 13 }} onClick={() => directory ? onToggle(node.path) : onSelect(node.path)}>{directory ? open ? <ChevronDown size={13} /> : <ChevronRight size={13} /> : <span className="node-spacer" />}{directory ? <Folder size={14} /> : <FileCode2 size={14} />}<span>{node.name}</span>{node.skipped ? <small>restricted</small> : null}</button>{directory && open ? node.children.map((child) => <FileNode key={child.path} node={child} depth={depth + 1} selected={selected} expanded={expanded} onToggle={onToggle} onSelect={onSelect} />) : null}</div>;
}

function WorkspacePreview({ sid, workspaceId, path }: { sid: string; workspaceId: string; path: string }) {
  const { text } = useWorkbenchText();
  const extension = path.toLowerCase().slice(path.lastIndexOf('.'));
  const media = ['.pdf', '.png', '.jpg', '.jpeg', '.webp', '.svg'].includes(extension);
  const file = useQuery({ queryKey: ['workspace-file', sid, workspaceId, path], queryFn: ({ signal }) => workspaceApi.file(sid, workspaceId, path, signal), enabled: Boolean(path && workspaceId && !media), refetchInterval: 5_000 });
  const blob = useWorkspaceBlobUrl(media ? sid : '', media ? workspaceId : '', media ? path : '');
  if (!path) return <EmptyState icon={File} title={text('打开一个文件开始阅读', 'Open a file to start reading')} description={text('左侧文件树直接映射已批准的服务器工作区。', 'The file tree maps the approved server workspace.')} />;
  if (media) {
    if (blob.error) return <EmptyState icon={LockKeyhole} title="Preview unavailable" description={blob.error} />;
    if (!blob.url) return <div className="editor-loading">Loading preview…</div>;
    return extension === '.pdf' ? <embed className="workspace-pdf" src={blob.url} type="application/pdf" /> : <img className="workspace-image" src={blob.url} alt={path} />;
  }
  if (file.isLoading) return <div className="editor-loading">Opening {path}…</div>;
  if (file.isError) return <EmptyState icon={LockKeyhole} title="Preview unavailable" description={file.error.message} />;
  const lines = (file.data?.content ?? '').split('\n');
  return <div className="vscode-code"><div className="vscode-line-numbers">{lines.map((_, index) => <span key={index}>{index + 1}</span>)}</div><pre><code>{file.data?.content}</code></pre></div>;
}

export function IdePage(props: WorkspacePageProps) {
  const { locale, text } = useWorkbenchText();
  const workspace = useWorkspaceProfile(props.sid, 'ide');
  const workspaceId = workspace.workspaceId;
  const root = workspace.active?.path || '';
  const [selected, setSelected] = useState('');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [activityView, setActivityView] = useState<'files' | 'git'>('files');
  const [sourceTab, setSourceTab] = useState<'changes' | 'timeline' | 'repository'>('repository');
  const tree = useQuery({ queryKey: ['workspace-tree', props.sid, workspaceId], queryFn: ({ signal }) => workspaceApi.tree(props.sid, workspaceId, signal), enabled: Boolean(workspaceId), refetchInterval: 8_000 });
  const git = useQuery({ queryKey: ['workspace-git', props.sid, workspaceId], queryFn: ({ signal }) => workspaceApi.git(props.sid, workspaceId, signal), enabled: Boolean(workspaceId), refetchInterval: 8_000 });
  const nodes = useMemo(() => buildFileTree(tree.data?.entries ?? []), [tree.data?.entries]);
  useEffect(() => { setSelected(''); setExpanded(new Set()); }, [workspaceId]);
  useEffect(() => { if (nodes.length && expanded.size === 0) setExpanded(new Set(nodes.filter((node) => node.type === 'directory').slice(0, 5).map((node) => node.path))); }, [expanded.size, nodes]);
  const commandEvents = props.events.filter((event) => ['command_execution', 'tool_use', 'tool_result', 'file_change'].includes(String(event.kind ?? ''))).slice(-100);
  const changed = (git.data?.status ?? '').split('\n').filter(Boolean);
  const commits = (git.data?.log ?? '').split('\n').filter(Boolean).map((line) => { const [hash, date, author, ...subject] = line.split('\t'); return { hash, date, author, subject: subject.join('\t') }; });
  const readiness = git.data;

  return (
    <div className="ros-page ide-v3">
      <header className="ros-page-header"><div><div className="eyebrow">AI IDE</div><h1>{text('服务器代码工作区', 'Server code workspace')}</h1><p>{text('接近 VS Code 的只读工作台：文件浏览、源码阅读、Git/GitHub 就绪状态和 Argus 终端轨迹。', 'A read-only VS Code-style workspace for files, source, Git/GitHub readiness, and Argus terminal activity.')}</p></div><Badge tone="info"><LockKeyhole size={12} />{text('只读安全模式', 'Read-only safe mode')}</Badge></header>
      <div className="ide-context-strip"><Server size={15} /><select aria-label={text('选择已批准工作区', 'Select approved workspace')} value={workspaceId} onChange={(event) => workspace.setWorkspaceId(event.target.value)}>{workspace.profiles.data?.profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.label}</option>)}</select><code>{root}</code>{tree.isError ? <Badge tone="danger">{text('连接失败', 'Connection failed')}</Badge> : tree.isFetching ? <Badge tone="live" dot>{text('同步中', 'Syncing')}</Badge> : <Badge tone="success"><CheckCircle2 size={11} />Synced</Badge>}<small>{tree.data?.entries.length ?? 0} entries</small><button type="button" onClick={() => { void tree.refetch(); void git.refetch(); }} aria-label={text('刷新工作区', 'Refresh workspace')}><RefreshCw size={14} /></button></div>

      <div className="vscode-shell">
        <nav className="vscode-activitybar"><button type="button" className={activityView === 'files' ? 'is-active' : ''} onClick={() => setActivityView('files')} title="Explorer" aria-label="Explorer"><Files size={21} /></button><button type="button" className={activityView === 'git' ? 'is-active' : ''} onClick={() => setActivityView('git')} title="Source Control" aria-label="Source Control"><GitBranch size={21} />{changed.length ? <i>{changed.length}</i> : null}</button></nav>
        <aside className="vscode-sidebar"><header><span>{activityView === 'git' ? 'SOURCE CONTROL' : 'EXPLORER'}</span><button type="button" onClick={() => void tree.refetch()} aria-label={text('刷新文件树', 'Refresh file tree')}><RefreshCw size={14} /></button></header>{activityView === 'files' ? <><div className="vscode-root"><ChevronDown size={13} /><strong>{root.split('/').at(-1) || root}</strong></div><div className="workspace-tree">{tree.isError ? <EmptyState icon={Server} title={text('目录连接失败', 'Directory connection failed')} description={tree.error.message} /> : nodes.map((node) => <FileNode key={node.path} node={node} depth={0} selected={selected} expanded={expanded} onToggle={(path) => setExpanded((current) => { const next = new Set(current); if (next.has(path)) next.delete(path); else next.add(path); return next; })} onSelect={setSelected} />)}</div></> : <div className="vscode-changes">{changed.length ? changed.map((line) => <button type="button" key={line} onClick={() => { const raw = line.slice(3).trim(); const path = raw.includes(' -> ') ? raw.split(' -> ').at(-1)! : raw; if (!path.endsWith('/')) setSelected(path); }}><b>{line.slice(0, 2).trim() || '?'}</b><span>{line.slice(3)}</span></button>) : <p>No changes</p>}</div>}</aside>
        <main className="vscode-editor"><div className="vscode-tabs"><button type="button" className="is-active"><Code2 size={13} />{selected || 'Welcome'}</button></div><div className="vscode-breadcrumbs">{selected ? selected.split('/').map((part, index) => <span key={`${part}-${index}`}>{part}{index < selected.split('/').length - 1 ? <ChevronRight size={11} /> : null}</span>) : <span>{root}</span>}</div><div className="vscode-editor-surface"><WorkspacePreview sid={props.sid} workspaceId={workspaceId} path={selected} /></div></main>
        <aside className="vscode-source-control"><header><div><span>SOURCE CONTROL</span><strong>{readiness?.branch || 'No repository'}</strong></div><Badge tone={readiness?.publish_ready ? 'success' : 'warn'}>{readiness?.publish_ready ? 'Publish ready' : `${changed.length} changes`}</Badge></header><div className="vscode-sc-tabs"><button type="button" className={sourceTab === 'changes' ? 'is-active' : ''} onClick={() => setSourceTab('changes')}>Changes</button><button type="button" className={sourceTab === 'timeline' ? 'is-active' : ''} onClick={() => setSourceTab('timeline')}>Timeline</button><button type="button" className={sourceTab === 'repository' ? 'is-active' : ''} onClick={() => setSourceTab('repository')}>Repository</button></div><div className="vscode-git-content">{!readiness?.available ? <EmptyState icon={GitBranch} title="Not a Git repository" /> : sourceTab === 'changes' ? <><pre className="vscode-status">{readiness.status || 'Working tree clean'}</pre>{readiness.diff ? <pre className="vscode-diff">{readiness.diff}</pre> : null}</> : sourceTab === 'timeline' ? <div className="vscode-commits">{commits.map((commit) => <article key={commit.hash}><GitBranch size={13} /><div><strong>{commit.subject}</strong><small>{commit.author} · {commit.date?.slice(0, 10)}</small></div></article>)}</div> : <div className="repository-readiness"><h3>Repository readiness</h3><dl><div><dt><GitBranch size={13} />Remote</dt><dd>{readiness.remotes.length ? readiness.remotes.map((remote) => `${remote.name}: ${remote.fetch}`).join('\n') : 'Not configured'}</dd><i className={readiness.remotes.length ? 'ok' : 'missing'}>{readiness.remotes.length ? <Check size={12} /> : <X size={12} />}</i></div><div><dt><GitBranch size={13} />Upstream</dt><dd>{readiness.upstream || 'Not configured'}{readiness.upstream ? ` · ahead ${readiness.ahead}, behind ${readiness.behind}` : ''}</dd><i className={readiness.upstream ? 'ok' : 'missing'}>{readiness.upstream ? <Check size={12} /> : <X size={12} />}</i></div><div><dt><UserRound size={13} />Commit identity</dt><dd>{readiness.identity.name && readiness.identity.email ? `${readiness.identity.name} <${readiness.identity.email}>` : 'Not configured'}</dd><i className={readiness.identity.valid ? 'ok' : 'missing'}>{readiness.identity.valid ? <Check size={12} /> : <X size={12} />}</i></div><div><dt><Github size={13} />GitHub CLI</dt><dd>{readiness.github.authenticated ? `${readiness.github.login} · ${readiness.github.protocol}` : 'Not authenticated'}</dd><i className={readiness.github.authenticated ? 'ok' : 'missing'}>{readiness.github.authenticated ? <Check size={12} /> : <X size={12} />}</i></div></dl><p>{readiness.publish_ready ? 'Repository is ready for an explicitly approved push.' : 'Configure the missing items before publishing. No credentials are shown in this UI.'}</p></div>}</div></aside>
        <section className="vscode-terminal"><header><strong>ARGUS ACTIVITY</strong><span><TerminalSquare size={13} />read-only</span></header><div>{commandEvents.length ? commandEvents.map((event, index) => <article key={`${event.ts}-${index}`}><time>{formatClock(event.ts, locale)}</time><b className={`terminal-role terminal-role--${eventRole(event)}`}>{eventRole(event)}</b><span>›</span><code>{eventDetail(event, 800) || eventTitle(event)}</code></article>) : <p>$ waiting for Argus activity</p>}</div></section>
        <footer className="vscode-statusbar"><span><GitBranch size={12} />{readiness?.branch || 'no branch'}</span><span>{tree.isError ? 'Workspace error' : tree.isFetching ? 'Workspace syncing' : tree.data?.truncated ? 'Tree truncated' : 'Workspace synced'}</span><span>{readiness?.github.authenticated ? `GitHub: ${readiness.github.login}` : 'GitHub: offline'}</span><span>UTF-8</span><span>{selected.split('.').at(-1)?.toUpperCase() || 'Plain Text'}</span></footer>
      </div>
    </div>
  );
}
