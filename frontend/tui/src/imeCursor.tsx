import React, { createContext, useContext, useEffect } from 'react';
import ansiEscapes from 'ansi-escapes';

export interface ImeCursorTarget {
  /** Number of terminal rows from the frame's last rendered line to the caret. */
  rowsAboveFrameBottom: number;
  /** Zero-based terminal column containing the caret. */
  column: number;
}

export interface ImeCursorController {
  enabled: boolean;
  activate(target: ImeCursorTarget): () => void;
}

const DISABLED_CONTROLLER: ImeCursorController = {
  enabled: false,
  activate: () => () => {},
};

const ImeCursorContext = createContext<ImeCursorController>(DISABLED_CONTROLLER);

export function ImeCursorProvider({
  controller,
  children,
}: {
  controller: ImeCursorController;
  children: React.ReactNode;
}) {
  return (
    <ImeCursorContext.Provider value={controller}>
      {children}
    </ImeCursorContext.Provider>
  );
}

/** Register one visible editor as the native terminal/IME cursor target. */
export function useImeCursorTarget(target: ImeCursorTarget): boolean {
  const controller = useContext(ImeCursorContext);
  useEffect(
    () => controller.activate(target),
    [controller, target.column, target.rowsAboveFrameBottom],
  );
  return controller.enabled;
}

interface ActiveTarget {
  token: symbol;
  target: ImeCursorTarget;
}

class TerminalImeCursor implements ImeCursorController {
  readonly enabled: boolean;
  readonly stdout: NodeJS.WriteStream;

  private active: ActiveTarget | null = null;
  private anchoredRows = 0;
  private anchored = false;
  private baseAfterNewline = true;
  private pending: ReturnType<typeof setImmediate> | null = null;
  private disposed = false;
  private readonly rawWrite: (...args: unknown[]) => boolean;

  constructor(
    private readonly target: NodeJS.WriteStream,
    force?: boolean,
  ) {
    this.enabled = force ?? Boolean(target.isTTY && process.env.TERM !== 'dumb');
    this.rawWrite = target.write.bind(target) as (...args: unknown[]) => boolean;
    this.stdout = this.enabled
      ? new Proxy(target, {
          get: (stream, property) => {
            if (property === 'write') return this.write;
            const value = Reflect.get(stream, property, stream);
            return typeof value === 'function' ? value.bind(stream) : value;
          },
        })
      : target;
  }

  activate(target: ImeCursorTarget): () => void {
    if (!this.enabled || this.disposed) return () => {};
    const token = Symbol('ime-cursor-target');
    this.active = {
      token,
      target: {
        rowsAboveFrameBottom: Math.max(0, Math.floor(target.rowsAboveFrameBottom)),
        column: Math.max(0, Math.floor(target.column)),
      },
    };
    this.scheduleAnchor();
    return () => {
      if (this.active?.token !== token) return;
      this.active = null;
      this.cancelAnchor();
      this.restoreFrameCursor();
    };
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.active = null;
    this.cancelAnchor();
    this.restoreFrameCursor();
    this.rawWrite(ansiEscapes.cursorShow);
  }

  private readonly write = (...args: unknown[]): boolean => {
    this.cancelAnchor();
    this.restoreFrameCursor();
    const value = args[0];
    const text = Buffer.isBuffer(value) ? value.toString() : String(value ?? '');
    const result = this.rawWrite(...args);
    this.observeFrameEnd(text);
    this.scheduleAnchor();
    return result;
  };

  private observeFrameEnd(text: string): void {
    // Ink's normal log-update frame ends in LF; its full-screen fallback uses
    // clearTerminal and leaves the cursor on the final rendered line.
    if (text.includes('\n') || text.includes('\u001b[2J')) {
      this.baseAfterNewline = text.endsWith('\n');
    }
  }

  private scheduleAnchor(): void {
    if (!this.enabled || this.disposed || !this.active) return;
    this.cancelAnchor();
    this.pending = setImmediate(() => {
      this.pending = null;
      this.anchorAtInput();
    });
  }

  private cancelAnchor(): void {
    if (!this.pending) return;
    clearImmediate(this.pending);
    this.pending = null;
  }

  private anchorAtInput(): void {
    if (this.anchored || !this.active || this.disposed) return;
    const { target } = this.active;
    const rows = target.rowsAboveFrameBottom + (this.baseAfterNewline ? 1 : 0);
    this.rawWrite(
      '\r' +
      (rows > 0 ? ansiEscapes.cursorUp(rows) : '') +
      (target.column > 0 ? ansiEscapes.cursorForward(target.column) : '') +
      ansiEscapes.cursorShow,
    );
    this.anchoredRows = rows;
    this.anchored = true;
  }

  private restoreFrameCursor(): void {
    if (!this.anchored) return;
    this.rawWrite(
      ansiEscapes.cursorHide +
      '\r' +
      (this.anchoredRows > 0 ? ansiEscapes.cursorDown(this.anchoredRows) : '') +
      '\r',
    );
    this.anchoredRows = 0;
    this.anchored = false;
  }
}

export function createImeCursorOutput(
  stdout: NodeJS.WriteStream,
  options: { force?: boolean } = {},
): {
  stdout: NodeJS.WriteStream;
  controller: ImeCursorController;
  dispose: () => void;
} {
  const cursor = new TerminalImeCursor(stdout, options.force);
  return {
    stdout: cursor.stdout,
    controller: cursor,
    dispose: () => cursor.dispose(),
  };
}
