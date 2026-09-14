export interface TimelineTask {
  id: string;
  title: string;
  phase: string;
  duration_hours: [number, number, number];
  resources?: Record<string, number>;
  depends_on?: string[];
  basis: string;
  difficulty?: string;
  optional?: boolean;
  status?: 'pending' | 'running' | 'completed' | 'failed' | 'blocked';
  actual_start_hours?: number;
  actual_finish_hours?: number;
  remaining_hours?: [number, number, number];
  reason?: string;
  evidence?: string[];
}
export interface TimelineInput {
  selected_proposal_id: string;
  resources: Record<string, number>;
  now_hours: number;
  deadline_hours?: number | null;
  defer_optional: boolean;
  proposals: Array<{ id: string; title: string; assumptions?: string[]; tasks: TimelineTask[] }>;
}
export interface TimelineForecast {
  id: string;
  title: string;
  finish_hours: { lower: number; expected: number; upper: number } | null;
  remaining_hours: number | null;
  deadline_gap_hours: number | null;
  deferred_task_ids: string[];
  blocked_tasks: Array<{ id: string; reason: string }>;
  failed_tasks: Array<{ id: string; reason: string }>;
  schedule: Array<{
    id: string; title: string; phase: string; status: string;
    start_hours: number; finish_hours: number; resource_wait_hours: number;
    resources: Record<string, number>; basis: string; reason: string;
  }>;
}
export interface TimelineReport {
  selected_proposal_id: string;
  proposals: TimelineForecast[];
  revision?: {
    reason: string; previous_version: number;
    baseline_finish_hours: number | null;
    previous_finish_hours: number | null;
    baseline_delta_hours: number | null;
    task_variances: Array<{ id: string; delay_hours: number; reason: string; observed: boolean; evidence: string[] }>;
  };
}
export interface TimelineEntry {
  version: number;
  created_at: string;
  reason: string;
  input: TimelineInput;
  report: TimelineReport;
}

export function normalizeTimelineInput(value: TimelineInput): TimelineInput {
  return { ...value, resources: value.resources ?? {}, now_hours: value.now_hours ?? 0, defer_optional: value.defer_optional ?? false };
}
