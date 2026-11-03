import { FlaskConical, Menu, MessageSquare, Network, Package, type LucideIcon } from 'lucide-react';
import { useI18n } from '../i18n';

export type MobileTab = 'sessions' | 'activity' | 'workbench' | 'map' | 'preview';

/** Conversation first; research tracing and expert tools remain one tap away. */
export function MobileTabBar({
  active,
  onSelect,
  onOpenSessions,
  sidebarOpen = false,
}: {
  active: Exclude<MobileTab, 'sessions'>;
  onSelect: (tab: Exclude<MobileTab, 'sessions'>) => void;
  onOpenSessions?: () => void;
  sidebarOpen?: boolean;
}) {
  const { t, locale } = useI18n();
  const tabs: { id: Exclude<MobileTab, 'sessions'>; label: string; icon: LucideIcon }[] = [
    { id: 'activity', label: t('mobile.activity'), icon: MessageSquare },
    { id: 'preview', label: locale === 'zh-CN' ? '成果与文件' : 'Results & files', icon: Package },
    { id: 'map', label: t('mobile.map'), icon: Network },
    { id: 'workbench', label: t('mobile.workbench'), icon: FlaskConical },
  ];

  return (
    <nav
      aria-label={t('mobile.views')}
      className={`mobile-tabbar glass-panel glass-panel--raised fixed inset-x-0 bottom-0 z-40 items-stretch border-t border-line/60 lg:hidden ${
        sidebarOpen ? 'hidden' : 'flex'
      }`}
    >
      {onOpenSessions ? (
        <button
          type="button"
          onClick={onOpenSessions}
          className="flex min-h-[3.5rem] flex-1 flex-col items-center justify-center gap-1 text-ink-faint active:bg-panel-raised"
        >
          <Menu size={20} strokeWidth={1.75} aria-hidden="true" />
          <span className="text-[11px] leading-none">{t('mobile.sessions')}</span>
        </button>
      ) : null}
      {tabs.map((tab) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => onSelect(tab.id)}
            aria-current={selected ? 'page' : undefined}
            className={`flex min-h-[3.5rem] flex-1 flex-col items-center justify-center gap-1 active:bg-panel-raised ${
              selected ? 'font-medium text-ink' : 'text-ink-faint'
            }`}
          >
            <tab.icon size={20} strokeWidth={selected ? 2 : 1.75} aria-hidden="true" />
            <span className="text-[11px] leading-none">{tab.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
