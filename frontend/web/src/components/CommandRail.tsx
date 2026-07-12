import type { ThemeMode } from './TopBar';
import { Wordmark } from './Wordmark';

type IconName = 'sessions' | 'plus' | 'preview' | 'chat' | 'settings' | 'theme';

function RailIcon({ name }: { name: IconName }) {
  const paths: Record<IconName, React.ReactNode> = {
    sessions: <><rect x="2" y="3" width="12" height="10" rx="1.5" /><path d="M5.5 3v10" /></>,
    plus: <path d="M8 3v10M3 8h10" />,
    preview: <><rect x="2" y="2.5" width="12" height="11" rx="1.5" /><path d="M9.5 2.75v10.5" /></>,
    chat: <path d="M3 3.5h10v7H7l-3.5 2v-2H3z" />,
    settings: <><circle cx="8" cy="8" r="2.25" /><path d="M8 2.25v1.2M8 12.55v1.2M2.25 8h1.2M12.55 8h1.2M3.95 3.95l.85.85M11.2 11.2l.85.85M12.05 3.95l-.85.85M4.8 11.2l-.85.85" /></>,
    theme: <path d="M8 2.25a5.75 5.75 0 1 0 0 11.5V2.25Z" />,
  };
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.25">
      {paths[name]}
    </svg>
  );
}

function RailButton({
  icon,
  label,
  active = false,
  onClick,
}: {
  icon: IconName;
  label: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={active}
      title={label}
      onClick={onClick}
      className={`relative flex h-10 w-10 items-center justify-center rounded-md transition-colors ${
        active ? 'bg-panel-raised text-blue-sky' : 'text-ink-faint hover:bg-panel hover:text-ink'
      }`}
    >
      {active ? <span className="absolute -left-0.5 h-5 w-0.5 rounded-full bg-blue" /> : null}
      <RailIcon name={icon} />
    </button>
  );
}

export function CommandRail({
  previewOpen,
  consoleOpen,
  pendingCount,
  themeMode,
  onSessions,
  onNew,
  onTogglePreview,
  onToggleConsole,
  onConfig,
  onCycleTheme,
}: {
  previewOpen: boolean;
  consoleOpen: boolean;
  pendingCount: number;
  themeMode: ThemeMode;
  onSessions: () => void;
  onNew: () => void;
  onTogglePreview: () => void;
  onToggleConsole: () => void;
  onConfig: () => void;
  onCycleTheme: () => void;
}) {
  return (
    <nav className="flex h-full w-11 shrink-0 flex-col items-center border-r border-line/70 bg-surface py-2" aria-label="Workbench">
      <button type="button" onClick={onSessions} aria-label="Open sessions" title="Sessions · Ctrl/⌘ P" className="mb-3 flex h-9 w-9 items-center justify-center">
        <Wordmark size={18} compact />
      </button>
      <div className="flex flex-col gap-1">
        <RailButton icon="sessions" label="Sessions" onClick={onSessions} />
        <RailButton icon="plus" label="New session" onClick={onNew} />
        <RailButton icon="preview" label="Toggle preview" active={previewOpen} onClick={onTogglePreview} />
        <div className="relative">
          <RailButton icon="chat" label="Toggle console" active={consoleOpen} onClick={onToggleConsole} />
          {pendingCount > 0 ? (
            <span className="pointer-events-none absolute right-0.5 top-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-gold px-1 font-mono text-[8px] font-bold text-bg">
              {pendingCount}
            </span>
          ) : null}
        </div>
      </div>
      <div className="mt-auto flex flex-col gap-1">
        <RailButton icon="theme" label={`Theme: ${themeMode}`} onClick={onCycleTheme} />
        <RailButton icon="settings" label="Runtime settings" onClick={onConfig} />
      </div>
    </nav>
  );
}
