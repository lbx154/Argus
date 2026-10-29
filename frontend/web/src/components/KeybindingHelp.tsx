import { Modal, ModalHeader } from './Modal';
import { useI18n } from '../i18n';
import { shortcut } from '../lib/format';

const BINDINGS: { keys: string; desc: string }[] = [
  { keys: shortcut('K'), desc: 'help.palette' },
  { keys: shortcut('B'), desc: 'help.sessions' },
  { keys: `/ · ${shortcut('J')}`, desc: 'help.composer' },
  { keys: '@ / #', desc: 'help.mention' },
  { keys: 'Tab', desc: 'help.acceptSuggestion' },
  { keys: '↵ Enter', desc: 'help.send' },
  { keys: 'Shift+Enter', desc: 'help.newline' },
  { keys: '?', desc: 'help.thisHelp' },
  { keys: 'Esc', desc: 'help.escape' },
];

/** ? keybinding help overlay — keyboard shortcuts. */
export function KeybindingHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  return (
    <Modal open={open} onClose={onClose} label={t('help.title')} width="max-w-2xl">
      <ModalHeader title={t('help.title')} />
      <div className="max-h-[70dvh] overflow-y-auto scroll-thin">
        <div className="p-4">
          {BINDINGS.map((b) => (
            <div key={b.keys} className="flex items-center justify-between py-1.5">
              <span className="text-sm text-ink-dim">{t(b.desc)}</span>
              <kbd className="rounded border border-line bg-surface px-2 py-0.5 font-mono text-[11px] text-ink">
                {b.keys}
              </kbd>
            </div>
          ))}
        </div>

      </div>
    </Modal>
  );
}
