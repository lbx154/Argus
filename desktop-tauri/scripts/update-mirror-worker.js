/**
 * Argus signed-update mirror for Cloudflare Workers.
 *
 * It follows the Tianshu two-channel pattern: a mirror may accelerate access to
 * the public GitHub Release, but it never changes package bytes or signatures.
 * The Tauri client still verifies the minisign signature embedded in latest.json.
 *
 * Required Worker variables:
 *   GITHUB_OWNER=lbx154
 *   GITHUB_REPO=Argus
 *
 * Routes:
 *   /argus/latest.json
 *   /argus/releases/download/<tag>/<asset>
 */

const CACHE_ASSET = 'public, max-age=86400, s-maxage=2592000';
const CACHE_MANIFEST = 'no-cache, no-store, must-revalidate';
const ALLOWED_ASSET = /^(Argus[_-].+\.(exe|exe\.sig)|latest\.json)$/i;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const owner = String(env.GITHUB_OWNER || 'lbx154').trim();
    const repo = String(env.GITHUB_REPO || 'Argus').trim();
    const base = `https://github.com/${owner}/${repo}`;

    if (url.pathname === '/' || url.pathname === '/health') {
      return new Response('argus-update-mirror ok\n', {
        headers: { 'content-type': 'text/plain; charset=utf-8' },
      });
    }
    if (url.pathname === '/argus/latest.json') {
      return proxyManifest(`${base}/releases/latest/download/latest.json`, base, url.origin);
    }
    const match = url.pathname.match(/^\/argus\/releases\/download\/([^/]+)\/([^/]+)$/);
    if (!match) return new Response('not found\n', { status: 404 });
    const [, tag, asset] = match;
    if (!ALLOWED_ASSET.test(asset)) return new Response('asset not allowed\n', { status: 403 });
    return proxyAsset(`${base}/releases/download/${encodeURIComponent(tag)}/${encodeURIComponent(asset)}`, request);
  },
};

async function proxyManifest(source, githubBase, mirrorOrigin) {
  try {
    const upstream = await fetch(source, {
      headers: { 'user-agent': 'argus-update-mirror/1.0' },
      redirect: 'follow',
    });
    if (!upstream.ok) return new Response(`upstream ${upstream.status}\n`, { status: upstream.status });
    const original = await upstream.text();
    // Rewriting URLs is safe: each selected package is still verified against
    // the immutable signature in the manifest by tauri-plugin-updater.
    const body = original.replaceAll(
      `${githubBase}/releases/download/`,
      `${mirrorOrigin}/argus/releases/download/`,
    );
    return new Response(body, {
      status: 200,
      headers: {
        'cache-control': CACHE_MANIFEST,
        'content-type': 'application/json; charset=utf-8',
        'access-control-allow-origin': '*',
      },
    });
  } catch (error) {
    return new Response(`mirror error: ${String(error)}\n`, { status: 502 });
  }
}

async function proxyAsset(source, request) {
  try {
    const upstream = await fetch(source, {
      method: request.method,
      headers: { 'user-agent': 'argus-update-mirror/1.0' },
      redirect: 'follow',
    });
    if (!upstream.ok) return new Response(`upstream ${upstream.status}\n`, { status: upstream.status });
    const headers = new Headers(upstream.headers);
    headers.set('cache-control', CACHE_ASSET);
    headers.set('access-control-allow-origin', '*');
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch (error) {
    return new Response(`mirror error: ${String(error)}\n`, { status: 502 });
  }
}
