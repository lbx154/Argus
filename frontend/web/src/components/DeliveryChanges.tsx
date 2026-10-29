import { errorText } from '../lib/format';
import { useState } from 'react';
import { api } from '../api';
import './delivery.css';

/** A unified diff as a reader takes it: each change headed by the line it starts on, the lines it
 * adds and drops marked, without the diff's own headers. */
export function DiffLines({ diff, truncated, zh }: { diff: string; truncated: boolean; zh: boolean }) {
  return <pre className="diff-lines" aria-label={zh ? '改动' : 'Changes'}>{diff.split('\n').filter(line => line && !/^(?:---|\+\+\+) /.test(line)).map((line, index) => {
    const at = /^@@ -\d+(?:,\d+)? \+(\d+)/.exec(line)?.[1];
    return at ? <span key={index} className="hunk">{zh ? `第 ${at} 行` : `Line ${at}`}</span>
      : <span key={index} className={line[0] === '+' ? 'add' : line[0] === '-' ? 'del' : undefined}>{line}{'\n'}</span>;
  })}{truncated ? (zh ? '…（改动过长，已截断）' : '… (truncated)') : ''}</pre>;
}

/** Collapsed "what changed since the last delivery" view for one redelivered file. */
export function DeliveryChanges({ sid, change, zh }: { sid: string; change: { path: string; before: string; after: string }; zh: boolean }) {
  const [state, setState] = useState<{ open: boolean; loading: boolean; diff: string; error: string; truncated: boolean }>({ open: false, loading: false, diff: '', error: '', truncated: false });
  const toggle = () => {
    if (state.open) { setState((s) => ({ ...s, open: false })); return; }
    setState((s) => ({ ...s, open: true, loading: !s.diff && !s.error }));
    if (state.diff || state.error) return;
    api.deliveryDiff(sid, change).then((view) => setState((s) => ({
      ...s, loading: false, truncated: view.truncated,
      diff: view.available ? view.diff : '',
      error: view.available ? (view.diff ? '' : (zh ? '内容没有变化' : 'No content changes')) : (zh ? '上一版已不再保存，无法对比' : 'The previous version is no longer kept'),
    }))).catch((err: unknown) => setState((s) => ({ ...s, loading: false, error: errorText(err, zh ? "zh-CN" : "en") })));
  };
  return (
    <div className="delivery-changes">
      <button type="button" aria-expanded={state.open} onClick={toggle}>
        {state.open ? (zh ? '收起改动' : 'Hide changes') : (zh ? '查看与上一次交付相比的改动' : 'Show changes since the previous delivery')}
      </button>
      {state.open && (state.loading ? <p>{zh ? '正在读取…' : 'Loading…'}</p>
        : state.error ? <p>{state.error}</p>
          : <DiffLines diff={state.diff} truncated={state.truncated} zh={zh} />)}
    </div>
  );
}
