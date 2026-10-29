import { useEffect, useMemo, useRef, useState } from 'react';
import { operatorDecisionCards, questionRole } from '../../core/src/decisions';
import { api, type BacklogItem } from './api';
import { type NoticeTone } from './components/ActionNotice';
import { useI18n } from './i18n';
import { roleLabel } from './lib/enumLabels';
import { errorText } from './lib/format';

export function usePendingReplySession({ activeSid, backlog, notify, pendingQuestions, refetchSnapshot }: {
  activeSid: string | null;
  backlog: BacklogItem[] | undefined;
  notify: (tone: NoticeTone, message: string, details?: unknown) => void;
  pendingQuestions: Array<Record<string, unknown>> | undefined;
  refetchSnapshot: () => Promise<unknown>;
}) {
  const { t } = useI18n();
  const [pendingReplyBusy, setPendingReplyBusy] = useState(false);
  const [pendingReplyError, setPendingReplyError] = useState('');
  const [pendingReplyNote, setPendingReplyNote] = useState('');
  const submitting = useRef(false);
  const pendingReply = useMemo(() => operatorDecisionCards(
    pendingQuestions ?? [], backlog?.map(item => ({ ...item })) ?? [],
  )[0] ?? null, [backlog, pendingQuestions]);

  useEffect(() => { setPendingReplyNote(''); setPendingReplyError(''); }, [activeSid, pendingReply?.id]);

  const answerPendingReply = async (note: string): Promise<boolean> => {
    if (!activeSid || !pendingReply || !note.trim() || submitting.current) return false;
    submitting.current = true;
    setPendingReplyBusy(true);
    setPendingReplyError('');
    try {
      const result = await api.resolveDecision(activeSid, pendingReply.id, 'custom', note.trim());
      if (result.resolved === false || ('kind' in result && ['error', 'cancelled'].includes(String(result.kind)))) {
        setPendingReplyError(errorText(result.reply, undefined, 'message_failed'));
        await refetchSnapshot();
        return false;
      }
      await refetchSnapshot();
      notify('success', t('pending.delivered', { role: roleLabel(questionRole(pendingReply), t) }));
      return true;
    } catch (error) {
      setPendingReplyError(errorText(error));
      await refetchSnapshot();
      return false;
    } finally {
      submitting.current = false;
      setPendingReplyBusy(false);
    }
  };

  return { answerPendingReply, pendingReply, pendingReplyBusy, pendingReplyError, pendingReplyNote, setPendingReplyNote };
}
