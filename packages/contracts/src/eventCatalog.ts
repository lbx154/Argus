/** Canonical event names shared by the Web and terminal frontends. */
export const EVENT_TYPES = {
  PROVIDER_REQUEST_STARTED: 'provider.request.started',
  PROVIDER_REQUEST_COMPLETED: 'provider.request.completed',
  ENGINEER_PROGRESS: 'engineer.progress',
  LIFE_STATUS: 'life.status',
  LIFE_MISSION_COMPLETED: 'life.mission.completed',
  LIFE_PLANNER_VERDICT: 'life.planner.verdict',
  UI_OPERATOR: 'ui.operator',
  UI_ARGUS: 'ui.argus',
} as const;

export type CatalogEventType = (typeof EVENT_TYPES)[keyof typeof EVENT_TYPES];

export const SIGNAL_EVENT_TYPES = new Set<CatalogEventType>([
  EVENT_TYPES.PROVIDER_REQUEST_STARTED,
  EVENT_TYPES.PROVIDER_REQUEST_COMPLETED,
  EVENT_TYPES.LIFE_MISSION_COMPLETED,
  EVENT_TYPES.LIFE_PLANNER_VERDICT,
  EVENT_TYPES.UI_OPERATOR,
  EVENT_TYPES.UI_ARGUS,
]);
