import { renderToStaticMarkup } from 'react-dom/server';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import type { ConfigSnapshot, Role } from '../api';
import { ComposerRuntime } from '../components/ComposerRuntime';

let config: Pick<ConfigSnapshot, 'trial_mode'> & Partial<Pick<ConfigSnapshot, 'roles'>> = {};
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

describe('composer runtime follows the real calls', () => {
  const lastCall = { backend: 'copilot', model: 'called-model', effort: 'medium', run_label: 'engineer-r1', ts: 1 };
  const configRoles = (model: string, backend = 'copilot') => [
    { role: 'engineer', backend, model, reasoning_effort: null },
    { role: 'manager', backend, model, reasoning_effort: null },
  ] as unknown as ConfigSnapshot['roles'];

  it('shows the backend, model and effort the latest call actually used', () => {
    config = { trial_mode: false, roles: configRoles('called-model') } as typeof config;
    const html = renderToStaticMarkup(<ComposerRuntime sid="s" roles={roles} running lastCall={lastCall} />);
    expect(html).toContain('called-model');
    expect(html).toContain('medium');
    expect(html).not.toContain('engineer-model');
    expect(html).not.toContain('data-runtime-next');
  });

  it('says what the next call will use right after settings change', () => {
    config = { trial_mode: false, roles: configRoles('applied-model', 'claude') } as typeof config;
    const html = renderToStaticMarkup(<ComposerRuntime sid="s" roles={roles} running lastCall={lastCall} />);
    expect(html).toContain('called-model');
    expect(html).toMatch(/next call: Claude · applied-model/);
  });

  it('opens the model settings when clicked', () => {
    config = { trial_mode: false, roles: configRoles('called-model') } as typeof config;
    const open = vi.fn();
    let tree: ReactTestRenderer | undefined;
    act(() => { tree = create(<ComposerRuntime sid="s" roles={roles} running lastCall={lastCall} onOpenSettings={open} />); });
    act(() => tree!.root.findByProps({ 'data-runtime-open-settings': true }).props.onClick());
    expect(open).toHaveBeenCalledTimes(1);
    act(() => tree!.unmount());
  });
});
