import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { ConfigSnapshot, Role } from '../api';
import { ComposerRuntime } from '../components/ComposerRuntime';

let config: Pick<ConfigSnapshot, 'trial_mode'> = {};
vi.mock('../hooks', () => ({ useConfig: () => ({ data: config }) }));
vi.mock('../lib/desktopBridge', () => ({ canOpenDesktopSettings: () => true, openDesktopTrialSettings: vi.fn() }));
const roles: Role[] = [
  { role: 'manager', model: 'manager-model', backend: 'copilot', effort: 'high', active: false },
  { role: 'engineer', model: 'engineer-model', backend: 'claude', effort: null, active: true },
] as Role[];

describe('composer runtime information', () => {
  it('shows the active role backend/model and returns to the manager when stopped', () => {
    config = { trial_mode: false };
    const active = renderToStaticMarkup(<ComposerRuntime sid="s-test" roles={roles} running />);
    expect(active).toContain('engineer-model');
    expect(active).toContain('Claude');
    expect(active).not.toContain('manager-model');
    const stopped = renderToStaticMarkup(<ComposerRuntime sid="s-test" roles={roles} running={false} />);
    expect(stopped).toContain('manager-model');
    expect(stopped).not.toContain('engineer-model');
  });

  it('shows the hosted trial model and Key replacement independently of old role settings', () => {
    config = { trial_mode: true };
    const html = renderToStaticMarkup(<ComposerRuntime sid="s-test" roles={roles} running />);
    expect(html).toContain('GPT-5.5');
    expect(html).toContain('high');
    expect(html).toContain('Change Key');
    expect(html).not.toContain('engineer-model');
  });
});
