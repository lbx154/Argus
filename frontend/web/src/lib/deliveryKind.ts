import type { DeliveryReceipt } from '../../../core/src/types';

export type DeliveryHeadingKey = 'mission.deliveryCertified' | 'mission.stagePackage' | 'mission.taskCompleted' | 'mission.workSaved';

/** A reviewed interim research package: useful on its own, but the overall goal stays open. */
export function isStagePackage(delivery: Pick<DeliveryReceipt, 'kind'> | null | undefined): boolean {
  return delivery?.kind === 'stage_package';
}

export function deliveryHeadingKey(delivery: Pick<DeliveryReceipt, 'kind'> | null | undefined): DeliveryHeadingKey {
  if (delivery?.kind === 'work_saved') return 'mission.workSaved';
  if (delivery?.kind === 'submission_certified') return 'mission.deliveryCertified';
  if (isStagePackage(delivery)) return 'mission.stagePackage';
  return 'mission.taskCompleted';
}
