import { slashCompletions } from '../../../core/src/commands';
import type { SlashCommand } from '../../../core/src/commands';

const MAX_ROWS = 8;

/**
 * Inline slash-command completion menu. Shown only when the user is typing
 * a command token (no whitespace yet). Returns null when there are no matches
 * or the query already contains a space (argument entry started).
 */
export function SlashCompletionMenu({
  query,
  selected,
  onSelect,
}: {
  query: string;
  selected: number;
  onSelect: (command: SlashCommand) => void;
}) {
  const completions = slashCompletions(query);
  if (completions.length === 0) return null;

  const visible = completions.slice(0, MAX_ROWS);
  const bounded = Math.max(0, Math.min(selected, visible.length - 1));

  return (
    <div
      role="listbox"
      aria-label="Slash command suggestions"
      className="slash-completion-menu scroll-thin border-b border-line/40"
    >
      {visible.map((command, index) => (
        <button
          key={command.id}
          type="button"
          role="option"
          aria-selected={index === bounded}
          onPointerDown={(e) => {
            // Prevent the textarea from losing focus on click.
            e.preventDefault();
            onSelect(command);
          }}
          className={`flex w-full items-baseline gap-2 px-3 py-1.5 text-left text-sm transition-colors ${
            index === bounded
              ? 'bg-blue/10 text-ink'
              : 'text-ink-dim hover:bg-line/20'
          }`}
        >
          <span className="shrink-0 font-mono text-blue">{command.name}</span>
          {command.arg ? (
            <span className="shrink-0 font-mono text-xs text-ink-faint">{command.arg}</span>
          ) : null}
          <span className="min-w-0 flex-1 truncate text-xs text-ink-faint">{command.desc}</span>
        </button>
      ))}
    </div>
  );
}
