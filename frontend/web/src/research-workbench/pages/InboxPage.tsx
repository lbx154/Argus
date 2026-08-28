import { AudioLines, Check, ChevronRight, FileText, Image, Inbox, Lightbulb, Link2, ListChecks, MessageSquareText, Plus, Save, Send, Sparkles, Target, Trash2, Upload, WandSparkles, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { Badge, EmptyState, Markdown } from '../components/Common';
import { formatDate } from '../utils';
import { useManagerRun } from '../useManagerRun';
import { useWorkbenchText } from '../useWorkbenchText';
import type { WorkspacePageProps } from './pageTypes';

interface InboxDraft {
  id: string; title: string; source: string; raw: string; prompt: string;
  changes: string[]; questions: string[]; createdAt: number; updatedAt: number; sentAt?: number;
}
interface KnowledgeSection { title: string; body: string; icon: typeof Target }
const MAX_RAW_CHARS = 200_000;
const MAX_FILES = 5;
const key = (sid: string) => `argus-v2-inbox:${sid}`;
const load = (sid: string): InboxDraft[] => { try { const value = JSON.parse(localStorage.getItem(key(sid)) ?? '[]'); return Array.isArray(value) ? value : []; } catch { return []; } };
const blank = (title: string, source: string): InboxDraft => ({ id: crypto.randomUUID(), title, source, raw: '', prompt: '', changes: [], questions: [], createdAt: Date.now(), updatedAt: Date.now() });

function sectionsOf(prompt: string, questions: string[], text: (zh: string, en: string) => string): KnowledgeSection[] {
  const headingMatches = [...prompt.matchAll(/^#{1,3}\s+(.+)\n([\s\S]*?)(?=^#{1,3}\s+|$)/gm)];
  const iconFor = (title: string) => /目标|objective|question/i.test(title) ? Target : /约束|constraint|boundary|non-goal/i.test(title) ? ListChecks : /文献|evidence|source|paper/i.test(title) ? Link2 : Lightbulb;
  const parsed = headingMatches.map((match) => ({ title: match[1].trim(), body: match[2].trim(), icon: iconFor(match[1]) })).filter((item) => item.body);
  if (parsed.length) return parsed.slice(0, 8);
  const paragraphs = prompt.split(/\n\s*\n/).map((item) => item.trim()).filter(Boolean);
  const fallback: KnowledgeSection[] = [];
  if (paragraphs[0]) fallback.push({ title: text('研究目标与背景', 'Research goal and context'), body: paragraphs[0], icon: Target });
  const constraints = prompt.split('\n').filter((line) => /不得|不要|必须|约束|only|must|do not|without/i.test(line)).join('\n');
  if (constraints) fallback.push({ title: text('约束与边界', 'Constraints and boundaries'), body: constraints, icon: ListChecks });
  if (questions.length) fallback.push({ title: text('仍需确认', 'Questions to confirm'), body: questions.map((item) => `- ${item}`).join('\n'), icon: Lightbulb });
  return fallback;
}

export function InboxPage(props: WorkspacePageProps) {
  const { locale, text } = useWorkbenchText();
  const [drafts, setDrafts] = useState<InboxDraft[]>(() => load(props.sid));
  const [selectedId, setSelectedId] = useState(() => load(props.sid)[0]?.id ?? '');
  const [extracting, setExtracting] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [textImportCount, setTextImportCount] = useState(0);
  const [error, setError] = useState('');
  const [storageError, setStorageError] = useState('');
  const [dispatchMode, setDispatchMode] = useState<'current' | 'new'>('current');
  const [newName, setNewName] = useState('');
  const [newWorkdir, setNewWorkdir] = useState('');
  const manager = useManagerRun(props.sid, props.refresh);
  useEffect(() => { const rows = load(props.sid); setDrafts(rows); setSelectedId(rows[0]?.id ?? ''); setFiles([]); setTextImportCount(0); }, [props.sid]);
  useEffect(() => { try { localStorage.setItem(key(props.sid), JSON.stringify(drafts)); setStorageError(''); } catch { setStorageError(text('本机草稿存储空间不足；请缩短文本或删除旧草稿。', 'Not enough local storage for this draft. Shorten the text or delete older drafts.')); } }, [drafts, props.sid, text]);
  const selected = drafts.find((item) => item.id === selectedId) ?? null;
  const knowledge = useMemo(() => sectionsOf(selected?.prompt ?? '', selected?.questions ?? [], text), [selected?.prompt, selected?.questions, text]);
  const update = (patch: Partial<InboxDraft>) => selected && setDrafts((rows) => rows.map((item) => item.id === selected.id ? { ...item, ...patch, updatedAt: Date.now() } : item));
  const create = () => { const item = blank(text('新的科研输入', 'New research input'), text('导师 / 组会 / 灵感', 'Advisor / meeting / idea')); setDrafts((rows) => [item, ...rows]); setSelectedId(item.id); };
  const remove = () => { if (!selected || !confirm(text(`删除“${selected.title}”？`, `Delete “${selected.title}”?`))) return; const next = drafts.filter((item) => item.id !== selected.id); setDrafts(next); setSelectedId(next[0]?.id ?? ''); };
  const extractionPrompt = selected ? text(
    `这是科研收信箱的预处理步骤，只分析输入并回复，不创建后台任务、不修改项目。请读取下面的零散内容和附件，提取知识点并生成第一版可直接交给 Argus 的研究 Prompt。必须保留事实来源和不确定性，不得虚构论文、实验或结论；使用以下 Markdown 结构：\n## 研究目标\n## 已知背景与知识点\n## 约束与非目标\n## 文献与证据线索\n## 建议任务与验收方式\n## 待确认问题\n\n标题：${selected.title}\n来源：${selected.source}\n\n原始内容：\n${selected.raw}`,
    `This is a Research Inbox preprocessing step. Analyze and reply only; do not create background work or modify the project. Read the rough content and attachments, extract the useful knowledge, and produce a first research prompt ready for Argus. Preserve sources and uncertainty, and do not invent papers, experiments, or conclusions. Use this Markdown structure:\n## Research goal\n## Known context and knowledge\n## Constraints and non-goals\n## Literature and evidence leads\n## Suggested tasks and acceptance criteria\n## Questions to confirm\n\nTitle: ${selected.title}\nSource: ${selected.source}\n\nRaw content:\n${selected.raw}`,
  ) : '';
  const addFiles = async (incoming: File[]) => {
    const textPattern = /\.(txt|md|markdown|json|csv|ya?ml|log|tex)$/i;
    const binaryPattern = /\.(pdf|png|jpe?g|webp|wav|mp3|m4a|ogg)$/i;
    const unsupported = incoming.filter((file) => !textPattern.test(file.name) && !binaryPattern.test(file.name));
    if (unsupported.length) { setError(text(`不支持的附件：${unsupported.map((file) => file.name).join('、')}`, `Unsupported attachments: ${unsupported.map((file) => file.name).join(', ')}`)); return; }
    const tooLarge = incoming.find((file) => file.size > 10 * 1024 * 1024);
    if (tooLarge) { setError(text(`${tooLarge.name} 超过单文件 10 MB 限制`, `${tooLarge.name} exceeds the 10 MB per-file limit`)); return; }
    if (files.length + textImportCount + incoming.length > MAX_FILES) { setError(text(`每次分析最多导入 ${MAX_FILES} 个文件`, `You can import up to ${MAX_FILES} files per analysis`)); return; }
    const textFiles = incoming.filter((file) => textPattern.test(file.name));
    const oversizedText = textFiles.find((file) => file.size > 1024 * 1024);
    if (oversizedText) { setError(text(`${oversizedText.name} 超过本机文本导入 1 MB 限制；请改为摘要或拆分文件`, `${oversizedText.name} exceeds the 1 MB local text-import limit. Summarize or split the file.`)); return; }
    const binaryFiles = incoming.filter((file) => binaryPattern.test(file.name));
    const merged = [...files, ...binaryFiles];
    if (merged.reduce((total, file) => total + file.size, 0) > 25 * 1024 * 1024) { setError(text('附件总大小超过 25 MB', 'Attachments exceed the 25 MB total limit')); return; }
    const blocks = await Promise.all(textFiles.map(async (file) => `\n\n--- ${text('文件', 'File')}: ${file.name} ---\n${await file.text()}`));
    const nextRaw = `${selected?.raw ?? ''}${blocks.join('')}`.trim();
    if (nextRaw.length > MAX_RAW_CHARS) { setError(text(`原始输入超过 ${MAX_RAW_CHARS.toLocaleString(locale)} 字符限制，请拆分或摘要`, `Raw input exceeds the ${MAX_RAW_CHARS.toLocaleString(locale)}-character limit. Split or summarize it.`)); return; }
    if (blocks.length) { update({ raw: nextRaw }); setTextImportCount((count) => count + textFiles.length); }
    setFiles(merged); setError('');
  };
  const extract = async () => {
    if (!selected || (!selected.raw.trim() && !files.length)) return;
    setExtracting(true); setError('');
    try {
      if (files.length) {
        const result = await manager.run(extractionPrompt, files);
        const generated = String(result?.reply || manager.output || '').trim();
        if (!generated) throw new Error(text('Argus 没有返回可用的知识提取结果', 'Argus did not return a usable knowledge extraction result'));
        update({ prompt: generated, changes: [text(`分析了 ${files.length} 个附件和原始输入`, `Analyzed ${files.length} attachments and the raw input`)], questions: [] });
        setFiles([]);
      } else {
        const result = await api.rewritePrompt(props.sid, extractionPrompt);
        if (result.error) throw new Error(result.error);
        update({ prompt: result.rewritten, changes: result.changes, questions: result.questions });
      }
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
    finally { setExtracting(false); }
  };
  const dispatch = async () => {
    if (!selected?.prompt.trim()) return;
    if (dispatchMode === 'new') {
      if (!newName.trim() || !confirm(text('确认用当前 Prompt 创建一个新的 Argus 项目？', 'Create a new Argus project with this prompt?'))) return;
      try { const result = await api.createDaemon(selected.prompt, newName, newWorkdir); update({ sentAt: Date.now() }); window.location.hash = `project/${result.sid}/overview`; }
      catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
      return;
    }
    if (!confirm(text('确认把这份第一版 Prompt 发送给当前 Argus 项目？', 'Send this first prompt to the current Argus project?'))) return;
    const result = await manager.run(selected.prompt);
    if (result) update({ sentAt: Date.now() });
  };
  const stage = selected?.sentAt ? 4 : selected?.prompt ? 3 : selected?.raw || files.length ? 2 : 1;

  return (
    <div className="ros-page inbox-v2">
      <header className="ros-page-header"><div><div className="eyebrow">RESEARCH INBOX</div><h1>{text('从零散输入开始研究', 'Start research from rough input')}</h1><p>{text('把消息、会议记录、文件或灵感交给 AI，提取知识点并形成第一版 Argus Prompt。', 'Give AI messages, meeting notes, files, or ideas to extract knowledge and create a first Argus prompt.')}</p></div><Badge tone="neutral"><Save size={12} />{text('本机自动保存', 'Saved locally')}</Badge></header>
      <div className="intake-steps">{([[text('收集原始内容', 'Collect input'), MessageSquareText], [text('AI 提取知识', 'Extract knowledge'), WandSparkles], [text('形成 Argus Prompt', 'Build Argus prompt'), FileText], [text('创建 / 发送项目', 'Create / send project'), Send]] as const).map(([label, Icon], index) => <div className={stage > index ? 'is-done' : stage === index + 1 ? 'is-active' : ''} key={String(label)}><span>{stage > index + 1 ? <Check size={14} /> : <Icon size={15} />}</span><strong>{String(label)}</strong>{index < 3 ? <ChevronRight size={14} /> : null}</div>)}</div>

      <div className="inbox-v2__layout">
        <aside className="ros-card inbox-sources"><header><div><span>INBOX</span><h2>{text('科研输入', 'Research input')}</h2></div><button className="icon-button" type="button" onClick={create} aria-label={text('新增输入', 'Add input')}><Plus size={15} /></button></header><div>{drafts.length ? drafts.map((item) => <button type="button" className={selected?.id === item.id ? 'is-active' : ''} key={item.id} onClick={() => setSelectedId(item.id)}><span className="inbox-item-icon">{item.sentAt ? <Check size={14} /> : item.prompt ? <Sparkles size={14} /> : <Inbox size={14} />}</span><div><strong>{item.title}</strong><small>{item.source} · {formatDate(item.updatedAt / 1_000, locale)}</small></div></button>) : <EmptyState icon={Inbox} title={text('暂无输入', 'No input yet')} description={text('新增一条导师消息、组会笔记或研究灵感。', 'Add an advisor message, meeting note, or research idea.')} />}</div></aside>

        <main className="ros-card inbox-input">
          <header><div><span>RAW MATERIAL</span><h2>{text('原始内容与附件', 'Raw content and attachments')}</h2></div>{selected ? <button className="icon-button" type="button" onClick={remove} aria-label={text('删除', 'Delete')}><Trash2 size={14} /></button> : null}</header>
          {selected ? <div className="inbox-input__form">
            <div className="form-grid"><label><span>{text('标题', 'Title')}</span><input value={selected.title} onChange={(event) => update({ title: event.target.value })} /></label><label><span>{text('来源', 'Source')}</span><input value={selected.source} onChange={(event) => update({ source: event.target.value })} /></label></div>
            <label className="field field--grow"><span>{text('零散消息、笔记或转写文本', 'Rough messages, notes, or transcripts')}</span><textarea maxLength={MAX_RAW_CHARS} value={selected.raw} onChange={(event) => update({ raw: event.target.value })} placeholder={text('不需要先整理，直接粘贴原始内容。AI 会区分目标、事实、约束、文献线索、待办和疑问…', 'Paste raw content directly. AI will separate goals, facts, constraints, evidence leads, tasks, and questions…')} /></label>
            {files.length ? <div className="inbox-attachment-list">{files.map((file, index) => <span key={`${file.name}-${index}`}>{file.type.startsWith('audio/') ? <AudioLines size={14} /> : file.type.startsWith('image/') ? <Image size={14} /> : <FileText size={14} />}<div><strong>{file.name}</strong><small>{(file.size / 1024 / 1024).toFixed(1)} MB · {text('仅在本次分析上传', 'Uploaded only for this analysis')}</small></div><button type="button" onClick={() => setFiles((rows) => rows.filter((_, rowIndex) => rowIndex !== index))}><X size={13} /></button></span>)}</div> : null}
            <div className="inbox-upload-types"><span><FileText size={14} />PDF / {text('文本', 'text')}</span><span><Image size={14} />{text('图片', 'images')}</span><span><AudioLines size={14} />{text('语音', 'audio')}</span><p>{text('语音会交给 Argus 和已配置工具处理，不把“上传成功”冒充“已完成转写”。', 'Audio is handed to Argus and configured tools; an upload is never presented as a completed transcript.')}</p></div>
            <div className="inbox-input__actions">
              <label className="button button--secondary file-button"><Upload size={14} />{text('添加文件', 'Add files')}<input type="file" multiple accept=".txt,.md,.markdown,.json,.csv,.yaml,.yml,.log,.tex,.pdf,.png,.jpg,.jpeg,.webp,.wav,.mp3,.m4a,.ogg" onChange={(event) => void addFiles(Array.from(event.target.files ?? []))} /></label>
              <button className="button button--primary" type="button" disabled={(!selected.raw.trim() && !files.length) || extracting || manager.busy} onClick={() => void extract()}>{extracting || manager.busy ? <Sparkles size={14} /> : <WandSparkles size={14} />}{extracting || manager.busy ? manager.phase || text('AI 正在分析', 'AI is analyzing') : text('分析内容并生成 Prompt', 'Analyze and generate prompt')}</button>
            </div>
            {error ? <div className="inline-error">{error}</div> : null}{storageError ? <div className="inline-error">{storageError}</div> : null}
          </div> : <EmptyState icon={Inbox} title={text('选择或新增一条科研输入', 'Select or add research input')} />}
        </main>

        <aside className="inbox-output">
          <section className="ros-card knowledge-panel"><header><div><span>KNOWLEDGE EXTRACTION</span><h2>{text('AI 提取的知识点', 'AI-extracted knowledge')}</h2></div>{knowledge.length ? <Badge tone="success">{knowledge.length} {text('组', 'groups')}</Badge> : null}</header>{knowledge.length ? <div className="knowledge-grid">{knowledge.map((item) => { const Icon = item.icon; return <article key={item.title}><span><Icon size={15} /></span><div><strong>{item.title}</strong><Markdown>{item.body}</Markdown></div></article>; })}</div> : <EmptyState icon={Lightbulb} title={text('等待 AI 提取', 'Waiting for AI extraction')} description={text('结果会明确区分目标、知识点、约束、证据线索和待确认问题。', 'The result separates goals, knowledge, constraints, evidence leads, and open questions.')} />}</section>
          <section className="ros-card first-prompt"><header><div><span>FIRST ARGUS PROMPT</span><h2>{text('第一版 Argus Prompt', 'First Argus prompt')}</h2></div>{selected?.prompt ? <Badge tone="info">{text('可编辑', 'Editable')}</Badge> : null}</header>{selected?.prompt ? <><textarea value={selected.prompt} onChange={(event) => update({ prompt: event.target.value })} /><div className="dispatch-mode"><button type="button" className={dispatchMode === 'current' ? 'is-active' : ''} onClick={() => setDispatchMode('current')}>{text('发送当前项目', 'Send to current project')}</button><button type="button" className={dispatchMode === 'new' ? 'is-active' : ''} onClick={() => setDispatchMode('new')}>{text('创建新项目', 'Create new project')}</button></div>{dispatchMode === 'new' ? <div className="new-project-fields"><input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder={text('新项目名称', 'New project name')} /><input value={newWorkdir} onChange={(event) => setNewWorkdir(event.target.value)} placeholder={text('工作目录（可选，留空自动创建）', 'Workdir (optional; blank creates one)')} /></div> : null}<button className="button button--primary button--full" type="button" disabled={manager.busy} onClick={() => void dispatch()}><Send size={14} />{manager.busy ? manager.phase || text('正在发送', 'Sending') : dispatchMode === 'new' ? text('用此 Prompt 创建 Argus 项目', 'Create Argus project with this prompt') : text('确认并发送给当前 Argus', 'Confirm and send to current Argus')}</button>{manager.output ? <div className="manager-mini-result"><Markdown>{manager.output}</Markdown></div> : null}</> : <EmptyState icon={FileText} title={text('尚未生成 Prompt', 'No prompt generated')} description={text('AI 提取后会在这里生成第一版 Prompt，你可以先修改再发送。', 'The first prompt appears here after extraction and can be edited before sending.')} />}</section>
        </aside>
      </div>
    </div>
  );
}
