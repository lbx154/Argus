import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, type Edition, type SearchResult } from '../api';
import { useI18n } from '../i18n';
import { errorText } from '../lib/format';
import { downloadBlob } from '../lib/downloadBlob';
import { Modal, ModalHeader } from './Modal';
import { MarkdownContent } from './MarkdownContent';
import { Button } from './primitives';
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
      {results.data?.errors.length ? <p role="alert" className="text-warn">{zh ? '部分记录无法读取：' : 'Some records could not be read: '}{results.data.errors.join(' · ')}</p> : null}
      {query && results.isSuccess && !results.data.results.length && <div className="experience-empty" role="status">
        <strong>{zh ? '没有找到匹配内容' : 'No matching content'}</strong>
        <p>{zh ? '试试更短的关键词，或换一种说法。' : 'Try a shorter keyword or a different phrase.'}</p>
      </div>}
      <div className="search-layout" data-reading={Boolean(selected)}>
      <div className="search-result-list" aria-label={zh ? '搜索结果' : 'Search results'}>
        {results.data?.results.map((result, index) => <button type="button"
          key={`${result.sid}:${result.kind}:${result.line ?? result.path ?? index}:${result.version ?? ''}`}
          aria-pressed={selected === result} onClick={() => setSelected(result)} className={selected === result ? 'is-selected' : ''}>
          <strong>{result.title}</strong><small>{result.kind === 'conversation' ? zh ? '对话记录' : 'Conversation'
            : result.kind === 'edition' ? zh ? `修改稿 v${result.version}` : `Edition v${result.version}`
              : result.kind === 'artifact' ? result.path : zh ? '项目' : 'Project'}</small>
          <span>{result.snippet}</span>
        </button>)}
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
  const [compare, setCompare] = useState(false);
  const [preview, setPreview] = useState(false);
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
            <option value={-1}>{zh ? '当前源文件' : 'Current source file'}</option>
            {query.data.versions.map(edition => <option key={edition.version} value={edition.version}>{edition.version === 0
              ? zh ? '首次修改前的源文件' : 'Source before first edit'
              : `${zh ? '未复审修改稿' : 'Unreviewed edition'} v${edition.version}`}</option>)}
          </select>
          <label className="experience-check"><input type="checkbox" checked={compare} onChange={event => setCompare(event.target.checked)} /> {zh ? '对照源文件' : 'Compare source'}</label>
          <Button aria-pressed={preview} onClick={() => setPreview(value => !value)}>{preview ? zh ? '继续编辑' : 'Edit' : zh ? '预览' : 'Preview'}</Button>
          <span className="edition-save-status" role="status">{dirty ? zh ? '尚未保存' : 'Unsaved changes'
            : zh ? '内容已保存' : 'Content saved'}</span>
        </div>
        <div className={`edition-grid ${compare ? 'is-comparing' : ''}`}>
          {compare && <section className="edition-pane">
            <h3>{zh ? '当前源文件 · 只读' : 'Current source · Read only'}</h3>
            <pre aria-label={zh ? '源文件对照' : 'Original comparison'}>{query.data.original}</pre>
          </section>}
          <section className="edition-pane">
          <h3>{preview ? zh ? '修改稿预览 · 未复审' : 'Edition preview · Unreviewed' : zh ? '修改稿 · 未复审' : 'Edition · Unreviewed'}</h3>
          {preview ? <div className="edition-preview"><MarkdownContent>{content}</MarkdownContent></div> : <textarea
            ref={textarea} aria-label={zh ? '修改稿内容' : 'Edition content'} value={content} disabled={busy}
            onChange={event => { setContent(event.target.value); setDirty(true); }}
            onSelect={() => { const input = textarea.current; if (input) setSelection(input.value.slice(input.selectionStart, input.selectionEnd)); }} />}
          </section>
        </div>
        <p className="experience-caption">{zh ? '修改稿单独保存，不覆盖原文件。' : 'Editions are saved separately from the original.'}</p>
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
  source: string; prompt: string; answer: string; mode: 'edit' | 'regenerate' | 'versions'; baseVersion: number;
}
export function ConversationEditor({ sid, target, onClose, onCompose }: {
  sid: string; target: ConversationRevision; onClose: () => void;
  onCompose: (text: string, target: ConversationRevision) => void;
}) {
  const zh = useI18n().locale === 'zh-CN';
  const query = useQuery({ queryKey: ['editions', sid, 'conversation', target.source], queryFn: () => api.editions(sid, 'conversation', target.source), refetchOnWindowFocus: false });
  const [prompt, setPrompt] = useState(target.prompt);
  const [edition, setEdition] = useState<Edition | null>(null);
  return <Modal open onClose={onClose} label={zh ? '对话修订与版本' : 'Conversation revisions and versions'} width="max-w-3xl">
    <ModalHeader title={zh ? '对话修订与版本' : 'Conversation revisions and versions'} sub={zh ? '准备新的对话草稿' : 'Prepare a new chat draft'} />
    <div className="experience-panel">
      {query.isError && <p role="alert" className="text-err">{errorText(query.error)}</p>}
      <select className="experience-select" aria-label={zh ? '对话版本' : 'Conversation version'} value={edition?.version ?? 0} onChange={event => {
        const next = query.data?.versions.find(item => item.version === Number(event.target.value)) ?? null;
        setEdition(next); setPrompt(next?.note || target.prompt);
      }}>
        <option value={0}>{zh ? '原始记录' : 'Original record'}</option>
        {query.data?.versions.filter(item => item.version > 0).map(item => <option key={item.version} value={item.version}>
          {zh ? '修订' : 'Revision'} v{item.version}</option>)}
      </select>
      <label>{zh ? '编辑后作为新消息发送' : 'Edit and send as a new message'}
        <textarea className="experience-input min-h-32" aria-label={zh ? '修订问题' : 'Revised question'} maxLength={32_000}
          value={prompt} onChange={event => setPrompt(event.target.value)} /></label>
      {(edition?.content || target.answer) && <section className="conversation-version-reader">
        <h3>{zh ? '对应回答' : 'Recorded answer'}</h3>
        <MarkdownContent>{edition?.content || target.answer}</MarkdownContent>
      </section>}
      <Button variant="primary" disabled={!prompt.trim() || query.isPending || query.isError}
        onClick={() => onCompose(prompt, { ...target, baseVersion: query.data?.versions.at(-1)?.version ?? 0 })}>
        {target.mode === 'regenerate' ? zh ? '准备重新回答（仅对话）' : 'Prepare another answer (chat only)' : zh ? '放入对话草稿' : 'Put in chat draft'}
      </Button>
    </div>
  </Modal>;
}

export function DeviceAccess({ open, onClose }: { open: boolean; onClose: () => void }) {
  const zh = useI18n().locale === 'zh-CN';
  const [link, setLink] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  return <Modal open={open} onClose={onClose} label={zh ? '设备与登录' : 'Devices and sign-in'}>
    <ModalHeader title={zh ? '设备与登录' : 'Devices and sign-in'} sub={zh ? '连接你的另一台设备，继续同一份工作' : 'Continue the same work on another device'} />
    <div className="experience-panel">
      <p className="text-ink-dim">{zh ? '配对后可共享项目、成果版本、已同步草稿和项目整理状态。' : 'Pairing shares projects, result editions, synced drafts and project organization.'}</p>
      <div className="experience-section">
      <h3>{zh ? '连接另一设备' : 'Connect another device'}</h3>
      <p className="experience-caption">{zh ? '仅限你自己的设备。登录链接可访问这个私有空间，请勿转发。' : 'Use only on your own devices. The link grants access to this private space; do not share it.'}</p>
      <Button variant="primary" disabled={busy} onClick={async () => {
        setBusy(true); setError('');
        try {
          const issued = await api.pairingLink();
          setLink(new URL(issued.path, window.location.href).toString());
          setCopied(false);
        } catch (problem) { setError(errorText(problem)); }
        finally { setBusy(false); }
      }}>{busy ? zh ? '正在处理…' : 'Working…' : zh ? '生成另一设备的单次登录链接' : 'Create a one-use sign-in link'}</Button>
      {link && <><input className="experience-input" type="password" aria-label={zh ? '单次登录链接' : 'One-use sign-in link'} readOnly value={link} onFocus={event => event.target.select()} />
        <Button onClick={async () => {
          try { await navigator.clipboard.writeText(link); setCopied(true); setError(''); }
          catch (problem) { setError(errorText(problem)); }
        }}>{copied ? zh ? '已复制登录链接' : 'Sign-in link copied' : zh ? '复制登录链接' : 'Copy sign-in link'}</Button>
        <small>{zh ? '1 小时内有效，仅可使用一次。' : 'Valid for one hour and one use.'}</small></>}
      </div>
      <div className="experience-section device-disconnect">
      <div><h3>{zh ? '当前浏览器' : 'This browser'}</h3>
        <p className="experience-caption">{zh ? '退出只断开本浏览器。' : 'Signing out disconnects only this browser.'}</p></div>
      <Button variant="danger" disabled={busy} onClick={async () => {
        setBusy(true); setError('');
        try { await api.disconnectBrowser(); window.location.reload(); }
        catch (problem) { setError(errorText(problem)); setBusy(false); }
      }}>{zh ? '退出当前浏览器' : 'Sign out this browser'}</Button>
      </div>
      {error && <p role="alert" className="text-err">{error}</p>}
    </div>
  </Modal>;
}
