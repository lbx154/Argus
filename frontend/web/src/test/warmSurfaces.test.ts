import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = readFileSync(new URL('../index.css', import.meta.url), 'utf8');
function declarations(selector: RegExp): Record<string, string> {
  const block = css.match(selector);
  if (!block) throw new Error('Missing palette declaration');
  return Object.fromEntries([...block[1].matchAll(/--([\w-]+):\s*([^;]+);/g)]
    .map(match => [match[1], match[2].trim()]));
}
const light = declarations(/:root\s*\{([^}]+)\}/);
const dark = { ...light, ...declarations(/:root\[data-theme='dark'\]\s*\{([^}]+)\}/) };
function rgb(palette: Record<string, string>, name: string, seen = new Set<string>()): number[] {
  if (seen.has(name)) throw new Error(`Palette alias cycle at ${name}`);
  seen.add(name);
  const value = palette[name];
  const alias = value?.match(/^var\(--([\w-]+)\)$/);
  if (alias) return rgb(palette, alias[1], seen);
  const channels = value?.split(/\s+/).map(Number);
  if (!channels || channels.length !== 3 || channels.some(channel => !Number.isFinite(channel))) {
    throw new Error(`Invalid RGB surface ${name}`);
  }
  return channels;
}

const primary = ['bg', 'surface-0', 'surface-1', 'surface', 'panel', 'glass', 'chrome-surface', 'conversation-argus'];
describe('the complete cockpit uses shared warm application surfaces', () => {
  it.each(primary)('%s uses the same pale yellow as the desktop shell', name => {
    expect(rgb(light, name)).toEqual([247, 243, 232]);
  });
  it.each(['surface-2', 'surface-3', 'panel-raised', 'glass-raised', 'conversation-user'])('%s stays warm rather than white/cold gray', name => {
    const [red, green, blue] = rgb(light, name);
    expect(red).toBeGreaterThanOrEqual(green);
    expect(green).toBeGreaterThan(blue);
    expect([red, green, blue]).not.toEqual([255, 255, 255]);
  });
  it.each(primary)('dark %s stays dark instead of inheriting a yellow surface', name => {
    expect(rgb(dark, name).every(channel => channel < 64)).toBe(true);
  });
  it('preserves white graphic/foreground semantics rather than blanket-replacing white', () => {
    expect(rgb(light, 'brand-eye')).toEqual([255, 255, 255]);
    expect(rgb(light, 'brand-highlight')).toEqual([255, 255, 255]);
    expect(rgb(light, 'on-accent')).toEqual([255, 255, 255]);
  });
  it('map canvas and minimap do not keep an independent cold palette', () => {
    const map = readFileSync(new URL('../map/map.css', import.meta.url), 'utf8');
    expect(map).toContain('--map-canvas: rgb(var(--bg));');
    expect(map).toContain('--map-card-surface: rgb(var(--panel-raised));');
    expect(map).not.toContain('--map-canvas: #f5f7fa;');
    expect(map).not.toContain('--map-card-surface: #1d2734;');
  });
  it('maps retain the shared surface around original image content', () => {
    const map = readFileSync(new URL('../map/design.css', import.meta.url), 'utf8');
    const figure = map.match(/\.argus-map \.macro-summary \.map-card-figure\s*\{([^}]+)\}/);
    expect(figure?.[1]).toContain('background: rgb(var(--surface));');
    expect(figure?.[1]).not.toContain('background: #fff;');
  });
});
