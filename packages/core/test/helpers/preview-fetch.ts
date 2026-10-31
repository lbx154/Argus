import { request } from 'node:http';

/** Node does not implement browsers' *.localhost resolution. Retain the public Host
 * on the wire while connecting to IPv4 loopback, including on redirected requests. */
export async function previewFetch(input: string | URL, init: RequestInit = {}): Promise<Response> {
  const url = new URL(input);
  if (!url.hostname.endsWith('.localhost')) return fetch(url, init);
  const headers = new Headers(init.headers);
  headers.set('host', url.host);
  const response = await new Promise<Response>((accept, reject) => {
    const req = request({ hostname: '127.0.0.1', port: url.port, path: url.pathname + url.search,
      method: init.method ?? 'GET', headers: Object.fromEntries(headers) }, res => {
      const chunks: Buffer[] = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => {
        const response = new Response(init.method === 'HEAD' ? null : Buffer.concat(chunks), {
          status: res.statusCode!, headers: res.headers as Record<string, string>,
        });
        Object.defineProperty(response, 'url', { value: url.href });
        accept(response);
      });
      res.on('error', reject);
    });
    req.on('error', reject);
    req.end();
  });
  if ([301, 302, 303, 307, 308].includes(response.status) && init.redirect !== 'manual') {
    return previewFetch(new URL(response.headers.get('location')!, url), init);
  }
  return response;
}
