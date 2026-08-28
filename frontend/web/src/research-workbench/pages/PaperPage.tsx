import { useQuery } from '@tanstack/react-query';
import { BookOpen, FileImage, FileText, FolderOpen, RefreshCw, Table2, Watch } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { PDFDocumentLoadingTask, PDFDocumentProxy, RenderTask } from 'pdfjs-dist';
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { Badge, EmptyState, Markdown } from '../components/Common';
import { formatBytes, formatDate } from '../utils';
import { paperAssets, workspaceApi, type WorkspaceEntry } from '../workspaceApi';
import { useWorkbenchText } from '../useWorkbenchText';
import { useWorkspaceBlobUrl } from '../useWorkspaceBlobUrl';
import { useWorkspaceProfile } from '../useWorkspaceProfile';
import type { WorkspacePageProps } from './pageTypes';

type OutputTab = 'pdf' | 'figures' | 'references';
function isImage(entry: WorkspaceEntry) { return ['.png', '.jpg', '.jpeg', '.webp', '.svg'].includes(entry.extension); }
function isTable(entry: WorkspaceEntry) { return ['.csv', '.tsv'].includes(entry.extension); }
function isSource(entry: WorkspaceEntry) { return ['.tex', '.md'].includes(entry.extension); }

function PdfCanvasPreview({ src, name }: { src: string; name: string }) {
  const { text } = useWorkbenchText();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [scale, setScale] = useState(1.25);
  const [error, setError] = useState('');
  const [rendered, setRendered] = useState(false);
  useEffect(() => {
    let alive = true;
    let task: PDFDocumentLoadingTask | null = null;
    setDocument(null); setPageNumber(1); setError(''); setRendered(false);
    const auth = localStorage.getItem('argus_web_token');
    Promise.all([
      fetch(src, { headers: auth ? { Authorization: `Bearer ${auth}` } : {} }).then((response) => {
        if (!response.ok) throw new Error(`PDF request failed (${response.status})`);
        return response.arrayBuffer();
      }),
      import('pdfjs-dist'),
    ]).then(([data, pdfjs]) => {
      if (!alive) return;
      pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
      task = pdfjs.getDocument({ data });
      return task.promise;
    }).then((pdf) => { if (alive && pdf) setDocument(pdf); }).catch((caught) => { if (alive) setError(caught instanceof Error ? caught.message : String(caught)); });
    return () => { alive = false; void task?.destroy(); };
  }, [src]);
  useEffect(() => {
    if (!document || !canvasRef.current) return;
    setRendered(false);
    let cancelled = false;
    let renderTask: RenderTask | null = null;
    void document.getPage(pageNumber).then((page) => {
      if (cancelled || !canvasRef.current) return;
      const viewport = page.getViewport({ scale });
      const canvas = canvasRef.current;
      const context = canvas.getContext('2d');
      if (!context) return;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.floor(viewport.width * ratio);
      canvas.height = Math.floor(viewport.height * ratio);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      renderTask = page.render({ canvas, canvasContext: context, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] });
      return renderTask.promise.then(() => { if (!cancelled) setRendered(true); });
    }).catch((caught) => { if (!cancelled) setError(caught instanceof Error ? caught.message : String(caught)); });
    return () => { cancelled = true; renderTask?.cancel(); };
  }, [document, pageNumber, scale]);
  return <div className="pdf-canvas-viewer"><div className="pdf-canvas-toolbar"><strong>{name}</strong><span>{text('第', 'Page')} {pageNumber} / {document?.numPages ?? '…'}</span><button type="button" disabled={pageNumber <= 1} onClick={() => setPageNumber((value) => value - 1)}>{text('上一页', 'Previous')}</button><button type="button" disabled={!document || pageNumber >= document.numPages} onClick={() => setPageNumber((value) => value + 1)}>{text('下一页', 'Next')}</button><button type="button" onClick={() => setScale((value) => Math.max(.75, value - .15))}>−</button><button type="button" onClick={() => setScale((value) => Math.min(2, value + .15))}>＋</button></div>{error ? <div className="inline-error">{error}</div> : null}<div className="pdf-canvas-scroll"><canvas ref={canvasRef} data-rendered={rendered ? 'true' : 'false'} /></div></div>;
}

function SourcePreview({ sid, workspaceId, entry }: { sid: string; workspaceId: string; entry: WorkspaceEntry | null }) {
  const { text } = useWorkbenchText();
  const file = useQuery({ queryKey: ['paper-source-file', sid, workspaceId, entry?.path, entry?.mtime], queryFn: ({ signal }) => workspaceApi.file(sid, workspaceId, entry!.path, signal), enabled: Boolean(entry && workspaceId), refetchInterval: 8_000 });
  if (!entry) return <EmptyState icon={FileText} title={text('等待 Argus 写入论文源文件', 'Waiting for Argus to write a paper source')} description={text('paper/ 或 technical_report/ 中出现 .tex / .md 后会自动加入。', '.tex and .md files under paper/ or technical_report/ appear automatically.')} />;
  if (file.isError) return <EmptyState icon={FileText} title={text('源文件暂时无法读取', 'Source file unavailable')} description={file.error.message} />;
  if (entry.extension === '.md' && file.data) return <div className="paper-markdown-preview"><Markdown>{file.data.content}</Markdown></div>;
  return <div className="latex-source"><div className="latex-line-numbers">{(file.data?.content ?? '').split('\n').map((_, index) => <span key={index}>{index + 1}</span>)}</div><pre>{file.data?.content || 'Loading…'}</pre></div>;
}

function WorkspaceFigure({ sid, workspaceId, entry }: { sid: string; workspaceId: string; entry: WorkspaceEntry }) {
  const blob = useWorkspaceBlobUrl(sid, workspaceId, entry.path);
  return <figure>{blob.url ? <img src={blob.url} alt={entry.name} /> : <div className="figure-loading">{blob.error || 'Loading…'}</div>}<figcaption>{entry.name}</figcaption></figure>;
}

export function PaperPage(props: WorkspacePageProps) {
  const { locale, text } = useWorkbenchText();
  const workspace = useWorkspaceProfile(props.sid, 'paper');
  const workspaceId = workspace.workspaceId;
  const root = workspace.active?.path || '';
  const tree = useQuery({ queryKey: ['paper-workspace-tree', props.sid, workspaceId], queryFn: ({ signal }) => workspaceApi.tree(props.sid, workspaceId, signal), enabled: Boolean(workspaceId), refetchInterval: 10_000 });
  const assets = useMemo(() => paperAssets(tree.data?.entries ?? []), [tree.data?.entries]);
  const sources = assets.filter(isSource);
  const references = assets.filter((entry) => entry.extension === '.bib');
  const pdfs = assets.filter((entry) => entry.extension === '.pdf');
  const figures = assets.filter((entry) => isImage(entry) || isTable(entry));
  const [selectedPath, setSelectedPath] = useState('');
  const selected = [...sources, ...references].find((entry) => entry.path === selectedPath) ?? sources[0] ?? references[0] ?? null;
  const [outputTab, setOutputTab] = useState<OutputTab>('pdf');
  const [selectedOutput, setSelectedOutput] = useState('');
  const preferredPdf = pdfs.find((entry) => /(?:^|\/)(?:argus-technical-report|main|paper|manuscript)\.pdf$/i.test(entry.path)) ?? pdfs[0];
  const activePdf = pdfs.find((entry) => entry.path === selectedOutput) ?? preferredPdf ?? null;

  return (
    <div className="ros-page paper-v3">
      <header className="ros-page-header"><div><div className="eyebrow">PAPER WORKSPACE</div><h1>{text('LaTeX 论文工作区', 'LaTeX paper workspace')}</h1><p>{text('论文源文件、编译 PDF、图表和 BibTeX 与真实项目目录保持同步。', 'Keep paper sources, compiled PDFs, figures, and BibTeX synchronized with the real project directory.')}</p></div><div className="header-badges"><Badge tone={tree.isError ? 'danger' : tree.isFetching ? 'live' : 'success'} dot><Watch size={12} />{tree.isError ? text('同步失败', 'Sync failed') : tree.isFetching ? text('同步中', 'Syncing') : text('自动同步', 'Auto sync')}</Badge><Badge tone={pdfs.length ? 'success' : 'neutral'}>{pdfs.length} PDF</Badge><Badge tone="neutral">{figures.length} {text('图表', 'figures')}</Badge></div></header>
      <div className="paper-root-bar ros-card"><FolderOpen size={16} /><div><span>APPROVED PAPER WORKSPACE</span><select aria-label={text('选择论文工作区', 'Select paper workspace')} value={workspaceId} onChange={(event) => { workspace.setWorkspaceId(event.target.value); setSelectedPath(''); setSelectedOutput(''); }}>{workspace.profiles.data?.profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.label}</option>)}</select><code>{root}</code></div>{tree.isError ? <Badge tone="danger">Error</Badge> : tree.isFetching ? <Badge tone="live" dot>Scanning</Badge> : <Badge tone="success">Synced</Badge>}<button className="icon-button" type="button" onClick={() => void tree.refetch()} aria-label={text('刷新论文工作区', 'Refresh paper workspace')}><RefreshCw size={14} /></button></div>

      <div className="paper-v3__shell">
        <aside className="paper-v3__sources ros-card">
          <header><div><span>LATEX PROJECT</span><h2>{text('论文文件', 'Paper files')}</h2></div><Badge tone="neutral">{sources.length + references.length}</Badge></header>
          <div className="paper-source-group"><h3><FileText size={13} />MANUSCRIPT</h3>{sources.length ? sources.map((entry) => <button type="button" className={selected?.path === entry.path ? 'is-active' : ''} key={entry.path} onClick={() => setSelectedPath(entry.path)}><FileText size={14} /><div><strong>{entry.name}</strong><code>{entry.path}</code></div><small>{formatBytes(entry.size)}</small></button>) : <p>等待 .tex / .md</p>}</div>
          <div className="paper-source-group"><h3><BookOpen size={13} />BIBTEX</h3>{references.length ? references.map((entry) => <button type="button" key={entry.path} onClick={() => setSelectedPath(entry.path)}><BookOpen size={14} /><div><strong>{entry.name}</strong><code>{entry.path}</code></div></button>) : <p>{text('等待 references.bib', 'Waiting for references.bib')}</p>}</div>
          <footer><span>{text('监听', 'Watching')}</span><code>{root}</code></footer>
        </aside>

        <main className="paper-v3__source ros-card">
          <header><div><strong>{selected?.name || text('源文件编辑器', 'Source editor')}</strong><code>{selected?.path || root}</code></div>{selected ? <span>{text('更新于', 'Updated')} {formatDate(selected.mtime, locale)}</span> : null}</header>
          <div><SourcePreview sid={props.sid} workspaceId={workspaceId} entry={selected} /></div>
          <footer><span>{selected?.extension.replace('.', '').toUpperCase() || 'WAITING'}</span><span>{selected ? formatBytes(selected.size) : text('Argus 写入后自动出现', 'Appears after Argus writes it')}</span></footer>
        </main>

        <aside className="paper-v3__outputs ros-card">
          <header><div><span>BUILD OUTPUT</span><h2>{text('可视化产出', 'Visual outputs')}</h2></div>{tree.isError ? <Badge tone="danger">Error</Badge> : tree.isFetching ? <Badge tone="live" dot>Scanning</Badge> : <Badge tone="success">Synced</Badge>}</header>
          <nav><button type="button" className={outputTab === 'pdf' ? 'is-active' : ''} onClick={() => setOutputTab('pdf')}><FileText size={14} />PDF <small>{pdfs.length}</small></button><button type="button" className={outputTab === 'figures' ? 'is-active' : ''} onClick={() => setOutputTab('figures')}><FileImage size={14} />{text('图表', 'Figures')} <small>{figures.length}</small></button><button type="button" className={outputTab === 'references' ? 'is-active' : ''} onClick={() => setOutputTab('references')}><BookOpen size={14} />{text('引用', 'References')} <small>{references.length}</small></button></nav>
          <div className="paper-output-surface">
            {outputTab === 'pdf' ? activePdf ? <><div className="pdf-switcher">{pdfs.map((entry) => <button type="button" className={activePdf.path === entry.path ? 'is-active' : ''} key={entry.path} onClick={() => setSelectedOutput(entry.path)}>{entry.name}</button>)}</div><PdfCanvasPreview src={workspaceApi.rawUrl(props.sid, workspaceId, activePdf.path)} name={activePdf.name} /></> : <EmptyState icon={FileText} title={text('尚无编译 PDF', 'No compiled PDF')} description={text('Argus 或 LaTeX 流程生成 PDF 后会直接在这里可视化。', 'PDFs generated by Argus or the LaTeX pipeline appear here.')} /> : null}
            {outputTab === 'figures' ? figures.length ? <div className="paper-figure-grid">{figures.map((entry) => isImage(entry) ? <WorkspaceFigure key={entry.path} sid={props.sid} workspaceId={workspaceId} entry={entry} /> : <article key={entry.path}><Table2 size={22} /><strong>{entry.name}</strong><code>{entry.path}</code></article>)}</div> : <EmptyState icon={FileImage} title={text('尚无图表产出', 'No figure outputs')} /> : null}
            {outputTab === 'references' ? references.length ? <div className="paper-reference-list">{references.map((entry) => <button type="button" key={entry.path} onClick={() => { setSelectedPath(entry.path); }}><BookOpen size={15} /><div><strong>{entry.name}</strong><code>{entry.path}</code></div></button>)}</div> : <EmptyState icon={BookOpen} title={text('尚无 BibTeX', 'No BibTeX')} /> : null}
          </div>
          <footer><span>{pdfs.length ? 'PDF build detected' : 'Waiting for LaTeX build'}</span><span>{assets.length} tracked assets</span></footer>
        </aside>
      </div>
    </div>
  );
}
