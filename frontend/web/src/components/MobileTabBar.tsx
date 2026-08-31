import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faBars,
  faDiagramProject,
  faFlask,
  faListUl,
  faWindowMaximize,
} from '@fortawesome/free-solid-svg-icons';
import type { IconDefinition } from '@fortawesome/fontawesome-svg-core';
import { useI18n } from '../i18n';

export type MobileTab = 'sessions' | 'mission' | 'activity' | 'workbench' | 'preview';

/** Bottom navigation for phones.
 *
 * The workbench has three destinations plus the session list, and on a narrow
 * screen they were reachable only through two 32px icons buried in the top
 * bar — the hardest place on a phone for a thumb to reach. This puts all four
 * on the bottom edge at full touch-target size, with the active one labelled,
 * and hides itself at `lg` where the real three-pane layout takes over. */
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
  const { t } = useI18n();
  const tabs: { id: Exclude<MobileTab, 'sessions'>; label: string; icon: IconDefinition }[] = [
    { id: 'mission', label: t('mobile.mission'), icon: faDiagramProject },
    { id: 'activity', label: t('mobile.activity'), icon: faListUl },
    { id: 'workbench', label: t('mobile.workbench'), icon: faFlask },
    { id: 'preview', label: t('mobile.preview'), icon: faWindowMaximize },
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
          aria-label={t('topbar.openSessions')}
          className="flex min-h-[3.25rem] flex-1 flex-col items-center justify-center gap-0.5 text-ink-faint active:bg-panel-raised"
        >
          <FontAwesomeIcon icon={faBars} className="h-4 w-4" />
          <span className="text-[10px] leading-none">{t('mobile.sessions')}</span>
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
            className={`flex min-h-[3.25rem] flex-1 flex-col items-center justify-center gap-0.5 active:bg-panel-raised ${
              selected ? 'text-blue' : 'text-ink-faint'
            }`}
          >
            <FontAwesomeIcon icon={tab.icon} className="h-4 w-4" />
            <span className="text-[10px] leading-none">{tab.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
