import { useEffect, useMemo, useRef, useState } from 'react';
import { api, type BacklogItem } from './api';
import { type NoticeTone } from './components/ActionNotice';
import { type PendingReply } from './components/PendingReplyDialog';

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

  const pendingReply = useMemo<PendingReply | null>(() => {
    const rows: Array<Record<string, unknown>> = [
      ...(pendingQuestions ?? []),
      ...(backlog ?? []).map<Record<string, unknown>>((item) => ({
        id: item.id,
        title: item.title,
        objective: item.objective,
        pending_question: item.pending_question,
      })),
    ];
    const row = rows.find((item) => {
      const id = String(item.id ?? '').trim();
      const question = String(item.pending_question ?? item.question ?? item.text ?? '').trim();
      return Boolean(id && question);
    });
    if (!row) return null;
    return {
      id: String(row.id),
      title: String(row.title ?? row.objective ?? 'Blocked task'),
      question: String(row.pending_question ?? row.question ?? row.text),
    };
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

  const answerPendingReply = async (text: string) => {
    if (!activeSid || !pendingReply || pendingReplyBusy) return;
    setPendingReplyBusy(true);
    try {
      const result = await api.answerPending(activeSid, pendingReply.id, text);
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
