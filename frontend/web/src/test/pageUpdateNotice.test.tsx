import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { PageUpdateNotice } from '../components/PageUpdateNotice';
import { observePageRelease } from '../lib/pageUpdate';

afterEach(() => vi.unstubAllGlobals());

it('shows a persistent refresh action without reloading or removing unsent input', () => {
  const reload = vi.fn();
  vi.stubGlobal('window', { location: { reload } });
  let renderer: ReactTestRenderer;
  act(() => { renderer = create(<><PageUpdateNotice /><textarea defaultValue="Keep this answer" /></>); });
  expect(renderer!.root.findAllByType('button')).toHaveLength(0);
  act(() => observePageRelease('next-release'));
  expect(renderer!.root.findByProps({ role: 'status' }).children).not.toHaveLength(0);
  expect(renderer!.root.findByType('textarea').props.defaultValue).toBe('Keep this answer');
  expect(reload).not.toHaveBeenCalled();
  act(() => renderer!.root.findByType('button').props.onClick());
  expect(reload).toHaveBeenCalledOnce();
  act(() => renderer!.unmount());
});
