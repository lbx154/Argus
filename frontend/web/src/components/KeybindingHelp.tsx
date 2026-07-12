import { Modal, ModalHeader } from './Modal';

const BINDINGS: { keys: string; desc: string }[] = [
  { keys: '⌘K / Ctrl+K', desc: 'command palette' },
  { keys: '⌘B / Ctrl+B', desc: 'toggle sessions' },
  { keys: '⌘J / Ctrl+J', desc: 'focus Manager chat' },
  { keys: '⌘O / Ctrl+O', desc: 'toggle agent reasoning' },
  { keys: '⌘. / Ctrl+.', desc: 'toggle kiosk (read-only) mode' },
  { keys: '/', desc: 'focus the composer' },
  { keys: '↵ Enter', desc: 'send message' },
  { keys: 'Shift+Enter', desc: 'insert newline' },
  { keys: '?', desc: 'this help' },
  { keys: 'Esc', desc: 'close overlay / stop waiting in composer' },
];

/** ? keybinding help overlay. */
export function KeybindingHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} label="Keyboard shortcuts" width="max-w-md">
      <ModalHeader title="Keyboard shortcuts" />
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
    </Modal>
  );
}
