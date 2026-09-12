import { ApiError, ensureResponseOk } from '../../../core/src/http';
import { requestWithTimeout } from '../api';
import type {
  AdminIdentity, AuditPage, CollaborationDetail, CollectorStatus,
  DownloadedObservations, ObservationExport, ObservedPage, Overview, ProjectDirectory, Purpose, WorkspaceIdentity,
} from './types';

const READ_TIMEOUT_MS = 60_000;
const EXPORT_TIMEOUT_MS = 120_000;

export interface RequestOptions { signal?: AbortSignal }
export interface OverviewOptions extends RequestOptions {
  purpose?: Purpose;
  tenant?: string;
  query?: string;
  offset?: number;
}
export interface ProjectOptions extends RequestOptions {
  purpose?: Purpose;
  tenant: string;
  sid: string;
  taskId?: string | null;
}
export interface ObservationOptions extends ProjectOptions {
  cursor?: string | null;
  limit?: number;
}

/** A 401 returns an error for the UI; it never invokes user pairing or redirects. */
export class AdminAPIError extends ApiError {
  readonly loginURL: string | null;

  constructor(error: ApiError) {
    super(
      error.status === 401 ? '数据后台登录已过期，请重新登录。' : error.message,
      error.status, error.method, error.path,
      error.status === 401 ? 'admin_authentication_required' : error.code,
    );
    this.name = 'AdminAPIError';
    this.loginURL = error.status === 401 ? '/admin/login' : null;
  }
}

function parameters(values: Record<string, string | number | null | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
  }
  const query = params.toString();
  return query ? `?${query}` : '';
}

function projectPath(kind: 'collaboration' | 'observations', options: ProjectOptions): string {
  return `/admin/api/training/${kind}/${encodeURIComponent(options.tenant)}/${encodeURIComponent(options.sid)}`;
}

async function checked(response: Response, method: string, path: string): Promise<void> {
  try {
    await ensureResponseOk(response, method, path);
  } catch (error) {
    // The optional /invite/status read uses a different cookie. Its 401 must not
    // invalidate a still-authenticated administrator session.
    if (error instanceof ApiError && path.startsWith('/admin/')) throw new AdminAPIError(error);
    throw error;
  }
}

function malformed(path: string, method = 'GET'): AdminAPIError {
  return new AdminAPIError(new ApiError('数据后台响应格式不正确。', 502, method, path, 'invalid_admin_response'));
}

async function get<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return requestWithTimeout(path, {
    method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
    headers: { Accept: 'application/json' }, signal: options.signal,
  }, READ_TIMEOUT_MS, async (response) => {
    await checked(response, 'GET', path);
    try {
      return await response.json() as T;
    } catch {
      throw malformed(path);
    }
  });
}

/** Trust only a plain ZIP filename, never a server-provided filesystem path. */
export function observationFilename(disposition: string | null): string {
  const extended = disposition?.match(/(?:^|;)\s*filename\*=UTF-8''([^;]+)/i)?.[1];
  const regular = disposition?.match(/(?:^|;)\s*filename\s*=\s*(?:"([^"]*)"|([^;]+))/i);
  let value = regular?.[1] ?? regular?.[2]?.trim() ?? '';
  if (extended) {
    try { value = decodeURIComponent(extended.trim()); } catch { /* Use the plain fallback. */ }
  }
  return value.length > 0 && value.length <= 180 && /\.zip$/i.test(value)
    && !/[\x00-\x1f\x7f/\\:]/.test(value) && !value.startsWith('.')
    ? value : 'argus-observations.zip';
}

export const adminAPI = {
  overview(options: OverviewOptions = {}): Promise<Overview> {
    const path = '/admin/api/training/collaboration' + parameters({
      purpose: options.purpose ?? 'internal_training', offset: options.offset ?? 0,
      tenant: options.tenant, query: options.query,
    });
    return get<Overview>(path, options);
  },

  detail(options: ProjectOptions): Promise<CollaborationDetail> {
    return get<CollaborationDetail>(projectPath('collaboration', options) + parameters({
      purpose: options.purpose ?? 'internal_training', task_id: options.taskId,
    }), options);
  },

  observations(options: ObservationOptions): Promise<ObservedPage> {
    return get<ObservedPage>(projectPath('observations', options) + parameters({
      purpose: options.purpose ?? 'internal_training', task_id: options.taskId,
      cursor: options.cursor, limit: options.limit,
    }), options);
  },

  async identity(options: RequestOptions = {}): Promise<AdminIdentity> {
    const path = '/admin/status';
    const result = await get<AdminIdentity>(path, options);
    if (result?.role !== 'admin' || typeof result.readonly !== 'boolean') throw malformed(path);
    return result;
  },

  async workspaceIdentity(options: RequestOptions = {}): Promise<WorkspaceIdentity> {
    const path = '/invite/status';
    const result = await get<WorkspaceIdentity>(path, options);
    if (typeof result?.key_id !== 'string' || typeof result.role !== 'string' || typeof result.readonly !== 'boolean') {
      throw malformed(path);
    }
    // Do not copy quotas or other unrelated account metadata into this view.
    return { key_id: result.key_id, role: result.role, readonly: result.readonly };
  },

  collector(options: RequestOptions = {}): Promise<CollectorStatus> {
    return get<CollectorStatus>('/admin/api/research/status', options);
  },

  projectDirectory(tenant: string, options: RequestOptions = {}): Promise<ProjectDirectory> {
    return get<ProjectDirectory>(`/admin/api/tenants/${encodeURIComponent(tenant)}/projects`, options);
  },

  audit(options: RequestOptions & { limit?: number } = {}): Promise<AuditPage> {
    return get<AuditPage>('/admin/api/training/audit' + parameters({ limit: options.limit }), options);
  },

  exportObservations(selection: ObservationExport, options: RequestOptions = {}): Promise<DownloadedObservations> {
    const path = '/admin/api/training/export-observations';
    return requestWithTimeout(path, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      headers: { 'Content-Type': 'application/json', Accept: 'application/zip' },
      // Pick the permitted fields explicitly; no approval claims are manufactured.
      body: JSON.stringify({
        purpose: selection.purpose,
        projects: selection.projects.map(({ tenant_id, sid }) => ({ tenant_id, sid })),
      }),
      signal: options.signal,
    }, EXPORT_TIMEOUT_MS, async (response) => {
      await checked(response, 'POST', path);
      if (response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'application/zip') {
        throw malformed(path, 'POST');
      }
      return {
        blob: await response.blob(),
        filename: observationFilename(response.headers.get('Content-Disposition')),
      };
    });
  },
};
