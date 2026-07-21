import { useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import type { ApiClient, DaemonStartResult, EventMsg } from './api.js';
import { taskDispatchMessage } from './api.js';

export interface ActiveManagerRequest {
  id: number;
  project: string;
  controller: AbortController;
  messageId: string;
}

export interface ManagerSessionState {
  pending: boolean;
  phase: string;
  phaseHeartbeat: boolean;
  phaseQuietS: number;
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
  const [phase, setPhase] = useState('');
  const [phaseHeartbeat, setPhaseHeartbeat] = useState(false);
  const [phaseQuietS, setPhaseQuietS] = useState(0);
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
    setPhase('');
    setPhaseHeartbeat(false);
    setPhaseQuietS(0);
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
    const id = setInterval(() => setTick((t) => t + 1), 120);
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
      { type: 'ui.operator', text, ts: Date.now() / 1000 } as EventMsg,
    ]);
    setPhase('');
    setStartedAt(Date.now());
    setTick(0);
    setPending(true);
    setNotice('');

    const say = (nextText: string, messageId = replyId) => {
      if (!isCurrent()) return;
      setEvents((events) => (
        isCurrent()
          ? [
              ...events,
              {
                type: 'ui.argus',
                text: nextText,
                message_id: messageId,
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
          onPhase: (label, _role, meta) => {
            if (!isCurrent()) return;
            setPhase(label);
            setPhaseHeartbeat(meta.heartbeat);
            setPhaseQuietS(meta.quietS);
          },
          onDelta: (block, messageId) => {
            if (!isCurrent()) return;
            gotDelta = true;
            setPhase('');
            setPhaseHeartbeat(false);
            setPhaseQuietS(0);
            const activeMessageId = messageId || replyId;
            const request = managerRequestRef.current;
            if (request?.id === requestId) request.messageId = activeMessageId;
            say(block, activeMessageId);
          },
          onDone: (result) => {
            if (!isCurrent()) return;
            if (result.kind === 'task') {
              captureAdmission(
                result.daemon,
                requestProject,
                Boolean(result.continuous),
              );
              say(taskDispatchMessage(result));
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
        setPhaseHeartbeat(false);
        setPhaseQuietS(0);
      }
    }
  };

  return {
    pending,
    phase,
    phaseHeartbeat,
    phaseQuietS,
    startedAt,
    tick,
    managerRequestRef,
    cancelManagerTurn,
    stopWaiting,
    submitFreeText,
  };
}
