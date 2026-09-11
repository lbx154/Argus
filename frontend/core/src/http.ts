/** Small fetch-error normalizer shared by the browser and Ink clients. */

export interface HttpResponseLike {
  ok: boolean;
  status: number;
  statusText?: string;
  text(): Promise<string>;
}

export class ApiError extends Error {
  readonly status: number;
  readonly method: string;
  readonly path: string;
  /** A machine-readable reason from the service, such as "trial_route_unavailable"; empty when none was given. */
  readonly code: string;

  constructor(message: string, status: number, method: string, path: string, code = '') {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.method = method;
    this.path = path;
    this.code = code;
  }
}

interface ErrorBody {
  detail: string;
  code: string;
}

function detailFromBody(raw: string): ErrorBody {
  const compact = raw.replace(/\s+/g, ' ').trim();
  if (!compact) return { detail: '', code: '' };
  try {
    const data = JSON.parse(raw) as Record<string, unknown>;
    const code = typeof data.code === 'string' ? data.code.trim() : '';
    for (const key of ['detail', 'error', 'message']) {
      const value = data[key];
      if (typeof value === 'string' && value.trim()) return { detail: value.trim(), code };
      if (Array.isArray(value)) {
        const messages = value
          .map((item) => item && typeof item === 'object' ? String((item as Record<string, unknown>).msg ?? '') : '')
          .filter(Boolean);
        if (messages.length) return { detail: messages.join('; '), code };
      }
    }
    return { detail: '', code };
  } catch {
    // A proxy may return plain text. Keep it, but never dump a whole HTML page.
  }
  return {
    detail: compact.startsWith('<!DOCTYPE') || compact.startsWith('<html') ? '' : compact.slice(0, 240),
    code: '',
  };
}

export async function responseError(
  response: HttpResponseLike,
  method: string,
  path: string,
): Promise<ApiError> {
  let body: ErrorBody = { detail: '', code: '' };
  try {
    body = detailFromBody(await response.text());
  } catch {
    // Reading an error body is best-effort; status remains authoritative.
  }
  const status = response.status || 0;
  const prefix = `${method.toUpperCase()} ${path} → ${status || 'network error'}`;
  const suffix = body.detail || response.statusText?.trim() || '';
  return new ApiError(suffix ? `${prefix}: ${suffix}` : prefix, status, method.toUpperCase(), path, body.code);
}

export async function ensureResponseOk(
  response: HttpResponseLike,
  method: string,
  path: string,
): Promise<void> {
  if (!response.ok) throw await responseError(response, method, path);
}
