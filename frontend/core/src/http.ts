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

  constructor(message: string, status: number, method: string, path: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.method = method;
    this.path = path;
  }
}

function detailFromBody(raw: string): string {
  const compact = raw.replace(/\s+/g, ' ').trim();
  if (!compact) return '';
  try {
    const data = JSON.parse(raw) as Record<string, unknown>;
    for (const key of ['detail', 'error', 'message']) {
      const value = data[key];
      if (typeof value === 'string' && value.trim()) return value.trim();
      if (Array.isArray(value)) {
        const messages = value
          .map((item) => item && typeof item === 'object' ? String((item as Record<string, unknown>).msg ?? '') : '')
          .filter(Boolean);
        if (messages.length) return messages.join('; ');
      }
    }
  } catch {
    // A proxy may return plain text. Keep it, but never dump a whole HTML page.
  }
  return compact.startsWith('<!DOCTYPE') || compact.startsWith('<html') ? '' : compact.slice(0, 240);
}

export async function responseError(
  response: HttpResponseLike,
  method: string,
  path: string,
): Promise<ApiError> {
  let detail = '';
  try {
    detail = detailFromBody(await response.text());
  } catch {
    // Reading an error body is best-effort; status remains authoritative.
  }
  const status = response.status || 0;
  const prefix = `${method.toUpperCase()} ${path} → ${status || 'network error'}`;
  const suffix = detail || response.statusText?.trim() || '';
  return new ApiError(suffix ? `${prefix}: ${suffix}` : prefix, status, method.toUpperCase(), path);
}

export async function ensureResponseOk(
  response: HttpResponseLike,
  method: string,
  path: string,
): Promise<void> {
  if (!response.ok) throw await responseError(response, method, path);
}
