import { useEffect, useRef, useState } from 'react';
import { attentionBadge, type Attention } from './lib/attention';

/**
 * The tab title says what needs the operator: a reply keeps its badge until
 * answered; results badge only while the tab is in the background.
 */
export function useAttentionSignals({ projectName, attention, zh, running = false }: {
  projectName: string;
  attention: Attention | null;
  zh: boolean;
  /** The project is working: say so in the tab when nothing needs the operator. */
  running?: boolean;
}) {
  const [hidden, setHidden] = useState(() => typeof document !== 'undefined' && document.hidden);
  const baseTitle = useRef(typeof document !== 'undefined' && document.title || 'Argus');

  useEffect(() => {
    const update = () => setHidden(document.hidden);
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);

  useEffect(() => {
    const base = projectName || baseTitle.current;
    const badge = attention && (attention.kind === 'reply' || hidden) ? attentionBadge(attention.kind, zh) : '';
    document.title = badge ? `(${badge}) ${base}`
      : running ? `● ${zh ? '运行中' : 'Running'} · ${base}` : base;
    return () => { document.title = baseTitle.current; };
  }, [attention, hidden, projectName, running, zh]);
}
