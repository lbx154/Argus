import { expect, it } from 'vitest';
import { ApiError } from '../../../core/src/http';
import { errorText } from '../lib/format';

it('names the run limit in plain words instead of the server sentence', () => {
  const detail = 'active daemon limit 2 reached; choose one running session to park before starting this work';
  const error = new ApiError(detail, 409, 'POST', '/api/projects/s-1/daemon/start', '', detail);
  const text = errorText(error);
  expect(text).not.toMatch(/daemon|park|session/i);
  expect(text).toMatch(/2/);
});
