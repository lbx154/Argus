import type { ProgressSourceRef } from '../../../core/src/types';
import type { CardCopy } from '../map/presentation';

export function isProgressSourceRef(value: unknown): value is ProgressSourceRef {
  if (!value || typeof value !== 'object') return false;
  const ref = value as ProgressSourceRef;
  return ['source_id', 'title', 'path', 'task_id', 'card_key'].every(key =>
    typeof ref[key as keyof ProgressSourceRef] === 'string' && String(ref[key as keyof ProgressSourceRef]).trim())
    && Number.isFinite(ref.generated_at) && ref.generated_at >= 0
    && Number.isSafeInteger(ref.copy_revision) && ref.copy_revision >= 0;
}

/** A neighboring card or a refreshed generation cannot supply this card's source. */
export function progressSourceForCard(card: CardCopy | undefined, cardKey: string, taskId: string): ProgressSourceRef | undefined {
  const ref = card?.progress_source;
  return card && isProgressSourceRef(ref) && ref.card_key === cardKey && ref.task_id === taskId
    && ref.generated_at === card.generated_at && ref.copy_revision === card.copy_revision
    && ref.title === card.title && card.source_snapshot?.card_key === cardKey
    && card.source_snapshot.task_id === taskId ? ref : undefined;
}

function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) !== Array.isArray(b)) return false;
  const left = a as Record<string, unknown>, right = b as Record<string, unknown>;
  const keys = Object.keys(left);
  return keys.length === Object.keys(right).length && keys.every(key => Object.hasOwn(right, key) && sameValue(left[key], right[key]));
}

/** Legacy GET registration may add only a source ref to the identical saved copy. */
export function attachProgressSource(previous: CardCopy, incoming: CardCopy, cardKey: string): CardCopy {
  if (previous.progress_source || previous.copy_revision === undefined) return previous;
  const source = incoming.progress_source;
  if (!isProgressSourceRef(source) || !progressSourceForCard(incoming, cardKey, source.task_id)) return previous;
  const { progress_source: _old, ...oldContent } = previous;
  const { progress_source: _new, ...newContent } = incoming;
  return sameValue(oldContent, newContent) ? { ...previous, progress_source: source } : previous;
}
