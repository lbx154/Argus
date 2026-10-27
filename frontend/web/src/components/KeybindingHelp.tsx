import { Modal, ModalHeader } from './Modal';
import { COMMANDS } from '../../../core/src/commands';
import { useI18n } from '../i18n';
import { localizedHelpGroups } from '../lib/commandI18n';

const BINDINGS: { keys: string; desc: string }[] = [
  { keys: '⌘K / Ctrl+K', desc: 'help.palette' },
  { keys: '⌘B / Ctrl+B', desc: 'help.sessions' },
  { keys: '⌘J / Ctrl+J', desc: 'help.managerChat' },
  { keys: '⌘R / Ctrl+R', desc: 'help.rewrite' },
  { keys: '⌘T / Ctrl+T', desc: 'help.reasoning' },
  { keys: '⌘. / Ctrl+.', desc: 'help.kiosk' },
  { keys: '/', desc: 'help.composer' },
  { keys: '@ / #', desc: 'help.mention' },
  { keys: 'Tab', desc: 'help.acceptSuggestion' },
  { keys: '↵ Enter', desc: 'help.send' },
  { keys: 'Shift+Enter', desc: 'help.newline' },
  { keys: '?', desc: 'help.thisHelp' },
  { keys: 'Esc', desc: 'help.escape' },
];

/** ? keybinding help overlay — keyboard shortcuts at top, full command reference below. */
export function KeybindingHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { locale, t } = useI18n();
  const groups = localizedHelpGroups(COMMANDS, locale);
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
        <div className="border-t border-line px-4 pb-4 pt-3">
          <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-ink-faint">{t('help.commands')}</p>
          {groups.map((group) => (
            <div key={group.group} className="mb-4">
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">{group.group}</p>
              {group.rows.map((row) => (
                <div key={row.label} className="flex items-start justify-between gap-4 py-1">
                  <code className="shrink-0 font-mono text-xs text-ink">{row.label}</code>
                  <span className="text-right text-xs text-ink-dim">{row.desc}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </Modal>
  );
}
