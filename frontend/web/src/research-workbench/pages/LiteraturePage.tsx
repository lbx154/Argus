import { useQuery } from '@tanstack/react-query';
import { BookOpen, Calendar, ExternalLink, FileJson, FileSearch, FolderSearch, Search, Sparkles } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Badge, EmptyState, Markdown } from '../components/Common';
import { eventDetail, eventTitle, formatClock, formatDate } from '../utils';
import { workspaceApi, type LiteraturePaper } from '../workspaceApi';
import { useManagerRun } from '../useManagerRun';
import { useWorkbenchText } from '../useWorkbenchText';
import { useWorkspaceProfile } from '../useWorkspaceProfile';
import type { WorkspacePageProps } from './pageTypes';

type LiteratureTab = 'all' | 'recent' | 'read' | 'sources';

function PaperCard({ paper, selected, onClick }: { paper: LiteraturePaper; selected: boolean; onClick: () => void }) {
  const { text } = useWorkbenchText();
  return (
    <button type="button" className={`paper-card ${selected ? 'is-selected' : ''}`} onClick={onClick}>
      <div className="paper-card__meta"><Badge tone={paper.evidenceStatus === 'verified_artifact' ? 'success' : paper.evidenceStatus === 'metadata' ? 'info' : 'warn'}>{paper.evidenceStatus === 'verified_artifact' ? text('原文文件已验证', 'Source verified') : paper.evidenceStatus === 'metadata' ? text('仅元数据', 'Metadata only') : text('待核验', 'Needs verification')}</Badge><span className="paper-card__year">{paper.year || '—'}{paper.venue ? ` · ${paper.venue}` : ''}</span></div>
      <h3>{paper.title}</h3>
      {paper.authors.length ? <p className="paper-card__authors">{paper.authors.slice(0, 4).join(', ')}{paper.authors.length > 4 ? ' et al.' : ''}</p> : null}
      <p className="paper-card__summary">{paper.relevance || paper.abstract || text('该记录尚未写入项目相关性摘要。', 'No project-relevance summary has been recorded.')}</p>
      <div className="paper-card__footer"><code>{paper.sourcePath}</code><span>{text('查看详情', 'View details')}</span></div>
    </button>
  );
}

export function LiteraturePage(props: WorkspacePageProps) {
  const { locale, text } = useWorkbenchText();
  const workspace = useWorkspaceProfile(props.sid, 'literature');
  const root = workspace.active?.path || '';
  const index = useQuery({ queryKey: ['workspace-literature', props.sid, workspace.workspaceId], queryFn: ({ signal }) => workspaceApi.literature(props.sid, workspace.workspaceId, signal), enabled: Boolean(workspace.workspaceId), refetchInterval: 15_000 });
  const [tab, setTab] = useState<LiteratureTab>('all');
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [researchPrompt, setResearchPrompt] = useState('');
  const manager = useManagerRun(props.sid, async () => { await props.refresh(); await index.refetch(); });
  const papers = index.data?.papers ?? [];
  const newestYear = Math.max(0, ...papers.map((paper) => paper.year ?? 0));
  const filtered = useMemo(() => papers.filter((paper) => {
    if (tab === 'recent' && (paper.year ?? 0) < newestYear - 1) return false;
    if (tab === 'read' && paper.evidenceStatus !== 'verified_artifact') return false;
    const needle = query.trim().toLowerCase();
    return !needle || `${paper.title} ${paper.authors.join(' ')} ${paper.venue} ${paper.relevance} ${paper.topics.join(' ')}`.toLowerCase().includes(needle);
  }), [newestYear, papers, query, tab]);
  const selected = papers.find((paper) => paper.id === selectedId) ?? filtered[0] ?? null;
  const retrievalEvents = useMemo(() => props.events.filter((event) => /paper|arxiv|doi|literature|search|citation|http/i.test(`${event.type} ${event.kind} ${eventDetail(event, 2_000)}`)).slice(-30).reverse(), [props.events]);
  const askArgus = async () => {
    if (!researchPrompt.trim()) return;
    await manager.run(`请为当前项目执行新的文献调研：${researchPrompt}\n\n要求读取原始论文或官方仓库，把结构化记录追加到项目的 literature grounding/audit 文件中，包括标题、作者、年份、URL、与当前项目关系、最近工作威胁和仍待全文核验项。完成后文献中心应能从工作目录直接读取这些记录。`);
  };

  return (
    <div className="ros-page literature-v2">
      <header className="ros-page-header"><div><div className="eyebrow">LITERATURE CENTER</div><h1>{text('文献中心', 'Literature center')}</h1><p>{text('直接读取 Argus 工作目录中的论文清单、文献审计和实时检索轨迹，不再依赖手工注册 artifacts。', 'Read paper inventories, literature audits, and live retrieval traces directly from the Argus workdir.')}</p></div><div className="header-badges"><Badge tone="success"><BookOpen size={12} />{papers.length} {text('篇论文', 'papers')}</Badge><Badge tone="neutral">{index.data?.sourceFiles.length ?? 0} {text('个证据文件', 'evidence files')}</Badge></div></header>

      <section className="literature-stats">
        <div><span className="stat-icon stat-icon--blue"><BookOpen size={18} /></span><p>{text('论文记录', 'Paper records')}<strong>{papers.length}</strong></p></div>
        <div><span className="stat-icon stat-icon--green"><FileSearch size={18} /></span><p>{text('原文文件已验证', 'Verified sources')}<strong>{papers.filter((paper) => paper.evidenceStatus === 'verified_artifact').length}</strong></p></div>
        <div><span className="stat-icon stat-icon--amber"><Calendar size={18} /></span><p>{text('最近工作', 'Recent work')}<strong>{papers.filter((paper) => (paper.year ?? 0) >= newestYear - 1).length}</strong></p></div>
        <div><span className="stat-icon stat-icon--violet"><FolderSearch size={18} /></span><p>{text('扫描项目文件', 'Scanned files')}<strong>{index.data?.scannedFiles ?? 0}</strong></p></div>
      </section>

      <div className="literature-v2__layout">
        <aside className="literature-v2__sidebar ros-card">
          <header><div><span>LIBRARY</span><h2>{text('项目文献库', 'Project library')}</h2></div></header>
          <label className="search-field search-field--block"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={text('搜索标题、作者、主题', 'Search title, author, or topic')} /></label>
          <nav className="library-tabs">
            {([['all', text('全部论文', 'All papers'), papers.length], ['recent', text('最近工作', 'Recent work'), papers.filter((paper) => (paper.year ?? 0) >= newestYear - 1).length], ['read', text('已验证原文', 'Verified sources'), papers.filter((paper) => paper.evidenceStatus === 'verified_artifact').length], ['sources', text('证据文件', 'Evidence files'), index.data?.sourceFiles.length ?? 0]] as const).map(([id, label, count]) => <button type="button" className={tab === id ? 'is-active' : ''} key={id} onClick={() => setTab(id)}><span>{label}</span><small>{count}</small></button>)}
          </nav>
          <div className="literature-source-note"><FileJson size={15} /><div><strong>{text('实时来源', 'Live source')}</strong><p title={root}>{root}</p></div></div>
        </aside>

        <main className="literature-v2__main">
          <div className="literature-list-header"><div><h2>{tab === 'recent' ? text('最近工作', 'Recent work') : tab === 'read' ? text('已验证原文文件', 'Verified source files') : tab === 'sources' ? text('文献证据文件', 'Literature evidence files') : text('全部论文', 'All papers')}</h2><p>{tab === 'recent' ? text(`按项目中最新年份 ${newestYear || '—'} 自动筛选`, `Filtered by the latest project year: ${newestYear || '—'}`) : text('Argus 写入工作目录后约 5 秒内自动更新', 'Updates shortly after Argus writes to the workdir')}</p></div>{index.isError ? <Badge tone="danger">{text('同步失败', 'Sync failed')}</Badge> : index.isFetching ? <Badge tone="live" dot>{text('同步中', 'Syncing')}</Badge> : <Badge tone="success">{text('已同步', 'Synced')}</Badge>}</div>
          {index.isError ? <div className="inline-error">{index.error.message}</div> : null}
          {tab === 'sources' ? (
            <div className="source-file-grid">{index.data?.sourceFiles.map((file) => <article key={file.path}><FileJson size={17} /><div><strong>{file.name}</strong><code>{file.path}</code></div><time>{formatDate(file.mtime, locale)}</time></article>)}</div>
          ) : filtered.length ? <div className="paper-grid">{filtered.map((paper) => <PaperCard key={paper.id} paper={paper} selected={selected?.id === paper.id} onClick={() => setSelectedId(paper.id)} />)}</div> : <EmptyState icon={BookOpen} title={text('此筛选下暂无论文', 'No papers match this filter')} description={text('Argus 完成检索并写入 LITERATURE_GROUNDING.json 后会自动出现。', 'Papers appear after Argus writes LITERATURE_GROUNDING.json.')} />}
        </main>

        <aside className="literature-v2__detail">
          <section className="ros-card paper-detail">
            {selected ? <><div className="paper-detail__top"><Badge tone={selected.evidenceStatus === 'verified_artifact' ? 'success' : selected.evidenceStatus === 'metadata' ? 'info' : 'warn'}>{selected.evidenceStatus === 'verified_artifact' ? 'verified artifact' : selected.evidenceStatus}</Badge><span>{selected.year || '—'}{selected.venue ? ` · ${selected.venue}` : ''}</span></div><h2>{selected.title}</h2>{selected.authors.length ? <p className="paper-detail__authors">{selected.authors.join(', ')}</p> : null}<div className="paper-detail__body"><h3>{text('与当前项目的关系', 'Relationship to this project')}</h3><Markdown>{selected.relevance || selected.abstract || text('尚未写入摘要。', 'No summary recorded.')}</Markdown>{selected.abstract && selected.relevance ? <><h3>{text('摘要', 'Abstract')}</h3><p>{selected.abstract}</p></> : null}</div><div className="paper-detail__source"><span>{text('证据文件', 'Evidence file')}</span><code>{selected.sourcePath}</code></div>{selected.url ? <a className="button button--secondary button--full" href={selected.url} target="_blank" rel="noreferrer">{text('打开原始来源', 'Open source')} <ExternalLink size={14} /></a> : null}</> : <EmptyState icon={BookOpen} title={text('选择一篇论文', 'Select a paper')} />}
          </section>
          <section className="ros-card retrieval-panel"><header><div><span>ARGUS RETRIEVAL</span><h2>{text('最近检索', 'Recent retrieval')}</h2></div><Badge tone={props.connected ? 'live' : 'warn'} dot>{props.connected ? 'Live' : 'Polling'}</Badge></header><div>{(index.data?.searchFiles ?? []).slice(0, 8).map((file) => <article key={file.path}><FileSearch size={13} /><div><strong>{file.name}</strong><code>{file.path}</code></div><time>{formatClock(file.mtime, locale)}</time></article>)}{!index.data?.searchFiles.length && retrievalEvents.slice(0, 8).map((event, indexValue) => <article key={`${event.ts}-${indexValue}`}><FileSearch size={13} /><div><strong>{eventTitle(event)}</strong><code>{eventDetail(event, 100)}</code></div><time>{formatClock(event.ts, locale)}</time></article>)}</div></section>
          <section className="ros-card literature-ask"><header><div><span>NEW SEARCH</span><h2>{text('让 Argus 调研新工作', 'Ask Argus to research new work')}</h2></div></header><textarea rows={3} value={researchPrompt} onChange={(event) => setResearchPrompt(event.target.value)} placeholder={text('例如：检索 2025–2026 年与当前方法最接近的直接竞争工作…', 'Example: find the closest competing work from 2025–2026…')} /><button className="button button--primary button--full" type="button" disabled={!researchPrompt.trim() || manager.busy} onClick={() => void askArgus()}><Sparkles size={14} />{manager.busy ? manager.phase || text('检索中', 'Researching') : text('发起文献调研', 'Start literature research')}</button>{manager.output ? <div className="manager-mini-result"><Markdown>{manager.output}</Markdown></div> : null}</section>
        </aside>
      </div>
    </div>
  );
}
