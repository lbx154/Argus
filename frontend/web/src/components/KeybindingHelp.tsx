import { Modal, ModalHeader } from './Modal';
import { helpGroups } from '../../../core/src/commands';

const BINDINGS: { keys: string; desc: string }[] = [
  { keys: '⌘K / Ctrl+K', desc: 'command palette' },
  { keys: '⌘B / Ctrl+B', desc: 'toggle sessions' },
  { keys: '⌘J / Ctrl+J', desc: 'focus Manager chat' },
  { keys: '⌘T / Ctrl+T', desc: 'toggle agent reasoning' },
  { keys: '⌘. / Ctrl+.', desc: 'toggle kiosk (read-only) mode' },
  { keys: '/', desc: 'focus the composer' },
  { keys: '↵ Enter', desc: 'send message' },
  { keys: 'Shift+Enter', desc: 'insert newline' },
  { keys: '?', desc: 'this help' },
  { keys: 'Esc', desc: 'close overlay / stop waiting in composer' },
];

/** ? keybinding help overlay — keyboard shortcuts at top, full command reference below. */
export function KeybindingHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  const groups = helpGroups();
  return (
    <Modal open={open} onClose={onClose} label="Keyboard shortcuts" width="max-w-2xl">
      <ModalHeader title="Keyboard shortcuts" />
      <div className="max-h-[70dvh] overflow-y-auto scroll-thin">
        <div className="p-4">
          {BINDINGS.map((b) => (
            <div key={b.keys} className="flex items-center justify-between py-1.5">
              <span className="text-sm text-ink-dim">{b.desc}</span>
              <kbd className="rounded border border-line bg-surface px-2 py-0.5 font-mono text-[11px] text-ink">
                {b.keys}
              </kbd>
            </div>
          ))}
        </div>
        <div className="border-t border-line px-4 pb-4 pt-3">
          <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-ink-faint">Commands</p>
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
