import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { WorkPauseBanner } from '../components/WorkPauseBanner';
import { errorText } from '../lib/format';
import { requestFailureText } from '../lib/requestFailure';
import { ApiError } from '../../../core/src/http';

it('shows the runtime reason as text and clears the notice when work resumes', () => {
  const reason = 'Maintenance until 18:00 <contact owner>';
  const html = renderToStaticMarkup(<WorkPauseBanner pause={{ reason, since: 123 }} />);
  expect(html).toContain('role="alert"');
  expect(html).toContain('Maintenance until 18:00 &lt;contact owner&gt;');
  expect(renderToStaticMarkup(<WorkPauseBanner pause={null} />)).toBe('');
  expect(renderToStaticMarkup(<WorkPauseBanner pause={undefined} />)).toBe('');
  expect(errorText(new Error(reason))).toBe(reason);
});

it.each([403, 404, 500])('keeps an HTTP %i failure visible as a failed request', status => {
  const error = new ApiError(`GET /api/control → ${status}`, status, 'GET', '/api/control');
  expect(requestFailureText(error, key => key)).toEqual({ text: 'operations.requestFailed', technical: error.message });
});
