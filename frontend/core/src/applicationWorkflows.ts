export const APPLICATION_WORKFLOWS = 'application.workflows.v1';

export function workflowActorLabel(role: string, executionLabel = 'Tool execution'): string {
  return role === 'host' ? executionLabel : role;
}

export interface WorkflowCatalog {
  domains: Record<string, string[]>;
  roles: string[];
  projects: { id: string; project: string }[];
}

export interface WorkflowCreate {
  project: string;
  objective: string;
  domain: string;
  profile: string;
  mode: 'design' | 'verify';
  timeout_s?: number;
  max_rounds?: number;
}

export interface WorkflowAction {
  action: 'run' | 'resume' | 'recover' | 'chat' | 'stop';
  allow_model?: boolean;
  message?: string;
  reason?: string;
}

export interface WorkflowSnapshot {
  id: string;
  project: string;
  objective: string;
  mode: string;
  status: string;
  phase: string;
  independently_accepted: boolean;
  plan: null | { roadmap: string[]; spec: string; checks: { id: string; argv?: string[]; provider_stage?: string }[] };
  feedback: string;
  resumes: string[];
  repairs: number;
  messages: { user: string; reply: string }[];
  calls: {
    role: string; operation: string; status: string; detail: string; folder: string;
    started_at: number | null; duration_s: number | null; live: boolean;
    decision: null | { status: string; reason: string };
  }[];
  jobs: {
    id: string; action: string; status: string; started_at: number;
    finished_at?: number; outcome?: string; detail: string; log: string;
  }[];
}

export function workflowClient(
  get: <T>(path: string) => Promise<T>,
  post: <T>(path: string, body: unknown) => Promise<T>,
) {
  const root = '/api/application-workflows';
  const project = (id: string) => `${root}/${encodeURIComponent(id)}`;
  return {
    catalog: () => get<WorkflowCatalog>(root),
    create: (body: WorkflowCreate) => post<WorkflowSnapshot>(root, body),
    open: (path: string) => post<WorkflowSnapshot>(`${root}/open`, { project: path }),
    snapshot: (id: string) => get<WorkflowSnapshot>(project(id)),
    action: (id: string, body: WorkflowAction) => post<WorkflowSnapshot>(project(id), body),
    document: (id: string, path: string) => get<{ path: string; text: string; truncated: boolean }>(
      `${project(id)}/document?path=${encodeURIComponent(path)}`,
    ),
  };
}
