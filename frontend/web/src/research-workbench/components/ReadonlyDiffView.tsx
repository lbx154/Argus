import { useMemo, useState } from 'react';
import { CopyButton } from '../../components/CopyButton';
import { useWorkbenchText } from '../useWorkbenchText';
import './ReadonlyDiffView.css';

export const MAX_DIFF_CHARS = 200_000;
export const MAX_DIFF_LINES = 3_000;
type DiffKind = 'header' | 'hunk' | 'addition' | 'deletion' | 'context';
export interface DiffLine { kind: DiffKind; text: string }

/** A bounded display classifier, not a patch applier. Unknown formats stay raw.
 * Each text segment retains its original newline (including CRLF and no EOF LF). */
export function classifyDiff(diff: string): DiffLine[] | null {
  if (!diff || diff.length > MAX_DIFF_CHARS || diff.includes('\0')) return null;
  if (!diff.startsWith('diff --git ') && !/^--- [^\n]+\n\+\+\+ /.test(diff)) return null;
  if (/^(?:Binary files |GIT binary patch)/m.test(diff)) return null;
  const lines = diff.match(/[^\n]*\n|[^\n]+$/g) ?? [];
  if (lines.length > MAX_DIFF_LINES) return null;
  const result: DiffLine[] = [];
  let oldRemaining = 0;
  let newRemaining = 0;
  for (const raw of lines) {
    const line = raw.replace(/\r?\n$/, '');
    let kind: DiffKind;
    const hunk = /^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@(?:.*)$/.exec(line);
    if (line.startsWith('diff --git ')) {
      kind = 'header'; oldRemaining = 0; newRemaining = 0;
    } else if (hunk) {
      kind = 'hunk'; oldRemaining = Number(hunk[1] ?? 1); newRemaining = Number(hunk[2] ?? 1);
      if (!Number.isSafeInteger(oldRemaining) || !Number.isSafeInteger(newRemaining)) return null;
    } else if (line === '\\ No newline at end of file') {
      kind = 'header';
    } else if (oldRemaining || newRemaining) {
      if (line.startsWith('+')) { kind = 'addition'; newRemaining--; }
      else if (line.startsWith('-')) { kind = 'deletion'; oldRemaining--; }
      else if (line.startsWith(' ')) { kind = 'context'; oldRemaining--; newRemaining--; }
      else return null;
      if (oldRemaining < 0 || newRemaining < 0) return null;
    } else if (/^(?:--- |\+\+\+ |index |old mode |new mode |new file mode |deleted file mode |similarity index |dissimilarity index |rename from |rename to |copy from |copy to )/.test(line)) {
      kind = 'header';
    } else return null;
    result.push({ kind, text: raw });
  }
  return result;
}

export function ReadonlyDiffView({ diff, truncated = false }: { diff: string; truncated?: boolean }) {
  const { text } = useWorkbenchText();
  const lines = useMemo(() => classifyDiff(diff), [diff]);
  const [raw, setRaw] = useState(false);
  const colored = lines !== null && !raw;
  return <section className="readonly-diff" data-readonly-diff>
    <div className="readonly-diff__toolbar">
      <strong>{text('当前工作区差异', 'Current workspace diff')}</strong>
      {lines && <button type="button" aria-pressed={raw} onClick={() => setRaw(value => !value)}>
        {raw ? text('着色视图', 'Highlighted view') : text('原始文本', 'Original text')}
      </button>}
      {diff && <CopyButton text={diff} label={text('复制原始差异', 'Copy original diff')} copiedLabel={text('已复制', 'Copied')} />}
    </div>
    <p className="readonly-diff__hint">{text('不代表所有变更都来自当前 Agent 或任务。', 'Changes are not necessarily from this Agent or task.')}</p>
    {truncated && <p role="status" className="readonly-diff__notice">{text('后端返回的差异已截断，当前内容不完整。', 'The backend truncated this diff; the displayed content is incomplete.')}</p>}
    {!diff ? <p className="readonly-diff__hint">{text('没有工作区差异。', 'No workspace diff.')}</p>
      : !lines ? <p className="readonly-diff__hint">{text('无法识别、二进制或较大差异：显示原始文本。', 'Unrecognized, binary or large diff: showing original text.')}</p> : null}
    <pre className="readonly-diff__text" tabIndex={0} aria-label={text('工作区差异文本', 'Workspace diff text')} data-diff-mode={colored ? 'highlighted' : 'raw'}>
      {colored ? lines.map((line, index) => <span key={index} data-diff-kind={line.kind} className={`readonly-diff__line--${line.kind}`}>{line.text}</span>) : diff}
    </pre>
  </section>;
}
