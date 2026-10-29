import { renderToStaticMarkup } from 'react-dom/server';
import { act, create } from 'react-test-renderer';
import { expect, it, vi } from 'vitest';
import { PluginCard, PluginEntries, type Plugin } from '../components/PluginLauncher';

const ready: Plugin = { id: 'bench', name: 'Bench', description: 'Solve a structure', version: '1.2', url: 'http://127.0.0.1:9100/', reason: '' };

it('opens a deployed plugin and shows the reason for an invalid URL', () => {
  const launch = vi.fn();
  let view!: ReturnType<typeof create>;
  act(() => { view = create(<PluginCard plugin={ready} running={false} onLaunch={launch} />); });
  act(() => view.root.findByType('button').props.onClick());
  expect(launch).toHaveBeenCalledOnce();
  const invalid = renderToStaticMarkup(<PluginCard plugin={{ ...ready, url: '', reason: 'No http(s) URL to open.' }} running={false} onLaunch={launch} />);
  expect(invalid).toContain('No http(s) URL to open.');
  expect(invalid).not.toContain('<button');
  act(() => view.unmount());
});

it('keeps plugin entries and their list available without installation controls', () => {
  const html = renderToStaticMarkup(<PluginEntries plugins={[ready]} pending={null} onLaunch={vi.fn()} onManage={vi.fn()} />);
  expect(html).toContain('aria-label="Bench"');
  expect(html).toContain('aria-label="View Bench"');
  expect(renderToStaticMarkup(<PluginEntries plugins={[]} pending={null} onLaunch={vi.fn()} onManage={vi.fn()} />)).toContain('aria-label="Plugins"');
  const card = renderToStaticMarkup(<PluginCard plugin={ready} running={false} onLaunch={vi.fn()} />);
  expect(card).toContain('Open workbench');
  expect(card).not.toMatch(/Install|Repair|Update|Enable|license|environment/i);
});
