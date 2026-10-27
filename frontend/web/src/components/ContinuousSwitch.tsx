import { useId } from 'react';
import { useI18n } from '../i18n';

/** Always-visible on/off for continuous work: the team plans and starts the next piece itself. */
export function ContinuousSwitch({ enabled, pending = false, onToggle }: {
  enabled: boolean;
  pending?: boolean;
  onToggle: () => void;
}) {
  const { t } = useI18n();
  const hintId = useId();
  return (
    <button type="button" role="switch" className="composer-continuous" aria-checked={enabled} disabled={pending}
      aria-describedby={hintId} title={t('continuous.hint')} onClick={onToggle}>
      <span className="composer-continuous-track" aria-hidden="true" />
      <span>{t('continuous.label')}</span>
      <span id={hintId} className="composer-continuous-hint">{t('continuous.hint')}</span>
    </button>
  );
}
