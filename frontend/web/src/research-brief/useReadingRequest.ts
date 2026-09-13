import { useEffect } from 'react';
import { useMutation, useMutationState, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../../../core/src/http';
import { api, type ArtifactInfo } from '../api';
import { beginExplanationProgress, useExplanationProgress } from './progress';
import {
  clearFoundationRequest, foundationChoice, foundationListKey, saveFoundationRequest, selectFoundation,
  useFoundationList, useFoundationRequest, type FoundationDraft, type FoundationRequest,
} from './foundation';

export function isRootFoundation(item: ArtifactInfo): boolean {
  const record = item.reader_foundation;
  return !!record && (record.kind ?? 'foundation') === 'foundation' && !record.parent_id && !record.progress_source;
}

/** Shared durable request lifecycle for foundation and progress reading questions. */
export function useReadingRequest(sid: string, language: 'zh-CN' | 'en-US', enabled = true) {
  const client = useQueryClient();
  const list = useFoundationList(sid, enabled);
  const pendingRequest = useFoundationRequest(sid, language);
  const progressKey = ['reader-foundation-create', sid, language];
  const activeRequests = useMutationState({ filters: { mutationKey: progressKey, status: 'pending' },
    select: mutation => (mutation.state.variables as FoundationRequest | undefined)?.id });
  const progress = useExplanationProgress(progressKey, activeRequests.at(-1) ?? pendingRequest?.id);
  const scopeOf = (request: FoundationRequest) => ({ sid: request.sid ?? sid, locale: request.locale ?? language });
  const generation = useMutation({
    mutationKey: progressKey,
    mutationFn: async (request: FoundationRequest) => {
      const scope = scopeOf(request);
      const observed = beginExplanationProgress(client, ['reader-foundation-create', scope.sid, scope.locale], [{ key: request.id }]);
      try {
        const body = { request_id: request.id, question: request.draft.question.trim(), locale: scope.locale };
        const result = request.draft.parentId
          ? await api.askReaderFoundation(scope.sid, request.draft.parentId, body, observed.update)
          : await api.generateReaderFoundation(scope.sid, {
            ...body, ...(request.draft.progressSource ? { progress_source: { source_id: request.draft.progressSource.source_id } }
              : request.draft.sourceTaskId ? { source_task_id: request.draft.sourceTaskId } : {}),
          }, observed.update);
        const record = result.reader_foundation;
        if (record?.id !== request.id || record.locale !== scope.locale)
          throw new Error(scope.locale === 'zh-CN' ? '说明请求的结果尚未确认。' : 'The result of this explanation request is unconfirmed.');
        if (request.draft.parentId && (record.kind !== 'clarification' || record.parent_id !== request.draft.parentId))
          throw new Error(scope.locale === 'zh-CN' ? '追问与来源说明的对应关系尚未确认。' : 'The question’s connection to its source is unconfirmed.');
        if (request.draft.progressSource && (record.progress_source?.source_id !== request.draft.progressSource.source_id
          || (!request.draft.parentId && record.kind !== 'progress_answer')))
          throw new Error(scope.locale === 'zh-CN' ? '回答与这版进展说明的对应关系尚未确认。' : 'The answer’s connection to this progress explanation is unconfirmed.');
        return result;
      } finally {
        observed.finish();
        await client.invalidateQueries({ queryKey: foundationListKey(scope.sid) });
      }
    },
    onSuccess: (artifact, request) => {
      const scope = scopeOf(request);
      if (!request.draft.parentId && !request.draft.progressSource && isRootFoundation(artifact)
        && artifact.exists && artifact.reader_foundation?.state === 'complete'
        && foundationChoice(client, scope.sid, scope.locale).choice === request.beforeChoice)
        selectFoundation(client, scope.sid, scope.locale, artifact.reader_foundation.id);
      client.setQueryData<ArtifactInfo[]>(foundationListKey(scope.sid), previous => {
        const saved = previous?.find(item => item.path === artifact.path);
        if (saved && saved.reader_foundation?.state !== 'generating' && artifact.reader_foundation?.state === 'generating') return previous;
        return [...(previous ?? []).filter(item => item.path !== artifact.path), artifact];
      });
      if (!request.draft.progressSource) void client.invalidateQueries({ queryKey: ['map-copy', 'project', scope.sid] });
    },
    onError: (error, request) => {
      if (error instanceof ApiError && error.code === 'reader_source_unavailable') {
        const scope = scopeOf(request);
        saveFoundationRequest(client, scope.sid, scope.locale, { ...request, rejected: 'reader_source_unavailable' });
      }
    },
    retry: false,
  });
  const notes = (list.data ?? []).filter(item => item.reader_foundation?.locale === language);
  const waiting = notes.some(item => item.reader_foundation?.state === 'generating');
  const overdue = notes.some(item => item.reader_foundation?.state === 'generating' && item.reader_foundation.deadline_exceeded);
  const ownRequest = generation.variables && scopeOf(generation.variables);
  const sameScope = ownRequest?.sid === sid && ownRequest.locale === language;
  const creating = (sameScope && generation.isPending) || activeRequests.length > 0 || progress.active;
  const requestId = pendingRequest?.id ?? (sameScope ? generation.variables?.id : undefined);
  const requested = notes.find(item => item.reader_foundation?.id === requestId);
  const confirmedFailure = requested?.reader_foundation?.state === 'failed';
  const confirmedComplete = requested?.reader_foundation?.state === 'complete';
  const requestRejected = pendingRequest?.rejected === 'reader_source_unavailable';
  const unknownResult = !!pendingRequest && !requestRejected && !requested && !creating;
  const creationBlocked = creating || waiting || (!!pendingRequest && !requestRejected) || list.isPending || list.isError;
  useEffect(() => {
    if (pendingRequest && (confirmedFailure || confirmedComplete)) clearFoundationRequest(client, sid, language, pendingRequest.id);
  }, [client, sid, language, pendingRequest, confirmedFailure, confirmedComplete]);
  const submit = (draft: FoundationDraft): string | null => {
    if (!draft.question.trim() || creationBlocked) return null;
    const id = crypto.randomUUID();
    const request: FoundationRequest = { id, sid, locale: language, draft: { ...draft,
      ...(draft.progressSource ? { progressSource: { ...draft.progressSource } } : {}) },
      beforeChoice: foundationChoice(client, sid, language).choice };
    saveFoundationRequest(client, sid, language, request);
    generation.mutate(request);
    return id;
  };
  const retrySavedRequest = (request: FoundationRequest) => generation.mutate({ ...request,
    sid: request.sid ?? sid, locale: request.locale ?? language });
  return { client, list, notes, pendingRequest, progress, generation, waiting, overdue, creating, requested,
    confirmedFailure, confirmedComplete, requestRejected, unknownResult, creationBlocked, submit, retrySavedRequest };
}
