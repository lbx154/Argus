import { describe, expect, it, vi } from 'vitest';

import { createSessionFast } from '../lib/createDaemonFlow';

describe('fast daemon creation flow', () => {
  it('creates an idle session first and defers the expensive campaign start', async () => {
    const createDaemon = vi.fn(async () => ({ sid: 's-fast' }));
    const setContinuous = vi.fn(async () => ({ ok: true }));

    const result = await createSessionFast(
      { createDaemon, setContinuous },
      'campaign',
      'keep optimizing',
      '/workspace/output',
    );

    expect(createDaemon).toHaveBeenCalledWith('', 'campaign', '/workspace/output');
    expect(setContinuous).not.toHaveBeenCalled();
    expect(result.created.sid).toBe('s-fast');

    await result.startCampaign?.();
    expect(setContinuous).toHaveBeenCalledWith('s-fast', true, 'keep optimizing');
  });

  it('does not create background work for an idle conversation', async () => {
    const createDaemon = vi.fn(async () => ({ sid: 's-idle' }));
    const setContinuous = vi.fn(async () => ({ ok: true }));

    const result = await createSessionFast(
      { createDaemon, setContinuous },
      'chat',
      '   ',
    );

    expect(result.startCampaign).toBeNull();
    expect(setContinuous).not.toHaveBeenCalled();
  });
});
