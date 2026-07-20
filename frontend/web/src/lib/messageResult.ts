export function managerMessageError(result: Record<string, unknown>): string | null {
  if (result.kind !== 'error') return null;
  const reply = typeof result.reply === 'string' ? result.reply.trim() : '';
  return reply || 'Manager could not handle this message.';
}

interface ManagerMessageCompletion {
  dispatchTask: (result: Record<string, unknown>) => void;
  notifyError: (message: string) => void;
  refetchTranscript: () => void;
}

export function finishManagerMessage(
  result: Record<string, unknown>,
  completion: ManagerMessageCompletion,
): void {
  if (result.kind === 'task') completion.dispatchTask(result);
  const error = managerMessageError(result);
  if (error) completion.notifyError(error);
  completion.refetchTranscript();
}
