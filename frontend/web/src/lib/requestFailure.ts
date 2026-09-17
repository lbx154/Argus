import { ApiError } from '../../../core/src/http';

type Translate = (key: string) => string;

export interface RequestFailure {
  /** The sentence for the page. */
  text: string;
  /** The request line and status, kept for a fold or a tooltip. */
  technical: string;
  /** True when this Argus refuses the route on purpose, so the feature is simply absent here. */
  refused: boolean;
}

// The hosted trial answers routes it does not offer with 403 and the code
// "trial_route_unavailable"; an older portal answers 403 or 404 without a
// code. Either way the feature is absent, which is a fact, not a failure.
export function routeRefused(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  return error.code === 'trial_route_unavailable' || error.status === 403 || error.status === 404;
}

export function requestFailureText(error: unknown, t: Translate): RequestFailure {
  const refused = routeRefused(error);
  return {
    text: t(refused ? 'operations.unavailableHere' : 'operations.requestFailed'),
    technical: error instanceof Error ? error.message : String(error || ''),
    refused,
  };
}
