import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faBars,
  faDiagramProject,
  faEllipsis,
  faFlask,
  faListUl,
  faWindowMaximize,
} from '@fortawesome/free-solid-svg-icons';
import type { IconDefinition } from '@fortawesome/fontawesome-svg-core';
import { useI18n } from '../i18n';

export type MobileTab = 'sessions' | 'mission' | 'activity' | 'workbench' | 'map' | 'preview';

/** Primary task destinations stay visible; specialist views share one menu. */
export function MobileTabBar({
  active,
  onSelect,
  onOpenSessions,
  sidebarOpen = false,
  onRead,
}: {
  active: Exclude<MobileTab, 'sessions'>;
  onSelect: (tab: Exclude<MobileTab, 'sessions'>) => void;
  onOpenSessions?: () => void;
  sidebarOpen?: boolean;
  onRead?: () => void;
}) {
  const { t, locale } = useI18n();
  const tabs: { id: Exclude<MobileTab, 'sessions'>; label: string; icon: IconDefinition }[] = [
    { id: 'mission', label: t('mobile.mission'), icon: faDiagramProject },
    { id: 'activity', label: t('mobile.activity'), icon: faListUl },
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
      <details className="workspace-more mobile-more flex-1" onKeyDown={event => {
        if (event.key === 'Escape') { event.currentTarget.open = false; event.currentTarget.querySelector('summary')?.focus(); }
      }}>
        <summary className="min-h-[3.25rem]" aria-current={active === 'map' || active === 'workbench' ? 'page' : undefined}>
          <FontAwesomeIcon icon={faEllipsis} className="h-4 w-4" />
          <span>{locale === 'zh-CN' ? '更多' : 'More'}</span>
        </summary>
        <div className="workspace-more-menu" onClick={event => {
          if ((event.target as HTMLElement).closest('button')) event.currentTarget.closest('details')?.removeAttribute('open');
        }}>
          {onRead ? <button type="button" onClick={onRead}>{locale === 'zh-CN' ? '任务说明与依据' : 'Task explanation and evidence'}</button> : null}
          <button type="button" onClick={() => onSelect('workbench')}><FontAwesomeIcon icon={faFlask} />{t('mobile.workbench')}</button>
          <button type="button" onClick={() => onSelect('map')}><FontAwesomeIcon icon={faDiagramProject} />{t('mobile.map')}</button>
        </div>
      </details>
    </nav>
  );
}
