import { describe, expect, it } from 'vitest';
import { GrowthLedger, stepIdentity, type GrowthScene } from '../map/growth';
function scene(steps: string[], extra = false): GrowthScene {
  return { cards: [{ id: 'a' }, ...(extra ? [{ id: 'b' }] : [])], layouts: {
    a: { steps: steps.map((id) => ({ id })), links: steps.slice(1).map((target, i) => ({ id: `${steps[i]}-${target}`, target })) },
    ...(extra ? { b: { steps: [{ id: 'b-brief' }], links: [] } } : {}),
  }, links: extra ? [{ id: 'a-b' }] : [] };
}
describe('live map growth', () => {
  it('does not animate history, rewording, or revisiting the same evidence', () => {
    const ledger = new GrowthLedger();
    expect(ledger.observe(scene(['brief', 'run']))).toBeNull();
    expect(ledger.observe(scene(['brief', 'run']))).toBeNull();
    ledger.observe(scene(['brief']));
    expect(ledger.observe(scene(['brief', 'run']))).toBeNull();
  });
  it('grows only appended steps and draws incoming relationships first', () => {
    const ledger = new GrowthLedger(); ledger.observe(scene(['brief']));
    const added = ledger.observe(scene(['brief', 'execute', 'review']))!;
    expect(added.cards).toEqual({});
    expect(Object.keys(added.steps)).toEqual([stepIdentity('a', 'execute'), stepIdentity('a', 'review')]);
    expect(added.links[stepIdentity('a', 'brief-execute')]).toBeLessThan(added.steps[stepIdentity('a', 'execute')]);
    expect(added.steps[stepIdentity('a', 'execute')]).toBeLessThan(added.steps[stepIdentity('a', 'review')]);
  });
  it('silently pages old history, then animates the next real task', () => {
    const ledger = new GrowthLedger(); ledger.observe(scene(['brief']), true);
    expect(ledger.observe(scene(['brief', 'run']), true)).toBeNull();
    expect(ledger.observe(scene(['brief', 'run', 'historic-result']))).toBeNull();
    const added = ledger.observe(scene(['brief', 'run', 'historic-result'], true))!;
    expect(Object.keys(added.cards)).toEqual(['b']);
    expect(Object.keys(added.links)).toEqual(['a-b']);
    expect(ledger.observe(scene(['brief', 'run', 'historic-result'], true))).toBeNull();
  });
  it('grows the first submitted task after an initially empty map', () => {
    const ledger = new GrowthLedger(); ledger.observe({ cards: [], layouts: {}, links: [] });
    expect(ledger.observe(scene(['brief']))!.cards).toHaveProperty('a');
  });
});
