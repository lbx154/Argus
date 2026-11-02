import { useState } from 'react';
import { Button } from './primitives';
import { FIRST_NEEDS, NEEDS, needLabel, utcTime, type Need } from '../lib/daily';

/** The chief before it has begun: the operator ticks what they need and when its letter comes; nothing to type. */
export function ChiefNeeds({ zh, onStart }: {
  zh: boolean;
  /** Begins the chief: the needs ticked, the directions if any, and the daily time in UTC (HH:MM). */
  onStart: (needs: Need[], directions: string, at: string) => Promise<boolean>;
}) {
  const [needs, setNeeds] = useState<Need[]>([...FIRST_NEEDS]);
  const [time, setTime] = useState('08:00');
  const [directions, setDirections] = useState('');
  const [busy, setBusy] = useState(false);
  const toggle = (need: Need) => setNeeds(current => current.includes(need) ? current.filter(item => item !== need) : NEEDS.filter(item => item === need || current.includes(item)));
  const at = utcTime(time);
  const start = async () => {
    if (busy || !at || !needs.length) return;
    setBusy(true);
    try { await onStart(needs, directions, at); } finally { setBusy(false); }
  };
  return (
    <section className="chief-needs mx-auto max-w-2xl px-5 pb-8 pt-[8vh]">
      <h2 className="font-serif text-[28px] font-medium leading-tight tracking-[-0.01em] text-ink">{zh ? '你的总负责人' : 'Your chief'}</h2>
      <p className="mt-2 text-sm leading-6 text-ink-faint">{zh
        ? '它照看你所有的项目，盯着你关心的方向，有值得说的会主动来找你。勾选你需要的就行。'
        : 'It looks after all your projects, follows your directions, and comes to you when something is worth it. Tick what you need.'}</p>
      <div className="chief-needs-list" role="group" aria-label={zh ? '你需要它做什么' : 'What you need'}>
        {NEEDS.map(need => (
          <label key={need} className={needs.includes(need) ? 'chief-need chief-need-on' : 'chief-need'}>
            <input type="checkbox" checked={needs.includes(need)} onChange={() => toggle(need)} disabled={busy} />
            <span>{needLabel(zh, need)}</span>
          </label>
        ))}
      </div>
      <div className="chief-needs-row">
        <label>{zh ? '每天来信' : 'Daily letter at'}
          <input type="time" value={time} onChange={event => setTime(event.target.value)} disabled={busy} />
        </label>
        <input className="chief-needs-directions" value={directions} onChange={event => setDirections(event.target.value)} disabled={busy}
          placeholder={zh ? '关心的方向（可不填，它会从你的项目里推断）' : 'Your directions (optional: it infers them from your projects)'} />
      </div>
      <Button variant="primary" className="mt-4" onClick={() => void start()} disabled={busy || !at || !needs.length}>
        {busy ? (zh ? '正在开始…' : 'Starting…') : (zh ? '开始' : 'Start')}
      </Button>
    </section>
  );
}
