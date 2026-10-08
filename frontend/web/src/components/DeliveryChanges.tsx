import { useState } from 'react';
import { api } from '../api';

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
    }))).catch((err: unknown) => setState((s) => ({ ...s, loading: false, error: err instanceof Error ? err.message : String(err) })));
  };
  return (
    <div className="delivery-changes">
      <button type="button" aria-expanded={state.open} onClick={toggle}>
        {state.open ? (zh ? '收起改动' : 'Hide changes') : (zh ? '查看与上一次交付相比的改动' : 'Show changes since the previous delivery')}
      </button>
      {state.open && (state.loading ? <p>{zh ? '正在读取…' : 'Loading…'}</p>
        : state.error ? <p>{state.error}</p>
          : <pre aria-label={zh ? '改动' : 'Changes'}>{state.diff.split('\n').map((line, index) => (
            <span key={index} className={line.startsWith('+') && !line.startsWith('+++') ? 'add' : line.startsWith('-') && !line.startsWith('---') ? 'del' : undefined}>{line}{'\n'}</span>
          ))}{state.truncated ? (zh ? '…（改动过长，已截断）' : '… (truncated)') : ''}</pre>)}
    </div>
  );
}
