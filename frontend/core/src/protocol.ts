/** Frontend release expectations around the shared protocol validators. */
import { RELEASE_ID } from './release.generated.js';
import {
  inspectApiMeta as inspectSharedApiMeta,
  requireCompatibleApiMeta as requireSharedApiMeta,
  type ApiRuntimeExpectation,
} from '../../../packages/contracts/src/protocol.js';

export * from '../../../packages/contracts/src/api.js';
export * from '../../../packages/contracts/src/apiProtocol.generated.js';
export { describeApiRuntime, requireSnapshotContract, RELEASE_ARTIFACT_DRIFT_WARNING } from '../../../packages/contracts/src/protocol.js';
export type { ApiCompatibility, ApiRuntimeExpectation } from '../../../packages/contracts/src/protocol.js';

export function inspectApiMeta(value: unknown, expected: ApiRuntimeExpectation = { releaseId: RELEASE_ID }) {
  return inspectSharedApiMeta(value, expected);
}

export function requireCompatibleApiMeta(value: unknown, onWarning?: (warning: string) => void) {
  return requireSharedApiMeta(value, { releaseId: RELEASE_ID }, onWarning);
}
