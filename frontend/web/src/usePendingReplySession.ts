import { useMemo, useRef, useState } from 'react';
import { operatorDecisionCards } from '../../core/src/decisions';
import { api, type Snapshot, type UploadedAttachment } from './api';
import { type NoticeTone } from './components/ActionNotice';
import { errorText } from './lib/format';

export function usePendingReplySession({ activeSid, backlog, notify, pendingQuestions, refetchSnapshot }: {
  activeSid: string | null;
  backlog: Snapshot['backlog'] | undefined;
  notify: (tone: NoticeTone, message: string, details?: unknown) => void;
  pendingQuestions: Array<Record<string, unknown>> | undefined;
  refetchSnapshot: () => Promise<unknown>;
}) {
  const [pendingReplyBusy, setPendingReplyBusy] = useState(false);
  const submitting = useRef(false);
  const pendingReply = useMemo(() => operatorDecisionCards(
    pendingQuestions ?? [], backlog?.map(item => ({ ...item })) ?? [],
  )[0] ?? null, [backlog, pendingQuestions]);

  const answerPendingReply = async (note: string, prepareAttachments?: () => Promise<UploadedAttachment[]>): Promise<boolean> => {
    if (!activeSid || !pendingReply || !note.trim() || submitting.current) return false;
    submitting.current = true;
    setPendingReplyBusy(true);
    try {
      const attachments = await prepareAttachments?.() ?? [];
      const result = await api.resolveDecision(activeSid, pendingReply.id, 'custom', note.trim(), attachments);
      if (result.resolved === false || ('kind' in result && ['error', 'cancelled'].includes(String(result.kind)))) {
        notify('error', errorText(result.reply, undefined, 'message_failed'), result.reply);
        await refetchSnapshot();
        return false;
      }
      await refetchSnapshot();
      return true;
    } catch (error) {
      notify('error', errorText(error), error);
      await refetchSnapshot();
      return false;
    } finally {
      submitting.current = false;
      setPendingReplyBusy(false);
    }
  };

  return { answerPendingReply, pendingReply, pendingReplyBusy };
}
