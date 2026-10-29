export const PROMPT_COMPLETION_VISIBLE_ROWS = 8;
export const PROMPT_COMPLETION_LISTBOX_ID = 'prompt-completion-listbox';

/** One row of the composer's completion menu and the draft choosing it leaves (caret: where the caret lands in the visible text). */
export interface CompletionItem { id: string; name: string; arg?: string; detail?: string; value: string; caret?: number; close?: boolean }

export function clampPromptCompletionSelection(selected: number, visibleCount: number): number {
  if (visibleCount <= 0) return 0;
  return Math.max(0, Math.min(selected, visibleCount - 1));
}

export function promptCompletionOptionId(id: string): string {
  return `prompt-completion-option-${id}`;
}

/** Inline completion menu for @paths and #tasks; the caller decides the rows and what choosing one leaves. */
export function PromptCompletionMenu({
  items,
  label,
  selected,
  onSelect,
}: {
  items: CompletionItem[];
  label: string;
  /** -1 until the arrows pick a row: Tab then takes the first, and Enter sends. */
  selected: number;
  onSelect: (index: number) => void;
}) {
  return (
    <div
      id={PROMPT_COMPLETION_LISTBOX_ID}
      role="listbox"
      aria-label={label}
      className="prompt-completion-menu scroll-thin border-b border-line/40"
    >
      {items.map((item, index) => (
        <button
          key={item.id}
          id={promptCompletionOptionId(item.id)}
          type="button"
          role="option"
          // The textarea keeps focus and answers the keys (aria-activedescendant).
          tabIndex={-1}
          aria-selected={index === selected}
          onPointerDown={(e) => {
            // Prevent the textarea from losing focus on click.
            e.preventDefault();
            onSelect(index);
          }}
          className={`flex w-full items-baseline gap-2 px-3 py-1.5 text-left text-sm transition-colors ${
            index === selected
              ? 'bg-blue/10 text-ink'
              : 'text-ink-dim hover:bg-line/20'
          }`}
        >
          <span className="min-w-0 max-w-[70%] truncate font-mono text-blue">{item.name}</span>
          {item.arg ? (
            <span className="shrink-0 font-mono text-xs text-ink-faint">{item.arg}</span>
          ) : null}
          <span className="min-w-0 flex-1 truncate text-xs text-ink-faint">{item.detail}</span>
          {index === 0 && selected < 0 ? <kbd className="shrink-0 font-mono text-xs text-ink-faint">Tab</kbd> : null}
        </button>
      ))}
    </div>
  );
}
