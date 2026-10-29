import { renderToStaticMarkup } from 'react-dom/server';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ConfigSnapshot, ModelChoices, Role } from '../api';
import { ComposerRuntime } from '../components/ComposerRuntime';

let config: Partial<Pick<ConfigSnapshot, 'roles' | 'backends'>> = {};
const backends = { copilot: 'Copilot CLI', claude: 'Claude Code' };
let choices: ModelChoices | undefined;
beforeEach(() => { choices = { backend: 'copilot', options: [{ model: 'backend-default', default: true, price: null }] }; });
vi.mock('../hooks', () => ({ useConfig: () => ({ data: config }), useModelChoices: () => ({ data: choices }) }));
vi.mock('../components/ModelPicker', () => ({
  ModelPicker: ({ open, onOpenSettings }: { open: boolean; onOpenSettings?: () => void }) =>
    open ? <button type="button" data-picker-open onClick={onOpenSettings} /> : null,
}));
const roles: Role[] = [
  { role: 'manager', model: 'manager-model', backend: 'copilot', effort: 'high', active: false },
  { role: 'engineer', model: 'engineer-model', backend: 'claude', effort: null, active: true },
] as Role[];

describe('composer runtime information', () => {
  it('does not treat a role snapshot of historical models as an explicit model setting', () => {
    config = { backends, roles: [] };
    const active = renderToStaticMarkup(<ComposerRuntime sid="s-test" roles={roles} running />);
    expect(active).toContain('Automatic');
    expect(active).toContain('Claude Code');
    expect(active).not.toContain('manager-model');
    const stopped = renderToStaticMarkup(<ComposerRuntime sid="s-test" roles={roles} running={false} />);
    expect(stopped).toContain('Automatic (backend-default)');
    expect(stopped).not.toContain('engineer-model');
  });


});

describe('composer runtime follows the real calls', () => {
  const now = () => Date.now() / 1000;
  const lastCall = { backend: 'copilot', model: 'called-model', effort: 'medium', run_label: 'engineer-r1', role: 'engineer', ts: now() - 60 };
  const configRoles = (model: string, backend = 'copilot') => [
    { role: 'engineer', backend, model, reasoning_effort: null },
    { role: 'manager', backend, model, reasoning_effort: null },
  ] as unknown as ConfigSnapshot['roles'];

  it('shows the backend, model and effort the latest call actually used', () => {
    config = { roles: configRoles('called-model') } as typeof config;
    const html = renderToStaticMarkup(<ComposerRuntime sid="s" roles={roles} running lastCall={lastCall} />);
    expect(html).toContain('called-model');
    expect(html).toContain('medium');
    expect(html).not.toContain('engineer-model');
    expect(html).not.toContain('data-runtime-earlier');
  });

  it('names the model just chosen at once, and the one a turn under way keeps', () => {
    config = { roles: configRoles('applied-model', 'claude'), backends } as typeof config;
    const html = renderToStaticMarkup(<ComposerRuntime sid="s" roles={roles} running lastCall={lastCall} />);
    expect(html).toMatch(/class="composer-model-name">applied-model</);
    expect(html).toMatch(/this turn: called-model/);
    expect(renderToStaticMarkup(<ComposerRuntime sid="s" roles={roles} running={false} lastCall={lastCall} />)).not.toContain('called-model');
  });

  it('keeps Automatic and the backend default for new projects and after a reply', () => {
    config = { roles: configRoles('') } as typeof config;
    for (const call of [undefined, lastCall]) {
      const html = renderToStaticMarkup(<ComposerRuntime sid="s" roles={roles} running={false} lastCall={call} />);
      expect(html).toMatch(/class="composer-model-name">Automatic \(backend-default\)</);
      expect(html).not.toContain('called-model');
      expect(html).not.toContain('data-runtime-earlier');
    }
  });

  it('names a running call separately when the next automatic turn uses a different default', () => {
    config = { roles: configRoles('') } as typeof config;
    const html = renderToStaticMarkup(<ComposerRuntime sid="s" roles={roles} running lastCall={lastCall} />);
    expect(html).toContain('Automatic (backend-default)');
    expect(html).toContain('this turn: called-model');
  });

  it('uses the current backend default and leaves an unreported default unnamed', () => {
    config = { roles: configRoles('', 'claude') } as typeof config;
    const stale = renderToStaticMarkup(<ComposerRuntime sid="s" roles={roles} running={false} />);
    expect(stale).not.toContain('backend-default');
    choices = { backend: 'claude', options: [{ model: 'claude-default', default: true, price: null }] };
    expect(renderToStaticMarkup(<ComposerRuntime sid="s" roles={roles} running={false} />)).toContain('Automatic (claude-default)');
    choices = undefined;
    const unknown = renderToStaticMarkup(<ComposerRuntime sid="s" roles={roles} running={false} />);
    expect(unknown).toMatch(/class="composer-model-name">Automatic</);
  });

  it('matches the call to the role that made it, not to the manager by default', () => {
    // A work label such as "self-implement" names no role; the backend says
    // which role made the call, and only that role's settings are compared.
    config = { roles: [
      { role: 'engineer', backend: 'copilot', model: 'called-model', reasoning_effort: null },
      { role: 'manager', backend: 'copilot', model: 'other-model', reasoning_effort: null },
    ] as unknown as ConfigSnapshot['roles'] } as typeof config;
    const call = { ...lastCall, run_label: 'self-implement', role: 'engineer' };
    const html = renderToStaticMarkup(<ComposerRuntime sid="s" roles={roles} running lastCall={call} />);
    expect(html).toContain('called-model');
    expect(html).not.toContain('data-runtime-earlier');
  });

  it('does not present a call from days ago as what the project runs on now', () => {
    config = { roles: configRoles('applied-model', 'claude') } as typeof config;
    const old = { ...lastCall, backend: 'pi', model: 'old-model', ts: now() - 12 * 86400 };
    const html = renderToStaticMarkup(<ComposerRuntime sid="s" roles={roles} running={false} lastCall={old} />);
    expect(html).not.toContain('old-model');
    expect(html).not.toContain('data-runtime-earlier');
    expect(html).toContain('applied-model');
  });

  it('opens the model chooser when clicked, which leads on to the settings', () => {
    config = { roles: configRoles('called-model') } as typeof config;
    const open = vi.fn();
    let tree: ReactTestRenderer | undefined;
    act(() => { tree = create(<ComposerRuntime sid="s" roles={roles} running lastCall={lastCall} onOpenSettings={open} />); });
    expect(tree!.root.findAllByProps({ 'data-picker-open': true })).toHaveLength(0);
    act(() => tree!.root.findByProps({ 'data-runtime-open-models': true }).props.onClick());
    act(() => tree!.root.findByProps({ 'data-picker-open': true }).props.onClick());
    expect(open).toHaveBeenCalledTimes(1);
    act(() => tree!.unmount());
  });
});
