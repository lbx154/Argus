import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { Modal } from './Modal';
import { isImeComposing } from '../lib/ime';
import { useI18n } from '../i18n';

/** Deliberate project creation: blank means an idle conversation; objective starts now. */
export function NewDaemonModal({
  open,
  busy,
  onClose,
  onCreate,
}: {
  open: boolean;
  busy: boolean;
  onClose: () => void;
  onCreate: (name: string, objective: string, workdir: string) => Promise<boolean>;
}) {
  const { t } = useI18n();
  const [name, setName] = useState('');
  const [objective, setObjective] = useState('');
  const [workdir, setWorkdir] = useState('');
  const formRef = useRef<HTMLFormElement>(null);
  useEffect(() => {
    if (open) {
      setName('');
      setObjective('');
      setWorkdir('');
    }
  }, [open]);

  const close = () => {
    if (!busy) onClose();
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (await onCreate(name.trim(), objective.trim(), workdir.trim())) onClose();
  };
  const objectiveKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (isImeComposing(event)) return;
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      formRef.current?.requestSubmit();
    }
  };
  const armed = Boolean(objective.trim());

  return (
    <Modal open={open} onClose={close} label={t('new.createDaemon')} width="max-w-xl" showClose={false}>
      <form ref={formRef} onSubmit={(event) => void submit(event)}>
        <div className="flex items-start gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold text-ink">{t('landing.new')}</h2>
            <p className="mt-0.5 text-xs text-ink-faint">{t('new.subtitle')}</p>
          </div>
          <button type="button" aria-label={t('new.close')} onClick={close} disabled={busy} className="rounded px-2 py-1 text-lg leading-none text-ink-faint hover:bg-surface hover:text-ink disabled:opacity-40">×</button>
        </div>
        <div className="space-y-4 p-5">
          <label className="block">
            <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-ink-faint">{t('new.name')} <span className="normal-case tracking-normal">{t('new.optional')}</span></span>
            <input
              data-autofocus
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={80}
              disabled={busy}
              placeholder={t('new.namePlaceholder')}
              className="h-10 w-full rounded border border-line bg-bg/50 px-3 text-sm text-ink outline-none placeholder:text-ink-faint focus:border-blue-deep disabled:opacity-50"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-ink-faint">{t('new.workdir')} <span className="normal-case tracking-normal">{t('new.optional')}</span></span>
            <input
              value={workdir}
              onChange={(event) => setWorkdir(event.target.value)}
              disabled={busy}
              placeholder={t('newDaemon.workdirPlaceholder')}
              className="h-10 w-full rounded border border-line bg-bg/50 px-3 font-mono text-xs text-ink outline-none placeholder:text-ink-faint focus:border-blue-deep disabled:opacity-50"
            />
            <span className="mt-1 block text-[10px] leading-relaxed text-ink-faint">{t('new.workdirHint')}</span>
          </label>
          <label className="block">
            <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-ink-faint">{t('new.objective')} <span className="normal-case tracking-normal">{t('new.optional')}</span></span>
            <textarea
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              onKeyDown={objectiveKey}
              maxLength={4000}
              disabled={busy}
              rows={4}
              placeholder={t('new.objectivePlaceholder')}
              className="w-full resize-y rounded border border-line bg-bg/50 px-3 py-2.5 text-sm leading-relaxed text-ink outline-none placeholder:text-ink-faint focus:border-blue-deep disabled:opacity-50"
            />
          </label>
          <div className={`rounded border p-3 ${armed ? 'border-gold/40 bg-gold/5' : 'border-line bg-bg/30'}`}>
            <div className={`text-xs font-medium ${armed ? 'text-gold' : 'text-blue-sky'}`}>
              {armed ? t('new.startsAfterCreate') : t('new.idleUntilMessage')}
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-ink-faint">
              {armed
                ? t('new.startsHint')
                : t('new.idleHint')}
            </p>
          </div>
        </div>
        <div className="flex items-center justify-between gap-3 border-t border-line px-5 py-3">
          <span className="text-[10px] text-ink-faint">{t('new.shortcut')}</span>
          <div className="flex gap-2">
            <button type="button" onClick={close} disabled={busy} className="rounded border border-line px-3 py-1.5 text-xs text-ink-dim hover:bg-surface disabled:opacity-40">{t('common.cancel')}</button>
            <button type="submit" disabled={busy} className="rounded border border-blue/35 bg-blue/8 px-3 py-1.5 text-xs font-medium text-blue hover:border-blue-deep hover:bg-blue-deep hover:text-white disabled:cursor-wait disabled:opacity-50">
              {busy ? t('new.creating') : armed ? t('new.createAndStart') : t('sidebar.create')}
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
}
