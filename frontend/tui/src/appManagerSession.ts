import { useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import type { ApiClient, DaemonStartResult, EventMsg } from './api.js';
import { taskDispatchMessage } from './api.js';

// Elapsed durations only change once per second. Updating faster forces Ink to
// diff and repaint the entire live frame without adding useful information.
export const MANAGER_TICK_MS = 1_000;

export interface ActiveManagerRequest {
  id: number;
  project: string;
  controller: AbortController;
  messageId: string;
}

export interface ManagerSessionState {
  pending: boolean;
  startedAt: number;
  tick: number;
  managerRequestRef: MutableRefObject<ActiveManagerRequest | null>;
  cancelManagerTurn: () => boolean;
  stopWaiting: () => void;
  submitFreeText: (text: string) => Promise<void>;
}

interface ManagerSessionArgs {
  api: ApiClient;
  projectRef: MutableRefObject<string>;
  setEvents: Dispatch<SetStateAction<EventMsg[]>>;
  setNotice: Dispatch<SetStateAction<string>>;
  captureAdmission: (
    start: DaemonStartResult | undefined,
    targetProject: string,
    resumeContinuous: boolean,
  ) => void;
}

export function useManagerSession({
  api,
  projectRef,
  setEvents,
  setNotice,
  captureAdmission,
}: ManagerSessionArgs): ManagerSessionState {
  const [pending, setPending] = useState(false);
  const [startedAt, setStartedAt] = useState(0);
  const [tick, setTick] = useState(0);
  const managerRequestRef = useRef<ActiveManagerRequest | null>(null);
  const managerEpochRef = useRef(0);

  const cancelManagerTurn = () => {
    const cancelled = Boolean(managerRequestRef.current);
    managerEpochRef.current += 1;
    managerRequestRef.current?.controller.abort();
    managerRequestRef.current = null;
    setPending(false);

    setStartedAt(0);
    return cancelled;
  };

  const stopWaiting = () => {
    if (cancelManagerTurn()) {
      setNotice('stopped waiting · server-side work may still finish in the project timeline');
    } else {
      setNotice('no Manager reply is currently in flight');
    }
  };

  useEffect(() => () => {
    managerEpochRef.current += 1;
    managerRequestRef.current?.controller.abort();
    managerRequestRef.current = null;
  }, []);

  useEffect(() => {
    if (!pending) return;
    const id = setInterval(() => setTick((t) => t + 1), MANAGER_TICK_MS);
    return () => clearInterval(id);
  }, [pending]);

  const submitFreeText = async (text: string) => {
    if (managerRequestRef.current) {
      setNotice('Argus is still working · wait or switch daemons to cancel');
      return;
    }
    const requestProject = projectRef.current;
    const requestId = ++managerEpochRef.current;
    const controller = new AbortController();
    managerRequestRef.current = {
      id: requestId,
      project: requestProject,
      controller,
      messageId: '',
    };
    const isCurrent = () => {
      const request = managerRequestRef.current;
      return Boolean(
        request
        && request.id === requestId
        && request.project === requestProject
        && projectRef.current === requestProject
        && !controller.signal.aborted,
      );
    };

    const replyId = `argus-${Date.now()}`;
    setEvents((events) => [
      ...events,
      {
        type: 'ui.operator',
        text,
        ts: Date.now() / 1000,
        event_id: `local-${requestProject}-${requestId}-operator`,
        message_id: `local-${requestId}-operator`,
        local_request_id: requestId,
        local_optimistic: true,
      } as EventMsg,
    ]);

    setStartedAt(Date.now());
    setTick(0);
    setPending(true);
    setNotice('');

    const say = (
      nextText: string,
      messageId = replyId,
      fragmentMode: 'append' | 'snapshot' | 'auto' = 'auto',
    ) => {
      if (!isCurrent()) return;
      setEvents((events) => (
        isCurrent()
          ? [
              ...events,
              {
                type: 'ui.argus',
                text: nextText,
                message_id: messageId,
                fragment_mode: fragmentMode,
                ts: Date.now() / 1000,
              } as EventMsg,
            ]
          : events
      ));
    };

    let gotDelta = false;
    let streamErr: Error | null = null;
    try {
      try {
        await api.messageStream(text, {
          onDelta: (block, messageId, fragmentMode) => {
            if (!isCurrent()) return;
            gotDelta = true;

            const activeMessageId = messageId || replyId;
            const request = managerRequestRef.current;
            if (request?.id === requestId) request.messageId = activeMessageId;
            say(
              block,
              activeMessageId,
              fragmentMode === 'append' || fragmentMode === 'snapshot'
                ? fragmentMode
                : 'auto',
            );
          },
          onDone: (result) => {
            if (!isCurrent()) return;
            if (result.kind === 'task') {
              captureAdmission(
                result.daemon,
                requestProject,
                Boolean(result.continuous),
              );
              if (!gotDelta) say(taskDispatchMessage(result));
            }
            else if (!gotDelta) {
              say(result.reply || '[Manager reply unavailable] No task was dispatched.');
            }
          },
          onError: (error) => {
            if (isCurrent()) streamErr = error;
          },
        }, controller.signal);
      } catch (error) {
        if (isCurrent()) streamErr = error as Error;
      }

      if (!isCurrent()) return;

      if (streamErr && !gotDelta) {
        try {
          const result = await api.message(text, controller.signal);
          if (!isCurrent()) return;
          if (result.kind === 'chat' && result.reply) say(result.reply);
          else if (result.kind === 'task') {
            captureAdmission(
              result.daemon,
              requestProject,
              Boolean(result.continuous),
            );
            say(taskDispatchMessage(result));
          }
          else say(result.reply || '(no response)');
        } catch (error) {
          if (isCurrent()) say(`(couldn’t reach Argus: ${(error as Error).message})`);
        }
      }
    } finally {
      if (managerRequestRef.current?.id === requestId) {
        managerRequestRef.current = null;
        setPending(false);
      }
    }
  };

  return {
    pending,
    startedAt,
    tick,
    managerRequestRef,
    cancelManagerTurn,
    stopWaiting,
    submitFreeText,
  };
}
