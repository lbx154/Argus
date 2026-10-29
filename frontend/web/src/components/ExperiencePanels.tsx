import { Fragment, useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, type Edition, type SearchResult } from '../api';
import { useI18n } from '../i18n';
import { errorText } from '../lib/format';
import { downloadBlob } from '../lib/downloadBlob';
import { Modal, ModalHeader } from './Modal';
import { MarkdownContent } from './MarkdownContent';
import { MarkdownExcerpt } from './MarkdownExcerpt';
import { DiffLines } from './DeliveryChanges';
import { Button } from './primitives';
import { ErrorDetails } from './ErrorMessage';
import './experience.css';

export function HistorySearch({ open, onClose, onChoose }: {
  open: boolean; onClose: () => void; onChoose: (result: SearchResult) => void;
}) {
  const zh = useI18n().locale === 'zh-CN';
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<SearchResult | null>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => { setQuery(input.trim()); setSelected(null); }, 300);
    return () => window.clearTimeout(timer);
  }, [input]);
  const results = useQuery({
    queryKey: ['history-search', query], queryFn: ({ signal }) => api.searchHistory(query, signal),
    enabled: open && Boolean(query), refetchOnWindowFocus: false,
  });
  return <Modal open={open} onClose={onClose} label={zh ? '搜索历史与成果' : 'Search history and results'} width="max-w-4xl" align="top">
    <div className="experience-shell history-search">
      <ModalHeader title={zh ? '搜索历史与成果' : 'Search history and results'} sub={zh ? '在项目、对话与成果中查找' : 'Find content across projects, conversations and results'} />
      <div className="experience-panel search-body">
      <input autoFocus type="search" aria-label={zh ? '搜索历史内容' : 'Search history content'} className="experience-input"
        placeholder={zh ? '搜索消息、结论、成果正文或修改稿…' : 'Search messages, conclusions, results or editions…'}
        value={input} onChange={event => setInput(event.target.value)} />
      {!query && <div className="experience-empty">
        <strong>{zh ? '输入关键词，找回之前的工作' : 'Pick up where you left off'}</strong>
        <p>{zh ? '搜索可见对话、成果正文和修改稿，不读取模型内部推理。' : 'Search visible conversations, result contents and editions, not private reasoning.'}</p>
      </div>}
      {query && results.isFetching && <p role="status">{zh ? '正在搜索…' : 'Searching…'}</p>}
      {results.isError && <p role="alert" className="text-err">{errorText(results.error)}</p>}
      {results.data?.partial && <p role="status" className="text-warn">{zh ? '已达到本次搜索的结果或读取上限；请增加关键词缩小范围。较大文件请打开后查找。' : 'Result or read limit reached. Narrow the query; open large files to search them.'}</p>}
      {results.data?.errors.length ? <div role="alert" className="text-warn">{zh ? '部分记录无法读取。已读取的内容仍可搜索，请查看详情后重试。' : 'Some records could not be read. You can still search loaded content; check the details and retry.'}<ErrorDetails error={results.data.errors.join('\n')} /></div> : null}
      {query && results.isSuccess && !results.data.results.length && <div className="experience-empty" role="status">
        <strong>{zh ? '没有找到匹配内容' : 'No matching content'}</strong>
        <p>{zh ? '试试更短的关键词，或换一种说法。' : 'Try a shorter keyword or a different phrase.'}</p>
      </div>}
      <div className="search-layout" data-reading={Boolean(selected)}>
      <div className="search-result-list" aria-label={zh ? '搜索结果' : 'Search results'}>
        {/* A project's results come together under its name, said once; each is quoted as the
            conversation renders it, without its Markdown's marks. */}
        {results.data?.results.map((result, index, all) => <Fragment
          key={`${result.sid}:${result.kind}:${result.line ?? result.path ?? index}:${result.version ?? ''}`}>
          {result.sid !== all[index - 1]?.sid && <h3 className="search-project">{result.title}</h3>}
          <button type="button" aria-pressed={selected === result} onClick={() => setSelected(result)} className={selected === result ? 'is-selected' : ''}>
            <small>{result.kind === 'conversation' ? zh ? '对话记录' : 'Conversation'
              : result.kind === 'edition' ? zh ? `修改稿 v${result.version}` : `Edition v${result.version}`
                : result.kind === 'artifact' ? result.path : zh ? '项目' : 'Project'}</small>
            <span><MarkdownExcerpt>{result.snippet}</MarkdownExcerpt></span>
          </button>
        </Fragment>)}
      </div>
      {selected && <section className="search-reader">
        <header>
          <Button className="search-back" onClick={() => setSelected(null)}>{zh ? '返回搜索结果' : 'Back to results'}</Button>
          <strong>{selected.path || selected.title}</strong>
          <Button variant="primary" onClick={() => { onChoose(selected); onClose(); }}>{zh ? '打开并定位' : 'Open in project'}</Button>
        </header>
        {selected.truncated && <p className="text-warn">{zh ? '这里只展示匹配位置附近的片段；文件全文可在项目内打开。' : 'Showing an excerpt around the match. Open the project file for the full content.'}</p>}
        <MarkdownContent>{selected.text}</MarkdownContent>
      </section>}
      </div>
      </div>
    </div>
  </Modal>;
}

export function ArtifactEditor({ sid, path, initialVersion, onClose, onRequest }: {
  sid: string; path: string; initialVersion?: number; onClose: () => void; onRequest: (text: string) => void;
}) {
  const zh = useI18n().locale === 'zh-CN';
  const query = useQuery({ queryKey: ['editions', sid, 'artifact', path], queryFn: () => api.editions(sid, 'artifact', path), refetchOnWindowFocus: false });
  const [content, setContent] = useState('');
  const [selectedVersion, setSelectedVersion] = useState(-1);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [selection, setSelection] = useState('');
  // The draft is edited, read as it renders, or read as what it changes in the source file.
  const [view, setView] = useState<'edit' | 'preview' | 'compare'>('edit');
  const [changes, setChanges] = useState<{ diff: string; truncated: boolean } | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const initialized = useRef(false);
  const latest = query.data?.versions.at(-1)?.version ?? 0;
  useEffect(() => {
    if (!query.data) return;
    const edition = initialized.current || initialVersion === undefined ? query.data.versions.at(-1) : query.data.versions.find(item => item.version === initialVersion);
    initialized.current = true;
    setContent(edition?.content ?? query.data.original);
    setSelectedVersion(edition?.version ?? -1);
    setDirty(false);
  }, [query.data, initialVersion]);
  useEffect(() => {
    if (view !== 'compare') return;
    let live = true;
    setChanges(null);
    api.editionDiff(sid, path, content).then(result => { if (live) setChanges(result); }, problem => { if (live) setError(errorText(problem)); });
    return () => { live = false; };
  }, [view, sid, path, content]);
  const close = () => {
    if (!dirty || window.confirm(zh ? '尚有未保存修改，确定关闭？' : 'Discard unsaved changes?')) onClose();
  };
  const choose = (version: number) => {
    if (dirty && !window.confirm(zh ? '切换版本将放弃尚未保存的修改，继续？' : 'Discard unsaved changes and switch version?')) return;
    setContent(query.data?.versions.find(item => item.version === version)?.content ?? query.data?.original ?? '');
    setSelectedVersion(version); setDirty(false); setSelection(''); setError('');
  };
  const save = async () => {
    setBusy(true); setError('');
    try {
      await api.saveEdition(sid, 'artifact', { source: path, content, base_version: latest });
      await query.refetch({ throwOnError: true });
      return true;
    } catch (problem) { setError(errorText(problem)); return false; }
    finally { setBusy(false); }
  };
  const ask = async () => {
    const excerpt = selection || content;
    const prompt = zh
      ? `请修改文件 ${path} 的${selection ? '以下选中部分' : '以下修改稿'}，另存为新版本，不要覆盖原文件。\n\n${excerpt}\n\n我的修改要求：`
      : `Revise ${selection ? 'this selection from' : 'this draft of'} ${path} and save it as a new version without overwriting the original.\n\n${excerpt}\n\nMy requested changes:`;
    if (prompt.length > 32_000) { setError(zh ? '内容过长，请先选中要修改的片段。' : 'Select a shorter passage before requesting a revision.'); return; }
    if (dirty && !await save()) return;
    onRequest(prompt);
  };
  return <Modal open onClose={close} label={zh ? '成果编辑与版本' : 'Edit result and versions'} width="max-w-6xl">
    <div className="experience-shell">
      <ModalHeader title={zh ? '成果编辑与版本' : 'Edit result and versions'} sub={path} />
      <div className="experience-panel edition-body">
      {query.isPending && <p role="status">{zh ? '正在读取…' : 'Loading…'}</p>}
      {query.isError && <p role="alert" className="text-err">{errorText(query.error)}</p>}
      {query.data && <>
        <div className="experience-toolbar">
          <select className="experience-select" aria-label={zh ? '成果版本' : 'Result version'} value={selectedVersion} disabled={busy} onChange={event => choose(Number(event.target.value))}>
            <option value={-1}>{zh ? '现在的文件' : 'The file as it is'}</option>
            {query.data.versions.map(edition => <option key={edition.version} value={edition.version}>{edition.version === 0
              ? zh ? '第一次修改前的文件' : 'The file before the first draft'
              : zh ? `修改稿 ${edition.version}` : `Draft ${edition.version}`}</option>)}
          </select>
          <div className="route-seg" role="radiogroup" aria-label={zh ? '查看方式' : 'View'}>
            {([['edit', '编辑', 'Edit'], ['preview', '预览', 'Preview'], ['compare', '和现在的文件对比', 'Compare with the file']] as const).map(([mode, zhLabel, enLabel]) =>
              <button key={mode} type="button" role="radio" aria-checked={view === mode} data-view={mode} onClick={() => setView(mode)}>{zh ? zhLabel : enLabel}</button>)}
          </div>
          <span className="edition-save-status" role="status">{dirty ? zh ? '尚未保存' : 'Unsaved changes'
            : zh ? '内容已保存' : 'Content saved'}</span>
        </div>
        <div className="edition-pane">
          {view === 'edit' ? <textarea
            ref={textarea} aria-label={zh ? '修改稿内容' : 'Edition content'} value={content} disabled={busy}
            onChange={event => { setContent(event.target.value); setDirty(true); }}
            onSelect={() => { const input = textarea.current; if (input) setSelection(input.value.slice(input.selectionStart, input.selectionEnd)); }} />
            : view === 'preview' ? <div className="edition-preview"><MarkdownContent>{content}</MarkdownContent></div>
              : !changes ? <p role="status" className="edition-note">{zh ? '正在对比…' : 'Comparing…'}</p>
                : changes.diff ? <DiffLines diff={changes.diff} truncated={changes.truncated} zh={zh} />
                  : <p role="status" className="edition-note">{zh ? '和现在的文件一样，没有改动。' : 'The same as the file as it is.'}</p>}
        </div>
        <p className="experience-caption">{zh ? '修改稿另外保存，不改动文件本身。' : 'Drafts are saved apart from the file and leave it as it is.'}</p>
      </>}
      {error && <div role="alert" className="experience-notice text-err">{error}<Button onClick={() => void query.refetch()}>{zh ? '重新读取版本（会放弃未保存修改）' : 'Reload versions (discard unsaved changes)'}</Button></div>}
      </div>
      {query.data && <div className="experience-footer">
          <Button variant="primary" onClick={() => void save()} disabled={busy || !dirty}>
            {busy ? zh ? '正在保存…' : 'Saving…' : zh ? '保存为新版本' : 'Save new version'}</Button>
          <Button disabled={busy} onClick={() => {
            const name = path.substring(path.lastIndexOf('/') + 1);
            const dot = name.lastIndexOf('.');
            const stem = dot > 0 ? name.slice(0, dot) : name;
            const suffix = dot > 0 ? name.slice(dot) : '.txt';
            downloadBlob(new Blob([content], { type: 'text/plain;charset=utf-8' }), `${stem}-v${selectedVersion < 0 ? 'source' : selectedVersion}${dirty ? '-unsaved' : ''}${suffix}`);
          }}>{zh ? '下载当前内容' : 'Download this content'}</Button>
          <Button disabled={busy} onClick={() => void ask()}>{selection ? zh ? '让 Argus 修改选中部分' : 'Ask Argus to revise selection' : zh ? '让 Argus 修改这份稿件' : 'Ask Argus to revise draft'}</Button>
          {selection && <small>{zh ? `已选 ${selection.length} 字` : `${selection.length} characters selected`}</small>}
        </div>
      }
    </div>
  </Modal>;
}

export interface ConversationRevision {
  source: string; prompt: string; answer: string; baseVersion: number;
}
export function ConversationEditor({ sid, target, onClose, onCompose }: {
  sid: string; target: ConversationRevision; onClose: () => void;
  onCompose: (text: string, target: ConversationRevision) => void;
}) {
  const zh = useI18n().locale === 'zh-CN';
  const query = useQuery({ queryKey: ['editions', sid, 'conversation', target.source], queryFn: () => api.editions(sid, 'conversation', target.source), refetchOnWindowFocus: false });
  const [prompt, setPrompt] = useState(target.prompt);
  const [edition, setEdition] = useState<Edition | null>(null);
  return <Modal open onClose={onClose} label={zh ? '改写后重发' : 'Rewrite and resend'} width="max-w-3xl">
    <ModalHeader title={zh ? '改写后重发' : 'Rewrite and resend'}
      sub={zh ? '改好的话放进输入框，发出去是一条新消息；原来的问答还在' : 'What you rewrite goes into the message box and is sent as a new message; the first question and answer stay'} />
    <div className="experience-panel">
      {query.isError && <p role="alert" className="text-err">{errorText(query.error)}</p>}
      <select className="experience-select" aria-label={zh ? '哪一次问答' : 'Which question and answer'} value={edition?.version ?? 0} onChange={event => {
        const next = query.data?.versions.find(item => item.version === Number(event.target.value)) ?? null;
        setEdition(next); setPrompt(next?.note || target.prompt);
      }}>
        <option value={0}>{zh ? '最初的问答' : 'The first question and answer'}</option>
        {query.data?.versions.filter(item => item.version > 0).map(item => <option key={item.version} value={item.version}>
          {zh ? `第 ${item.version} 次改写` : `Rewrite ${item.version}`}</option>)}
      </select>
      <label>{zh ? '要重发的话' : 'What to send again'}
        <textarea className="experience-input min-h-32" aria-label={zh ? '要重发的话' : 'What to send again'} maxLength={32_000}
          value={prompt} onChange={event => setPrompt(event.target.value)} /></label>
      {(edition?.content || target.answer) && <section className="conversation-version-reader">
        <h3>{zh ? '当时的回答' : 'The answer then'}</h3>
        <MarkdownContent>{edition?.content || target.answer}</MarkdownContent>
      </section>}
      <Button variant="primary" disabled={!prompt.trim() || query.isPending || query.isError}
        onClick={() => onCompose(prompt, { ...target, baseVersion: query.data?.versions.at(-1)?.version ?? 0 })}>
        {zh ? '放进输入框' : 'Put in the message box'}
      </Button>
    </div>
  </Modal>;
}
