import { Fragment, useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, type SearchResult } from '../api';
import { useI18n } from '../i18n';
import { errorText } from '../lib/format';
import { downloadBlob } from '../lib/downloadBlob';
import { Modal, ModalHeader } from './Modal';
import { MarkdownContent } from './MarkdownContent';
import { MarkdownExcerpt } from './MarkdownExcerpt';
import { DiffLines } from './DeliveryChanges';
import { Button } from './primitives';
import { ErrorDetails, ErrorMessage } from './ErrorMessage';
import { roleLabel } from '../lib/enumLabels';
import './experience.css';

export function HistorySearch({ open, onClose, onChoose }: {
  open: boolean; onClose: () => void; onChoose: (result: SearchResult) => void;
}) {
  const { locale, t } = useI18n();
  const zh = locale === 'zh-CN';
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  useEffect(() => {
    if (!open) { setInput(''); setQuery(''); }
  }, [open]);
  useEffect(() => {
    const timer = window.setTimeout(() => { setQuery(input.trim()); }, 300);
    return () => window.clearTimeout(timer);
  }, [input]);
  const results = useQuery({
    queryKey: ['history-search', query], queryFn: ({ signal }) => api.searchHistory(query, signal),
    enabled: open && Boolean(query), refetchOnWindowFocus: false,
  });
  return <Modal open={open} onClose={onClose} label={zh ? '搜索历史与成果' : 'Search history and results'} width="max-w-4xl" align="top">
    <div className="experience-shell history-search">
      <ModalHeader title={zh ? '搜索历史与成果' : 'Search history and results'} sub={zh ? '在项目、对话与成果中查找' : 'Find content across projects, conversations and results'} />
      {/* ↑ and ↓ go between the box and the results, as Tab does. */}
      <div className="experience-panel search-body" onKeyDown={event => {
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
        const stops = [...event.currentTarget.querySelectorAll<HTMLElement>('input, .search-result-list button')];
        const next = stops[stops.indexOf(document.activeElement as HTMLElement) + (event.key === 'ArrowDown' ? 1 : -1)];
        if (!next) return;
        event.preventDefault();
        next.focus();
      }}>
      <input data-autofocus type="search" aria-label={zh ? '搜索历史内容' : 'Search history content'} className="experience-input"
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
      <div className="search-result-list" aria-label={zh ? '搜索结果' : 'Search results'}>
        {/* A project's results come together under its name, said once; each is quoted as the
            conversation renders it, without its Markdown's marks. */}
        {results.data?.results.map((result, index, all) => <Fragment
          key={`${result.sid}:${result.kind}:${result.line ?? result.path ?? index}:${result.version ?? ''}`}>
          {result.sid !== all[index - 1]?.sid && <h3 className="search-project">{result.title}</h3>}
          <button type="button" onClick={() => { onChoose(result); onClose(); }}>
            <small>{result.role ? roleLabel(result.role, t)
              : result.kind === 'conversation' ? zh ? '对话记录' : 'Conversation'
              : result.kind === 'edition' ? zh ? `修改稿 v${result.version}` : `Edition v${result.version}`
                : result.kind === 'artifact' ? result.path : zh ? '项目' : 'Project'}</small>
            <span><MarkdownExcerpt>{result.snippet}</MarkdownExcerpt></span>
          </button>
        </Fragment>)}
      </div>
      </div>
    </div>
  </Modal>;
}

export function ProjectTrash({ open, onClose, onRestored }: {
  open: boolean; onClose: () => void; onRestored: (sid: string) => Promise<void>;
}) {
  const { locale, t } = useI18n();
  const zh = locale === 'zh-CN';
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [restoring, setRestoring] = useState('');
  const [error, setError] = useState<unknown>(null);
  const trash = useQuery({ queryKey: ['trash', query, offset],
    queryFn: ({ signal }) => api.trash(query, offset, signal), enabled: open });
  const restore = async (id: string) => {
    setRestoring(id); setError(null);
    try {
      const restored = await api.restoreTrash(id);
      await onRestored(restored.sid);
      onClose();
    } catch (problem) { setError(problem); }
    finally { setRestoring(''); }
  };
  return <Modal open={open} onClose={onClose} label={zh ? '回收站' : 'Trash'} width="max-w-2xl">
    <div className="experience-shell">
      <ModalHeader title={zh ? '回收站' : 'Trash'} />
      <div className="experience-panel">
        <input type="search" className="experience-input" data-autofocus value={query}
          aria-label={t('operations.searchTrash')} placeholder={t('operations.searchTrash')}
          onChange={event => { setQuery(event.target.value); setOffset(0); }} />
        {trash.isPending && <p role="status">{t('common.loading')}</p>}
        {(trash.isError || Boolean(error)) && <div role="alert"><ErrorMessage error={error || trash.error} />
          <Button onClick={() => { setError(null); void trash.refetch(); }}>{zh ? '重试' : 'Retry'}</Button></div>}
        {trash.data && <>
          {!trash.data.total && <p role="status">{query ? zh ? '没有匹配的项目。' : 'No matching projects.' : t('operations.trashEmpty')}</p>}
          <div className="search-result-list">
            {trash.data.entries.map(entry => <div key={entry.trash_id} className="flex items-center gap-3 border-b border-line py-3">
              <div className="min-w-0 flex-1"><strong className="block truncate" title={entry.label}>{entry.label}</strong>
                <time className="block text-ink-faint" dateTime={new Date(entry.trashed_at * 1000).toISOString()}>{new Date(entry.trashed_at * 1000).toLocaleString(locale)}</time></div>
              <Button disabled={Boolean(restoring)} onClick={() => void restore(entry.trash_id)}>
                {restoring === entry.trash_id ? zh ? '正在恢复…' : 'Restoring…' : zh ? '恢复' : 'Restore'}</Button>
            </div>)}
          </div>
          {(offset > 0 || offset + trash.data.limit < trash.data.total) && <div className="experience-toolbar">
            <Button disabled={!offset || Boolean(restoring)} onClick={() => setOffset(Math.max(0, offset - trash.data.limit))}>{zh ? '上一页' : 'Previous'}</Button>
            <Button disabled={offset + trash.data.limit >= trash.data.total || Boolean(restoring)} onClick={() => setOffset(offset + trash.data.limit)}>{zh ? '下一页' : 'Next'}</Button>
          </div>}
        </>}
      </div>
    </div>
  </Modal>;
}

export function ArtifactEditor({ sid, path, initialVersion, onClose, onRequest }: {
  sid: string; path: string; initialVersion?: number; onClose: () => void; onRequest: (text: string) => void;
}) {
  const zh = useI18n().locale === 'zh-CN';
  const query = useQuery({ queryKey: ['editions', sid, 'artifact', path], queryFn: () => api.editions(sid, path), refetchOnWindowFocus: false });
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
      await api.saveEdition(sid, { source: path, content, base_version: latest });
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
