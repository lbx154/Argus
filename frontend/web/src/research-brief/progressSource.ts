import type { ProgressSourceRef } from '../../../core/src/types';

export function isProgressSourceRef(value: unknown): value is ProgressSourceRef {
  if (!value || typeof value !== 'object') return false;
  const ref = value as ProgressSourceRef;
  return ['source_id', 'title', 'path', 'task_id', 'card_key'].every(key =>
    typeof ref[key as keyof ProgressSourceRef] === 'string' && String(ref[key as keyof ProgressSourceRef]).trim())
    && Number.isFinite(ref.generated_at) && ref.generated_at >= 0
    && Number.isSafeInteger(ref.copy_revision) && ref.copy_revision >= 0;
}
