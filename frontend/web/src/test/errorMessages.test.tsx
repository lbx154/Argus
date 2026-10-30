import { readFileSync } from 'node:fs';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, responseError } from '../../../core/src/http';
import { LocalArgusUnavailableError } from '../api';
import { ErrorMessage } from '../components/ErrorMessage';
import { I18nProvider, translate, type Locale } from '../i18n';
import { errorText } from '../lib/format';
import { plainRowText } from '../lib/plainProgress';

// Captured from the served cockpit before the fix, for the two reported failures.
const recorded = JSON.parse(readFileSync(new URL('./fixtures/service-errors.json', import.meta.url), 'utf8'));
afterEach(() => vi.unstubAllGlobals());

function render(error: unknown, locale: Locale, code?: string) {
  vi.stubGlobal('localStorage', { getItem: () => locale });
  return renderToStaticMarkup(<I18nProvider><ErrorMessage error={error} code={code} /></I18nProvider>);
}

describe.each(['zh-CN', 'en'] as const)('error messages in %s', locale => {
  it('says a request that never reached Argus is the connection, whichever request it was', () => {
    expect(errorText(new LocalArgusUnavailableError('POST', '/api/projects/p-1/daemon/stop'), locale)).toBe(translate('error.network', {}, locale));
  });

  it('replays the reported directory response through HTTP decoding and keeps its raw reason folded', async () => {
    const response = new Response(JSON.stringify({ ...recorded.directory.body, code: 'invalid_workdir' }), { status: recorded.directory.status });
    const error = await responseError(response, 'POST', '/api/daemons');
    expect(errorText(error, locale)).toBe(translate('error.invalid_workdir', {}, locale));
    const html = render(error, locale);
    expect(html.slice(0, html.indexOf('<details'))).not.toContain('workdir is not a directory');
    expect(html).toContain(recorded.directory.body.detail);
    expect(html).not.toContain('<details open');
    expect(html).toContain(translate('error.details', {}, locale));
  });

  it('replays the empty rewrite with a localized next step and unchanged input', () => {
    const { error, rewritten, original } = recorded.rewrite.body;
    expect(rewritten).toBe(original);
    expect(errorText(error, locale, 'rewrite_failed')).toBe(translate('error.rewrite_failed', {}, locale));
    const html = render(error, locale, 'rewrite_failed');
    expect(html.slice(0, html.indexOf('<details'))).not.toContain(error);
    expect(html).toContain(error);
    expect(html).not.toContain('<details open');
  });

  it.each([0, 400, 401, 403, 404, 409, 413, 415, 422, 429, 500, 503])('never promotes an unknown service reason at status %s', status => {
    const raw = 'Unrecognized upstream failure';
    const error = new ApiError(raw, status, 'POST', '/api/projects/a', 'future_error_code', raw);
    const message = errorText(error, locale);
    expect(message).not.toContain(raw);
    expect(message).not.toContain('future_error_code');
    expect(message).not.toMatch(/^error\./);
    if (locale === 'zh-CN') expect(message).toMatch(/[\u4e00-\u9fff]/);
    expect(render(error, locale)).toContain(raw);
  });

  it('keeps command output out of task failure summaries, with the original reason in detail', () => {
    const raw = 'CLI exited with code 1: login required';
    const event = { type: 'life.status', operator_alert: true, text: raw };
    const line = plainRowText(event, { text: raw } as never, locale);
    expect(line.text).toBe(translate('error.agent_failed', {}, locale));
    expect(line.technical).toBe(raw);
  });
});

it('localizes unknown thrown values', () => {
  vi.stubGlobal('localStorage', { getItem: () => 'zh-CN' });
  for (const error of [new Error('socket hang up'), 'plain CLI error', undefined, { error: 'unknown shape' }]) {
    expect(errorText(error)).toBe(translate('error.unknown'));
  }
});
