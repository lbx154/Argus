import { useDoctor, useConfig, useIdentity, useTranscript } from '../hooks';
import type { ConfigKnob } from '../api';
import { Modal, ModalHeader } from './Modal';
import { Spinner, EmptyHint } from './primitives';
import { effortColor } from '../lib/theme';
import { ago } from '../lib/format';

/** Doctor: health checks with the single recommended fix pinned + gold, plus the
 *  daemon.log tail. Read-only diagnostics. */
export function DoctorModal({ sid, open, onClose }: { sid: string; open: boolean; onClose: () => void }) {
  const { data, isLoading } = useDoctor(sid, open);
  return (
    <Modal open={open} onClose={onClose} label="Doctor" width="max-w-3xl">
      <ModalHeader title="Doctor" sub="daemon health checks + recommended root-cause fix" />
      <div className="max-h-[64vh] overflow-y-auto scroll-thin p-4">
        {isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
        {data?.recommended && (
          <div className="mb-4 rounded-lg border border-gold/40 bg-gold/5 p-3">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-gold">recommended fix</div>
            <div className="mt-1 text-sm text-ink">{data.recommended.name}</div>
            <div className="mt-0.5 text-xs text-ink-dim">{data.recommended.detail}</div>
            {data.recommended.fix && (
              <pre className="mt-2 whitespace-pre-wrap break-words rounded bg-bg p-2 font-mono text-xs text-blue-sky">{data.recommended.fix}</pre>
            )}
          </div>
        )}
        <div className="space-y-1.5">
          {(data?.checks ?? []).map((c, i) => (
            <div key={i} className="flex items-start gap-2 rounded-md border border-line/60 px-3 py-2">
              <span className={c.ok ? 'text-ok' : 'text-err'}>{c.ok ? '✓' : '✗'}</span>
              <div className="min-w-0">
                <div className="text-xs font-medium text-ink">{c.name}</div>
                {c.detail && <div className="mt-0.5 text-[11px] text-ink-dim">{c.detail}</div>}
                {!c.ok && c.fix && (
                  <pre className="mt-1 whitespace-pre-wrap break-words rounded bg-bg p-1.5 font-mono text-xs text-ink-dim">{c.fix}</pre>
                )}
              </div>
            </div>
          ))}
        </div>
        {data?.log_tail && (
          <div className="mt-4">
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">daemon.log</div>
            <pre className="max-h-48 overflow-x-hidden overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-bg p-3 font-mono text-xs leading-relaxed text-ink-dim scroll-thin">{data.log_tail}</pre>
          </div>
        )}
      </div>
    </Modal>
  );
}

/** Config: per-role backend·model·effort + knobs. READ-ONLY — there is no PATCH
 *  endpoint and the backend is intentionally unchanged, so this is presented
 *  honestly as "set via env / restart to apply". */
export function ConfigModal({ sid, open, onClose }: { sid: string; open: boolean; onClose: () => void }) {
  const { data, isLoading } = useConfig(sid, open);
  const knobGroups = (data?.operator_knobs ?? []).reduce<Record<string, ConfigKnob[]>>(
    (groups, knob) => {
      (groups[knob.group] ??= []).push(knob);
      return groups;
    },
    {},
  );
  return (
    <Modal open={open} onClose={onClose} label="Runtime settings" width="max-w-4xl">
      <ModalHeader title="Runtime settings" sub={data ? `resolved ${data.generated_at_utc}` : 'resolved role and operator settings'} />
      <div className="max-h-[64vh] overflow-y-auto scroll-thin p-4">
        {isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
        <div className="grid gap-2 sm:grid-cols-2">
          {(data?.roles ?? []).map((r) => (
            <div key={r.role} className="rounded-lg border border-line bg-surface p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="text-xs font-semibold capitalize text-ink">{r.role}</div>
                <span className="text-[10px] text-ink-faint">{r.backend_label}</span>
              </div>
              <div className="mt-2 truncate font-mono text-[11px] text-ink-dim" title={r.model}>{r.model}</div>
              <div className="mt-1 flex items-center gap-2 text-[10px] text-ink-faint">
                <span className="truncate" title={r.model_source}>{r.model_source}</span>
                {r.reasoning_effort && (
                  <span className="ml-auto shrink-0" style={{ color: effortColor(r.reasoning_effort) }}>
                    {r.reasoning_effort}
                  </span>
                )}
              </div>
              {r.description ? <p className="mt-2 text-[10px] leading-relaxed text-ink-faint">{r.description}</p> : null}
            </div>
          ))}
        </div>
        {Object.entries(knobGroups).map(([group, knobs]) => (
          <section key={group} className="mt-5">
            <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">{group}</div>
            <div className="overflow-hidden rounded-lg border border-line">
              {knobs.map((knob, index) => (
                <div key={knob.name} className={`grid gap-1 px-3 py-2.5 sm:grid-cols-[minmax(0,1fr)_auto] ${index ? 'border-t border-line/60' : ''}`}>
                  <div className="min-w-0">
                    <div className="truncate font-mono text-[11px] text-ink-dim" title={knob.name}>{knob.name}</div>
                    <div className="mt-0.5 text-[10px] leading-relaxed text-ink-faint">{knob.doc}</div>
                  </div>
                  <div className="text-left sm:text-right">
                    <div className="font-mono text-[11px] text-ink">{knob.value}</div>
                    <div className="mt-0.5 text-[9px] text-ink-faint">{knob.source}</div>
                  </div>
                </div>
              ))}
            </div>
          </section>
        ))}
      </div>
    </Modal>
  );
}

/** Identity: the operator identity text on a wordmarked panel. */
export function IdentityModal({ sid, open, onClose }: { sid: string; open: boolean; onClose: () => void }) {
  const { data, isLoading } = useIdentity(sid, open);
  return (
    <Modal open={open} onClose={onClose} label="Identity" width="max-w-2xl">
      <ModalHeader title="Identity" sub="who argus is working for on this project" />
      <div className="max-h-[64vh] overflow-y-auto scroll-thin p-5">
        {isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
        {data ? (
          <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-ink-dim">{data}</pre>
        ) : (
          !isLoading && <EmptyHint>no identity configured</EmptyHint>
        )}
      </div>
    </Modal>
  );
}

/** Transcript: recent operator↔argus turns for replay/resume. Reply via the
 *  composer (nudge/note) — this pane is read-only history. */
export function TranscriptModal({ sid, open, onClose }: { sid: string; open: boolean; onClose: () => void }) {
  const { data, isLoading } = useTranscript(sid, open);
  const turns = data ?? [];
  return (
    <Modal open={open} onClose={onClose} label="Transcript" width="max-w-2xl">
      <ModalHeader title="Transcript" sub="recent operator ↔ argus turns · reply from the composer" />
      <div className="max-h-[64vh] overflow-y-auto scroll-thin p-4">
        {isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
        {!isLoading && turns.length === 0 && <EmptyHint>no conversation turns yet</EmptyHint>}
        {turns.map((t, i) => {
          const me = t.role === 'operator';
          return (
            <div key={i} className="grid grid-cols-[72px_minmax(0,1fr)] border-b border-line/50 py-2.5 last:border-b-0">
              <div>
                <div className={`font-mono text-[10px] font-semibold uppercase tracking-wide ${me ? 'text-ink-faint' : 'text-blue-sky'}`}>
                  {me ? 'operator' : 'argus'}
                </div>
                <div className="mt-0.5 text-[9px] text-ink-faint">{ago(t.ts)}</div>
              </div>
              <div className="whitespace-pre-wrap text-sm leading-relaxed text-ink-dim">{t.text}</div>
            </div>
          );
        })}
      </div>
    </Modal>
  );
}
