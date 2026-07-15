import { useCallback, useEffect, useRef } from 'react';
import { ArgusMark, Wordmark } from './Wordmark';

export const WEB_SPLASH_DURATION_MS = 650;

export function BootSplash({ onDone }: { onDone: () => void }) {
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
      aria-label="Argus starting"
      onClick={finish}
      onAnimationEnd={(event) => {
        if (event.currentTarget === event.target) finish();
      }}
      className="argus-web-splash"
    >
      <div className="argus-web-splash-logo argus-web-splash-logo-full" aria-hidden="true">
        <Wordmark size={72} />
      </div>
      <div className="argus-web-splash-logo argus-web-splash-logo-compact" aria-hidden="true">
        <ArgusMark size={112} />
      </div>
    </div>
  );
}
