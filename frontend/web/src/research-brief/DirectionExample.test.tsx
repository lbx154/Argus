import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { DirectionExample, directionCount, offersDirectionExample } from './DirectionExample';

let renderer: ReactTestRenderer | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; vi.unstubAllGlobals(); });

it('counts dependent and zero vectors without the integer-multiple fallacy', () => {
  expect(directionCount([2, 0], [3, 0])).toBe(1);
  expect(directionCount([1, 0], [1.5, 0])).toBe(1);
  expect(directionCount([0, 0], [0, 1])).toBe(1);
  expect(directionCount([1, 0], [0, 0])).toBe(1);
  expect(directionCount([0, 0], [0, 0])).toBe(0);
  expect(directionCount([1, 0], [0, 1])).toBe(2);
});

it('offers the illustration for explicit independence terms, not unrelated uses of independence', () => {
  const concept = (value: string) => ({ name: '', explanation: value, example: '', connection: '' });
  for (const term of ['独立方向', '独立的方向', '线性无关', '线性独立', 'independent directions', 'linear independence', 'linearly independent']) {
    expect(offersDirectionExample(concept(term))).toBe(true);
  }
  for (const term of ['独立复核', '独立事件', '研究方向', 'Picard number', 'independent verification']) {
    expect(offersDirectionExample(concept(term))).toBe(false);
  }
  expect(offersDirectionExample(null)).toBe(false);
});

it('updates the real arrows, selected buttons and explanation together using local state only', () => {
  const request = vi.fn();
  vi.stubGlobal('fetch', request);
  act(() => { renderer = create(<DirectionExample />); });
  const root = renderer!.root;
  const select = (id: string) => {
    const button = root.findAllByType('button').find(item => item.props['data-choice'] === id)!;
    act(() => button.props.onClick());
    expect(button.props.type).toBe('button');
    expect(button.props['aria-pressed']).toBe(true);
    expect(root.findAllByType('button').filter(item => item.props['aria-pressed'])).toHaveLength(1);
  };
  const result = () => root.findByProps({ 'data-testid': 'direction-count' }).children.join('');
  expect(root.findByProps({ 'aria-live': 'polite' })).toBeDefined();
  expect(result()).toContain('2 independent directions');
  expect(root.findByProps({ 'data-testid': 'direction-b' }).props).toMatchObject({ x2: 62, y2: 80 });
  select('right');
  expect(result()).toContain('1 independent direction');
  expect(root.findByProps({ 'data-testid': 'direction-b' }).props).toMatchObject({ x2: 134, y2: 116 });
  select('fraction');
  expect(root.findByProps({ 'data-testid': 'direction-b' }).props).toMatchObject({ x2: 116, y2: 116 });
  expect(root.findByType('desc').children.join('')).toContain('B = (1.5, 0)');
  expect(JSON.stringify(renderer!.toJSON())).toContain('Neither is an integer multiple');
  select('zero');
  expect(result()).toContain('1 independent direction');
  expect(root.findAllByProps({ 'data-testid': 'direction-b' })).toHaveLength(0);
  expect(root.findByProps({ 'data-testid': 'direction-b-zero' }).props).toMatchObject({ cx: 62, cy: 116 });
  expect(JSON.stringify(renderer!.toJSON())).toContain('Finding some independent directions does not establish');
  select('up');
  expect(result()).toContain('2 independent directions');
  expect(request).not.toHaveBeenCalled();
});
