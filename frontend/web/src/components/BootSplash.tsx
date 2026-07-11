import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ARGUS_SPLASH_ACTIVE_FRAMES,
  ARGUS_SPLASH_COLORS,
  ARGUS_SPLASH_FADE_FRAMES,
  ARGUS_SPLASH_FRAME_MS,
  ARGUS_SPLASH_HOLD_MS,
  splashLogoForWidth,
} from '../../../core/src/splash';

function terminalColumns(): number {
  const cell = window.innerWidth < 640 ? 7 : 8;
  return Math.max(40, Math.floor((window.innerWidth - 32) / cell));
}

export function BootSplash({ onDone }: { onDone: () => void }) {
  const [frame, setFrame] = useState(0);
  const [columns, setColumns] = useState(terminalColumns);
  const finished = useRef(false);
  const holdTimer = useRef<number | null>(null);
  const finish = useCallback(() => {
    if (finished.current) return;
    finished.current = true;
    onDone();
  }, [onDone]);

  useEffect(() => {
    const resize = () => setColumns(terminalColumns());
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, []);

  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      holdTimer.current = window.setTimeout(finish, 80);
      return () => {
        if (holdTimer.current != null) window.clearTimeout(holdTimer.current);
      };
    }
    const timer = window.setInterval(() => {
      setFrame((current) => {
        if (current < ARGUS_SPLASH_ACTIVE_FRAMES + ARGUS_SPLASH_FADE_FRAMES - 1) return current + 1;
        window.clearInterval(timer);
        holdTimer.current = window.setTimeout(finish, ARGUS_SPLASH_HOLD_MS);
        return current;
      });
    }, ARGUS_SPLASH_FRAME_MS);
    return () => {
      window.clearInterval(timer);
      if (holdTimer.current != null) window.clearTimeout(holdTimer.current);
    };
  }, [finish]);

  useEffect(() => {
    const skip = () => finish();
    window.addEventListener('keydown', skip, { once: true });
    return () => window.removeEventListener('keydown', skip);
  }, [finish]);

  const logo = splashLogoForWidth(columns);
  const fadeStep = Math.max(0, frame - ARGUS_SPLASH_ACTIVE_FRAMES + 1);
  const hiddenRows = fadeStep <= 1 ? 0 : Math.min(logo.length, (fadeStep - 1) * 2);
  const firstVisible = Math.floor(hiddenRows / 2);
  const lastVisible = logo.length - Math.ceil(hiddenRows / 2);
  const opacity = fadeStep > 0
    ? Math.max(0.08, 1 - fadeStep / (ARGUS_SPLASH_FADE_FRAMES + 1))
    : 1;

  return (
    <div
      role="status"
      aria-label="Argus starting"
      onClick={finish}
      className="fixed inset-0 z-[200] flex cursor-pointer items-center justify-center overflow-hidden bg-bg"
      style={{ opacity }}
    >
      <pre className="max-w-full select-none overflow-hidden px-4 font-mono text-[clamp(6px,1.05vw,13px)] font-semibold leading-[1.08]">
        {logo.map((line, row) => (
          <span key={row} aria-hidden="true" className="block">
            {[...(row >= firstVisible && row < lastVisible ? line : ' '.repeat([...line].length))].map((char, column) => (
              <span key={column} style={{ color: ARGUS_SPLASH_COLORS[(Math.floor(column / 7) + row + frame) % ARGUS_SPLASH_COLORS.length] }}>
                {char}
              </span>
            ))}
          </span>
        ))}
      </pre>
    </div>
  );
}
