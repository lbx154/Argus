import { useEffect, useMemo, useRef, useState } from 'react';
import { operatorDecisionCards, type OperatorDecisionCard } from '../../core/src/decisions';
import { api, type BacklogItem } from './api';
import { type NoticeTone } from './components/ActionNotice';

const errorText = (error: unknown): string =>
  error instanceof Error ? error.message : String(error || 'Unknown error');

interface UsePendingReplySessionOptions {
  activeSid: string | null;
  currentTaskId?: string | null;
  /** Auto-surface the dialog for a newly seen decision. The map keeps its own
   * banner and node highlight, so it opts out; false never opens uninvited. */
  autoOpen?: boolean;
  backlog: BacklogItem[] | undefined;
  notify: (tone: NoticeTone, message: string) => void;
  pendingQuestions: Array<Record<string, unknown>> | undefined;
  refetchSnapshot: () => Promise<unknown>;
}

const PROMPTED_KEY = 'argus.decision.prompted.v1';
const readPrompted = (): string => {
  try {
    return window.sessionStorage.getItem(PROMPTED_KEY) ?? '';
  } catch {
    return '';
  }
};
const writePrompted = (value: string) => {
  try {
    window.sessionStorage.setItem(PROMPTED_KEY, value);
  } catch {
    // Private-mode storage failures only cost the reload suppression.
  }
};

export function usePendingReplySession({
  activeSid,
  currentTaskId,
  autoOpen = true,
  backlog,
  notify,
  pendingQuestions,
  refetchSnapshot,
}: UsePendingReplySessionOptions) {
  const [openedReply, setOpenedReply] = useState<{
    sid: string;
    card: OperatorDecisionCard;
  } | null>(null);
  const [pendingReplyBusy, setPendingReplyBusy] = useState(false);
  const [pendingReplyError, setPendingReplyError] = useState('');
  const submitting = useRef(false);
  const promptedReplyRef = useRef('');

  const decisionCards = useMemo<OperatorDecisionCard[]>(() => {
    const backlogRows = (backlog ?? []).map<Record<string, unknown>>((item) => ({
      ...item,
      operator_decision: (item as unknown as Record<string, unknown>).operator_decision,
    }));
    return operatorDecisionCards(pendingQuestions ?? [], backlogRows, currentTaskId);
  }, [backlog, pendingQuestions, currentTaskId]);
  const preferredReply = decisionCards[0] ?? null;
  const hasSnapshot = backlog !== undefined || pendingQuestions !== undefined;
  const pendingReplyOpen = openedReply !== null && openedReply.sid === activeSid;
  // Keep the question being edited/submitted stable when the daemon moves to
  // another task. Its current-task label can still follow the live mission.
  const pendingReply = pendingReplyOpen ? {
    ...openedReply.card,
    is_current_task: currentTaskId && openedReply.card.kind !== 'domain_intake' ? openedReply.card.item_id === currentTaskId : undefined,
  } : preferredReply;
  const setPendingReplyOpen = (open: boolean) => {
    setPendingReplyError('');
    if (!open) {
      setOpenedReply(null);
    } else if (activeSid && preferredReply) {
      setOpenedReply(previous => previous?.sid === activeSid
        ? previous : { sid: activeSid, card: preferredReply });
    }
  };

  useEffect(() => {
    if (openedReply && (openedReply.sid !== activeSid || (
      hasSnapshot && !pendingReplyBusy && !decisionCards.some(card => card.id === openedReply.card.id)
    ))) {
      setOpenedReply(null);
    }
    if (!activeSid || !preferredReply || (!autoOpen && preferredReply.kind !== 'domain_intake') || pendingReplyOpen || pendingReplyBusy) return;
    const key = `${activeSid}:${preferredReply.id}`;
    // Session storage remembers across reloads: a decision hijacks the screen
    // once per tab, not on every visit while it stays unanswered.
    if (promptedReplyRef.current === key || readPrompted() === key) return;
    promptedReplyRef.current = key;
    writePrompted(key);
    setPendingReplyError('');
    setOpenedReply({ sid: activeSid, card: preferredReply });
  }, [activeSid, autoOpen, decisionCards, hasSnapshot, preferredReply, openedReply, pendingReplyOpen, pendingReplyBusy]);

  const answerPendingReply = async (optionId: string, note: string) => {
    if (!activeSid || !pendingReply || submitting.current) return;
    submitting.current = true;
    setPendingReplyBusy(true);
    setPendingReplyError('');
    try {
      const result = pendingReply.kind === 'domain_intake'
        ? await api.answerDomain(activeSid, pendingReply.id, optionId, note)
        : pendingReply.legacy
        ? await api.answerPending(activeSid, pendingReply.item_id, note)
        : await api.resolveDecision(
          activeSid,
          pendingReply.id,
          optionId,
          note,
        );
      if (result.resolved === false || ('kind' in result && ['error', 'cancelled'].includes(String(result.kind)))) {
        setPendingReplyError(String(result.reply || 'Could not send answer.'));
        await refetchSnapshot();
        return;
      }
      // A request may finish after the operator has opened another dialog.
      setOpenedReply(current => current === openedReply ? null : current);
      await refetchSnapshot();
      if (result.daemon && Number(result.daemon.rc ?? 0) !== 0) {
        notify(
          'error',
          `Answer queued, but the daemon did not start: ${result.daemon.error || 'operator action required'}`,
        );
      } else if (pendingReply.kind !== 'domain_intake') {
        notify(
          'success',
          String(result.reply || 'Manager delivered your answer to the team.'),
        );
      }
    } catch (error) {
      setPendingReplyError(errorText(error));
      notify('error', `Could not send answer: ${errorText(error)}`);
      await refetchSnapshot();
    } finally {
      submitting.current = false;
      setPendingReplyBusy(false);
    }
  };

  return {
    answerPendingReply,
    pendingReply,
    pendingReplyBusy,
    pendingReplyError,
    pendingReplyOpen,
    setPendingReplyOpen,
  };
}
