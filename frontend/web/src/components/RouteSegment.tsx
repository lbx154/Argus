import type { MessageRouteOverride } from '../api';
import { useI18n } from '../i18n';

const ROUTES: readonly MessageRouteOverride[] = ['auto', 'task', 'chat'];

/** Per-message route choice (Auto / Task / Chat) as a segmented control. */
export function RouteSegment({ value, onChange, disabled = false, tabIndex }: {
  value: MessageRouteOverride;
  onChange: (route: MessageRouteOverride) => void;
  disabled?: boolean;
  tabIndex?: number;
}) {
  const { t } = useI18n();
  const labels: Record<MessageRouteOverride, string> = {
    auto: t('chat.routeAuto'), task: t('chat.routeTask'), chat: t('chat.routeChat'),
  };
  return (
    <div className="route-seg" role="radiogroup" aria-label={t('chat.routeLabel')} title={t('chat.routeHint')}>
      {ROUTES.map((route) => (
        <button key={route} type="button" role="radio" aria-checked={value === route} disabled={disabled}
          tabIndex={tabIndex} onClick={() => { if (route !== value) onChange(route); }}>
          {labels[route]}
        </button>
      ))}
    </div>
  );
}
