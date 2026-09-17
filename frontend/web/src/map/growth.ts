export interface GrowthScene {
  cards: Array<{ id: string }>;
  layouts: Record<string, { steps: Array<{ id: string }>; links: Array<{ id: string; target: string }> }>;
  links: Array<{ id: string }>;
}
export interface GrowthBatch {
  revision: number;
  cards: Record<string, number>;
  steps: Record<string, number>;
  links: Record<string, number>;
}
export const emptyGrowth = (): GrowthBatch => ({ revision: 0, cards: {}, steps: {}, links: {} });
export const stepIdentity = (card: string, step: string) => `${card}\u0000${step}`;

/** Baseline history silently. Only evidence appended while this map is open grows. */
export class GrowthLedger {
  private initialized = false;
  private seen = new Set<string>();
  private revision = 0;
  private loadingHistory = false;
  observe(scene: GrowthScene, historical = false): GrowthBatch | null {
    const batch = emptyGrowth();
    let cardIndex = 0;
    for (const card of scene.cards) {
      if (!this.seen.has(`card:${card.id}`)) batch.cards[card.id] = Math.min(cardIndex++ * 100, 400);
      this.seen.add(`card:${card.id}`);
      const layout = scene.layouts[card.id];
      let stepIndex = 0;
      for (const step of layout.steps) {
        const key = stepIdentity(card.id, step.id);
        if (!this.seen.has(`step:${key}`)) batch.steps[key] = 160 + Math.min(stepIndex++ * 120, 720);
        this.seen.add(`step:${key}`);
      }
      for (const link of layout.links) {
        const key = stepIdentity(card.id, link.id);
        if (!this.seen.has(`link:${key}`)) batch.links[key] = Math.max(0, (batch.steps[stepIdentity(card.id, link.target)] ?? 160) - 160);
        this.seen.add(`link:${key}`);
      }
    }
    for (const link of scene.links) {
      if (!this.seen.has(`outer:${link.id}`)) batch.links[link.id] = 0;
      this.seen.add(`outer:${link.id}`);
    }
    const silent = !this.initialized || historical || this.loadingHistory;
    this.loadingHistory = historical;
    this.initialized = true;
    if (silent || ![batch.cards, batch.steps, batch.links].some((items) => Object.keys(items).length)) return null;
    return { ...batch, revision: ++this.revision };
  }
}
