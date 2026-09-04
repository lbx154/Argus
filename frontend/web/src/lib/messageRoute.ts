import type { MessageRouteOverride } from '../api';

// v1 persisted Desktop's implicit Task default. Use a new key so existing
// installations return to Auto instead of treating that default as a choice.
export const MESSAGE_ROUTE_KEY = 'argus.message.route.v2';

export function initialMessageRoute(): MessageRouteOverride {
  try {
    const stored = localStorage.getItem(MESSAGE_ROUTE_KEY);
    if (stored === 'auto' || stored === 'chat' || stored === 'task') return stored;
  } catch {
    // Storage is optional; Auto remains the safe default.
  }
  return 'auto';
}
