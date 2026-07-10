import { useEffect, useMemo, useRef, useState } from 'react';
import { Modal } from './Modal';

export interface PaletteItem {
  id: string;
  label: string;
  hint?: string;
  group: string;
  keywords?: string;
  run: () => void;
}

export function filterPaletteItems(items: PaletteItem[], query: string): PaletteItem[] {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return items;
  return items.filter((item) => {
    const text = `${item.label} ${item.group} ${item.hint ?? ''} ${item.keywords ?? ''}`.toLowerCase();
    return terms.every((term) => text.includes(term));
  });
}

/** ⌘K command palette — fuzzy-filtered, keyboard-driven, mirrors the CLI slash
 *  registry (navigation + every mutation + project switch). */
export function CommandPalette({
  open,
  onClose,
  items,
}: {
  open: boolean;
  onClose: () => void;
  items: PaletteItem[];
}) {
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const activeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (open) {
      setQ('');
      setSel(0);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  const filtered = useMemo(() => {
    return filterPaletteItems(items, q);
  }, [q, items]);

  useEffect(() => {
    if (sel >= filtered.length) setSel(Math.max(0, filtered.length - 1));
  }, [filtered.length, sel]);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest' });
  }, [open, q, sel]);

  const choose = (it: PaletteItem | undefined) => {
    if (!it) return;
    onClose();
    it.run();
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (filtered.length) setSel((s) => Math.min(filtered.length - 1, s + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSel((s) => Math.max(0, s - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      choose(filtered[sel]);
    }
  };

  // group in display order, preserving first-seen grouping
  const groups: { name: string; items: PaletteItem[] }[] = [];
  for (const it of filtered) {
    let g = groups.find((x) => x.name === it.group);
    if (!g) {
      g = { name: it.group, items: [] };
      groups.push(g);
    }
    g.items.push(it);
  }
  let flatIndex = -1;

  return (
    <Modal open={open} onClose={onClose} label="Command palette" width="max-w-xl" align="top">
      <div className="border-b border-line px-4 py-3">
        <input
          ref={inputRef}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKey}
          placeholder="Type a command or search…"
          role="combobox"
          aria-expanded={open}
          aria-autocomplete="list"
          aria-controls="command-palette-results"
          aria-activedescendant={filtered[sel] ? `palette-${filtered[sel].id}` : undefined}
          className="w-full bg-transparent font-mono text-sm text-ink outline-none placeholder:text-ink-faint"
        />
      </div>
      <div id="command-palette-results" role="listbox" className="max-h-[52vh] overflow-y-auto scroll-thin py-1.5">
        {filtered.length === 0 && (
          <div className="px-4 py-6 text-center text-xs text-ink-faint">no matching commands</div>
        )}
        {groups.map((g) => (
          <div key={g.name} className="mb-1">
            <div className="px-4 py-1 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">
              {g.name}
            </div>
            {g.items.map((it) => {
              flatIndex++;
              const active = flatIndex === sel;
              return (
                <button
                  key={it.id}
                  id={`palette-${it.id}`}
                  ref={active ? activeRef : undefined}
                  role="option"
                  aria-selected={active}
                  onMouseEnter={() => setSel(filtered.indexOf(it))}
                  onClick={() => choose(it)}
                  className={`flex w-full items-center justify-between px-4 py-1.5 text-left text-sm transition-colors ${
                    active ? 'bg-blue-deep/20 text-ink' : 'text-ink-dim hover:bg-panel/60'
                  }`}
                >
                  <span>{it.label}</span>
                  {it.hint && <span className="font-mono text-[11px] text-ink-faint">{it.hint}</span>}
                </button>
              );
            })}
          </div>
        ))}
      </div>
      <div className="flex items-center gap-3 border-t border-line px-4 py-1.5 text-[10px] text-ink-faint">
        <span>↑↓ navigate</span>
        <span>↵ run</span>
        <span>esc close</span>
      </div>
    </Modal>
  );
}
