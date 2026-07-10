import { useEffect, useState } from 'react';
import { useStdout } from 'ink';

export interface TerminalSize {
  columns: number;
  rows: number;
}

/** Reactive terminal dimensions for density-aware Ink layouts. */
export function useTerminalSize(): TerminalSize {
  const { stdout } = useStdout();
  const read = (): TerminalSize => ({
    columns: Math.max(40, stdout.columns || 80),
    rows: Math.max(16, stdout.rows || 24),
  });
  const [size, setSize] = useState<TerminalSize>(read);

  useEffect(() => {
    const onResize = () => setSize(read());
    stdout.on('resize', onResize);
    return () => {
      stdout.off('resize', onResize);
    };
  }, [stdout]);

  return size;
}
