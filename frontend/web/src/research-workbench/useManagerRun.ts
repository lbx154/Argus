import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { ManagerResult } from './types';
import { useWorkbenchText } from './useWorkbenchText';
import { mergeStreamText } from './utils';

export function useManagerRun(sid: string | null, onComplete?: () => void | Promise<void>) {
  const { text } = useWorkbenchText();
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState('');
  const [phaseRole, setPhaseRole] = useState('manager');
  const [phases, setPhases] = useState<Array<{ label: string; role: string; at: number }>>([]);
  const [output, setOutput] = useState('');
  const [result, setResult] = useState<ManagerResult | null>(null);
  const [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null);

  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    controller.current?.abort();
    controller.current = null;
    setBusy(false);
    setPhase('');
    setPhases([]);
    setOutput('');
    setResult(null);
    setError('');
  }, [sid]);

  const run = async (message: string, files: File[] = []): Promise<ManagerResult | null> => {
    if (!sid || !message.trim() || busy) return null;
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setBusy(true);
    setPhase(text('正在连接 Argus', 'Connecting to Argus'));
    setPhaseRole('manager');
    setPhases([]);
    setOutput('');
    setResult(null);
    setError('');
    try {
      let attachments: Array<{ attachment_id: string }> = [];
      if (files.length) {
        setPhase(text('正在上传附件', 'Uploading attachments'));
        const uploaded = await api.uploadAttachments(sid, files, request.signal);
        attachments = uploaded.attachments.map((item) => ({ attachment_id: item.attachment_id }));
      }
      const response = await api.messageStream(sid, message.trim(), {
        onPhase: (label, role) => {
          const resolvedLabel = label || text('Argus 正在处理', 'Argus is working');
          const resolvedRole = role || 'manager';
          setPhase(resolvedLabel);
          setPhaseRole(resolvedRole);
          setPhases((current) => current.at(-1)?.label === resolvedLabel ? current : [...current, { label: resolvedLabel, role: resolvedRole, at: Date.now() }].slice(-8));
        },
        onDelta: (block, mode) => {
          setOutput((current) => mergeStreamText(current, block, mode));
        },
        onDone: (done) => {
          setResult(done);
          if (done.reply) setOutput((current) => mergeStreamText(current, done.reply ?? '', 'snapshot'));
        },
      }, request.signal, attachments);
      setResult(response);
      if (response.reply) setOutput((current) => mergeStreamText(current, response.reply ?? '', 'snapshot'));
      await onComplete?.();
      return response;
    } catch (caught) {
      if (!request.signal.aborted) {
        setError(caught instanceof Error ? caught.message : String(caught));
      }
      return null;
    } finally {
      if (controller.current === request) {
        controller.current = null;
        setBusy(false);
        setPhase('');
      }
    }
  };

  const cancel = () => {
    controller.current?.abort();
    controller.current = null;
    setBusy(false);
    setPhase('');
  };

  return {
    busy,
    phase,
    phaseRole,
    phases,
    output,
    result,
    error,
    run,
    cancel,
    clear: () => {
      setOutput('');
      setResult(null);
      setError('');
    },
  };
}
