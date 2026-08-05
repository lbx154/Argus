import { useEffect, useMemo, useRef, useState } from 'react';
import { operatorDecisionCards, type OperatorDecisionCard } from '../../core/src/decisions';
import { api, type BacklogItem } from './api';
import { type NoticeTone } from './components/ActionNotice';

const errorText = (error: unknown): string =>
  error instanceof Error ? error.message : String(error || 'Unknown error');

interface UsePendingReplySessionOptions {
  activeSid: string | null;
  backlog: BacklogItem[] | undefined;
  notify: (tone: NoticeTone, message: string) => void;
  pendingQuestions: Array<Record<string, unknown>> | undefined;
  refetchSnapshot: () => Promise<unknown>;
}

export function usePendingReplySession({
  activeSid,
  backlog,
  notify,
  pendingQuestions,
  refetchSnapshot,
}: UsePendingReplySessionOptions) {
  const [pendingReplyOpen, setPendingReplyOpen] = useState(false);
  const [pendingReplyBusy, setPendingReplyBusy] = useState(false);
  const promptedReplyRef = useRef('');

  const pendingReply = useMemo<OperatorDecisionCard | null>(() => {
    const backlogRows = (backlog ?? []).map<Record<string, unknown>>((item) => ({
      ...item,
      operator_decision: (item as unknown as Record<string, unknown>).operator_decision,
    }));
    return operatorDecisionCards(pendingQuestions ?? [], backlogRows)[0] ?? null;
  }, [backlog, pendingQuestions]);

  useEffect(() => {
    if (!pendingReply || !activeSid) {
      setPendingReplyOpen(false);
      return;
    }
    const key = `${activeSid}:${pendingReply.id}`;
    if (promptedReplyRef.current !== key) {
      promptedReplyRef.current = key;
      setPendingReplyOpen(true);
    }
  }, [activeSid, pendingReply]);

  const answerPendingReply = async (optionId: string, note: string) => {
    if (!activeSid || !pendingReply || pendingReplyBusy) return;
    setPendingReplyBusy(true);
    try {
      const result = pendingReply.legacy
        ? await api.answerPending(activeSid, pendingReply.item_id, note)
        : await api.resolveDecision(
          activeSid,
          pendingReply.id,
          optionId,
          note,
          pendingReply.revision,
        );
      if (result.resolved === false) {
        notify(
          'info',
          String(result.reply || 'Manager needs a more specific answer.'),
        );
        return;
      }
      setPendingReplyOpen(false);
      await refetchSnapshot();
      if (result.daemon && Number(result.daemon.rc ?? 0) !== 0) {
        notify(
          'error',
          `Answer queued, but the daemon did not start: ${result.daemon.error || 'operator action required'}`,
        );
      } else {
        notify(
          'success',
          String(result.reply || 'Manager delivered your answer to the team.'),
        );
      }
    } catch (error) {
      notify('error', `Could not send answer: ${errorText(error)}`);
    } finally {
      setPendingReplyBusy(false);
    }
  };

  return {
    answerPendingReply,
    pendingReply,
    pendingReplyBusy,
    pendingReplyOpen,
    setPendingReplyOpen,
  };
}
