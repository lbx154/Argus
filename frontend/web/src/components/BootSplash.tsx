import { useCallback, useEffect, useRef } from 'react';
import { useI18n } from '../i18n';
import { ArgusMark } from './Wordmark';

export const WEB_SPLASH_DURATION_MS = 820;

export function BootSplash({ onDone }: { onDone: () => void }) {
  const { t } = useI18n();
  const finished = useRef(false);
  const finish = useCallback(() => {
    if (finished.current) return;
    finished.current = true;
    onDone();
  }, [onDone]);

  useEffect(() => {
    const fallback = window.setTimeout(finish, WEB_SPLASH_DURATION_MS + 150);
    const skip = () => finish();
    window.addEventListener('keydown', skip, { once: true });
    return () => {
      window.clearTimeout(fallback);
      window.removeEventListener('keydown', skip);
    };
  }, [finish]);

  return (
    <div
      role="status"
      aria-label={t('splash.starting')}
      onClick={finish}
      onAnimationEnd={(event) => {
        if (event.currentTarget === event.target) finish();
      }}
      className="argus-web-splash"
    >
      <div className="argus-web-splash-logo" aria-hidden="true">
        <ArgusMark size={168} />
      </div>
    </div>
  );
}
